"""
cases/case2_mediator_briefing.py — Case 2 Handler 实现

案件类型：调解员简报生成（Mediator Briefing）
输入：任意数量 PDF，前端上传时由用户指定 party（甲方 / 乙方 / 通用）

处理流程：
  Phase 1 — OCR（复用 Agent A 逐页提取）：所有 PDF 依次抽取文本，存入 session_store
  Phase 2 — 按 party 分组合并 → Agent C 综合分析：
            甲方 / 乙方 / 通用 各组材料分别合并为 1 份 Markdown（最多 3 份），
            一次性发送给 Agent C 生成调解员简报，
            结果以 result_key="调解员简报" 写入 session_results_store

result_key：调解员简报

SSE 事件 data schema（analyze 产出）：
  progress  {"stage": "ocr"|"analyzing", "message": str, "filename": str (ocr)}
  result    {"result_key": "调解员简报", "analysis_text": str, "conversation_id": str}
  error     {"message": str, "filename": str (ocr error) | ""}
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx

from cases.base import CaseHandler, SseEvent
from model.config import (
    AGENT_C_API_KEY,
    CREATE_URL,
    MESSAGE_URL,
    agent_c_auth_headers,
    pick_conversation_id,
)
from model.utils import extract_gptbots_reply

logger = logging.getLogger(__name__)

# 调解员简报的唯一下载键
_BRIEFING_KEY = "调解员简报"


class Case2MediatorBriefingHandler(CaseHandler):
    """Case 2：调解员简报生成。

    - 任意数量 PDF，由前端 UI 指定 party（甲方 / 乙方 / 通用）
    - 按 party 分组合并后，Agent C 一次性接收最多 3 份合并材料，生成完整调解员简报
    - 仅产出一个 result_key="调解员简报"
    - 报告下载：单份调解员简报（Word / PDF）
    """

    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        """
        按 party 分组合并材料并调用 Agent C 生成调解员简报。

        Phase 1：读取 session_store 中已有的逐页摘要（OCR + Agent A 已完成）
        Phase 2：按 party 分组 → 合并为最多 3 份 md → Agent C → 写入 session_results_store

        Raises:
            ValueError: AGENT_C_API_KEY 未配置或 session_id 不存在。
        """
        # ── 前置校验 ──────────────────────────────────────────────────────
        if not AGENT_C_API_KEY:
            raise ValueError(
                "AGENT_C_API_KEY 未配置，Case 2 功能不可用，请在 .env 中设置该环境变量"
            )

        docs = session_store.get(session_id)
        if not docs:
            raise ValueError(f"会话 {session_id} 不存在或已过期，请重新上传文件")

        # ── Phase 1：按 doc.party 统计（用于日志 + 进度提示）────────────────
        party_a_docs = [d for d in docs if d.party == "甲方"]
        party_b_docs = [d for d in docs if d.party == "乙方"]
        common_docs  = [d for d in docs if d.party == "通用"]

        logger.info(
            "Session %s Case2：甲方 %d 份，乙方 %d 份，共用 %d 份，合计 %d 份",
            session_id, len(party_a_docs), len(party_b_docs), len(common_docs), len(docs),
        )

        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": (
                    f"正在整理 {len(docs)} 份材料"
                    f"（甲方 {len(party_a_docs)} 份、乙方 {len(party_b_docs)} 份、"
                    f"共用 {len(common_docs)} 份）..."
                ),
            },
        )

        # ── Phase 2：构造 doc_items 并调用 Agent C ────────────────────────
        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": "正在为调解员简报创建 Agent C 分析会话...",
            },
        )

        # 创建 Agent C conversation
        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as init_client:
                cr = await init_client.post(
                    CREATE_URL,
                    headers=agent_c_auth_headers(),
                    json={"user_id": "case2_analyze_user"},
                )
                cr.raise_for_status()
            conv_id = pick_conversation_id(cr.json())
            if not conv_id:
                raise RuntimeError(f"无法创建 Agent C 对话：{cr.json()}")
            logger.info("Session %s Case2：Agent C conversation_id=%s", session_id, conv_id)
        except Exception as exc:
            raise ValueError(f"创建 Agent C 对话失败：{exc}") from exc

        # 构造 document items：按 party 分组合并，最多 3 个 md（CLAIMANT/RESPONDENT/COMMON/NEUTRAL）
        groups: dict[str, list] = {}
        for d in docs:
            label = _PARTY_LABEL_MAP.get(d.party, "COMMON/NEUTRAL")
            groups.setdefault(label, []).append(d)

        doc_items: list[dict] = []
        for label in ("CLAIMANT", "RESPONDENT", "COMMON/NEUTRAL"):
            if label in groups:
                doc_items.append(_build_c2_merged_doc_item(label, groups[label]))

        logger.info(
            "Session %s Case2：分组结果 CLAIMANT=%d RESPONDENT=%d COMMON/NEUTRAL=%d，"
            "合并后 doc_items=%d 个，文件名及字节数：%s",
            session_id,
            len(groups.get("CLAIMANT", [])),
            len(groups.get("RESPONDENT", [])),
            len(groups.get("COMMON/NEUTRAL", [])),
            len(doc_items),
            [
                {"name": it["name"], "bytes": len(base64.b64decode(it["base64_content"]))}
                for it in doc_items
            ],
        )

        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": (
                    f"正在向 Agent C 发送 {len(doc_items)} 份合并材料"
                    f"（甲方 {len(party_a_docs)} 份、乙方 {len(party_b_docs)} 份、"
                    f"共用 {len(common_docs)} 份），请稍候..."
                ),
            },
        )

        # 调用 Agent C 生成调解员简报
        try:
            briefing_prompt = (
                "Please prepare the mediator briefing based on the attached materials. "
                "The bundle contains documents from both parties (Claimant and Respondent) "
                "and one common/neutral document. "
                "Each attachment's first line indicates its party label. "
                "Follow the output structure and principles defined in your role configuration."
            )

            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as c_client:
                briefing_text = await _ask_agent_c_with_docs(
                    c_client, conv_id, briefing_prompt, doc_items
                )

            logger.info(
                "Session %s Case2：调解员简报生成完成，回复长度 %d 字符",
                session_id, len(briefing_text),
            )
        except Exception as exc:
            logger.error("Session %s Case2：Agent C 调用失败：%s", session_id, exc)
            yield SseEvent(
                type="error",
                data={
                    "message": f"调解员简报生成失败：{exc}",
                },
            )
            return

        # 写入 session_results_store（必须在 yield result 之前完成）
        session_results_store[session_id] = {
            "case_type": "case2",
            "created_at": time.time(),
            "results": {
                _BRIEFING_KEY: {
                    "analysis_text": briefing_text,
                    "conversation_id": conv_id,
                },
            },
        }

        yield SseEvent(
            type="result",
            data={
                "result_key": _BRIEFING_KEY,
                "analysis_text": briefing_text,
                "conversation_id": conv_id,
            },
        )

    async def generate_report(
        self,
        session_id: str,
        result_key: str,
        output_format: str,
        session_results_store: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        """
        生成调解员简报报告文件。

        Args:
            result_key:    必须为 "调解员简报"。
            output_format: "pdf" 或 "docx"。

        Returns:
            (file_bytes, filename, media_type)

        Raises:
            ValueError: session_id 不存在或简报未生成。
            RuntimeError: report_generator 内部异常。
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            raise ValueError(f"会话 {session_id} 不存在或已过期")

        results = store_entry.get("results", {})
        if result_key not in results:
            raise ValueError(
                f"{result_key} 的分析结果不存在，可能分析尚未完成或中途失败"
            )

        analysis_text = results[result_key]["analysis_text"]
        base_name = "Mediator Briefing"

        try:
            from model.report_generator import generate_report as _gen_report  # noqa: PLC0415
            file_bytes, filename = _gen_report(
                analysis_text=analysis_text,
                output_format=output_format,
                base_name=base_name,
            )
        except Exception as exc:
            logger.exception(
                "报告生成失败：session_id=%s, result_key=%s, output_format=%s",
                session_id, result_key, output_format,
            )
            raise RuntimeError(f"报告生成器异常：{exc}") from exc

        if output_format == "pdf" and filename.endswith(".pdf"):
            media_type = "application/pdf"
        else:
            media_type = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )

        return file_bytes, filename, media_type

    async def followup_chat(
        self,
        session_id: str,
        text: str,
        conversation_id: str | None,
        session_results_store: dict[str, Any],
    ) -> dict[str, Any]:
        """
        基于调解员简报进行后续文字追问。

        conversation_id 为 None 时：
            创建独立追问 conversation，注入简报全文作为上下文，再发送用户问题。
        conversation_id 有值时：
            直接复用已有 conversation，发送追问（无需重新注入上下文）。

        Returns:
            {"conversation_id": str, "reply": str}

        Raises:
            ValueError: session_id 不存在或简报尚未生成。
            RuntimeError: Agent C 创建/调用失败。
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            raise ValueError("会话不存在或尚未完成调解员简报分析，请先进行综合分析")

        results = store_entry.get("results", {})
        if _BRIEFING_KEY not in results:
            raise ValueError("调解员简报尚未生成，请先完成综合分析")

        try:
            conv_id: str

            if conversation_id:
                conv_id = conversation_id
            else:
                # 创建追问 conversation，注入简报文本作为上下文
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                    cr = await client.post(
                        CREATE_URL,
                        headers=agent_c_auth_headers(),
                        json={"user_id": "case2_chat_user"},
                    )
                    cr.raise_for_status()
                conv_id = pick_conversation_id(cr.json())
                if not conv_id:
                    raise RuntimeError(f"无法创建 Agent C 追问对话：{cr.json()}")

                briefing_text = results[_BRIEFING_KEY]["analysis_text"]
                context_text = (
                    "以下是本次调解案件的调解员简报，请在回答后续问题时以此为背景：\n\n"
                    + briefing_text
                )
                async with httpx.AsyncClient(timeout=180.0, trust_env=False) as ctx_client:
                    await _ask_agent_c(ctx_client, conv_id, context_text)
                logger.info(
                    "Session %s Case2：已创建追问 conversation 并注入简报上下文，conv_id=%s",
                    session_id, conv_id,
                )

            # 发送用户追问
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as chat_client:
                reply = await _ask_agent_c(chat_client, conv_id, text)

        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"追问失败：{exc}") from exc

        return {"reply": reply, "conversation_id": conv_id}

    def get_downloadable_keys(
        self,
        session_id: str,
        session_results_store: dict[str, Any],
    ) -> list[str]:
        """
        返回当前 session 可下载的报告键列表。

        Case 2 固定只有一个 result_key："调解员简报"。
        若简报生成失败（未写入 store），返回空列表。

        Returns:
            ["调解员简报"] 或 []
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            return []
        return [k for k in store_entry.get("results", {}) if k == _BRIEFING_KEY]


# ── Agent C 私有调用函数 ──────────────────────────────────────────────────────
# 与 api.pdf_chat 中的 _ask_agent_b / _ask_agent_b_with_docs 结构对称，
# 但使用 Agent C 专属的鉴权头和端点。
# 定义在文件末尾以避免与 Case1Handler 的 Agent B 函数混淆。

async def _ask_agent_c(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
) -> str:
    """
    向 Agent C 发送纯文本消息，返回回复文本。
    与 api.pdf_chat._ask_agent_b 对称，使用相同的 messages[] payload 格式。
    """
    payload = {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
    }
    resp = await client.post(MESSAGE_URL, headers=agent_c_auth_headers(), json=payload)
    resp.raise_for_status()
    return extract_gptbots_reply(resp.json())


# ── Case 2 文档构造辅助 ──────────────────────────────────────────────────────

# DocResult.party（中文）→ 发送给 Agent C 的英文 party 标签（出现在 md 首行）
_PARTY_LABEL_MAP: dict[str, str] = {
    "甲方": "CLAIMANT",
    "乙方": "RESPONDENT",
    "通用": "COMMON/NEUTRAL",
}

# 英文 party 标签 → 合并后的固定 md 文件名
_PARTY_FILENAME_MAP: dict[str, str] = {
    "CLAIMANT": "claimant_materials.md",
    "RESPONDENT": "respondent_materials.md",
    "COMMON/NEUTRAL": "common_materials.md",
}


def _build_c2_merged_doc_item(party_label: str, docs: list) -> dict:
    """
    将同一方的多份 DocResult 合并为单个 Case 2 GPTBots document item。

    Markdown 格式（首行为英文 party 标签，各文档间用分隔符隔开）：

        [CLAIMANT]

        # 文档 1：UC2_1_mediation_intake_form (Party A).pdf

        [Page 1]
        {PART_A 摘要}

        ---

        ```json
        {PART_B JSON}
        ```

        ===== 文档分隔 =====

        # 文档 2：UC2_2_email_from_Party_A.pdf
        ...

    Args:
        party_label: 英文 party 标签，如 "CLAIMANT"、"RESPONDENT"、"COMMON/NEUTRAL"。
        docs:        该方所有 DocResult，按上传顺序排列。

    Returns:
        GPTBots document item dict，name 为固定文件名（如 claimant_materials.md）。
    """
    sections: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        filled = [(i + 1, s) for i, s in enumerate(doc.summaries) if s.strip()]
        summary_block = "\n\n".join(f"[Page {p}]\n{t}" for p, t in filled)
        fields_json = json.dumps(doc.merged_fields, ensure_ascii=False, indent=2)
        section = (
            f"# 文档 {idx}：{doc.filename}\n\n"
            f"{summary_block}\n\n"
            f"---\n\n"
            f"```json\n{fields_json}\n```"
        )
        sections.append(section)

    sep = "\n\n===== 文档分隔 =====\n\n"
    body = sep.join(sections)
    content = f"[{party_label}]\n\n{body}\n"

    b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    filename = _PARTY_FILENAME_MAP[party_label]
    return {"base64_content": b64, "format": "md", "name": filename}


async def _ask_agent_c_with_docs(
    client: httpx.AsyncClient,
    conversation_id: str,
    prompt: str,
    doc_items: list[dict],
) -> str:
    """
    向 Agent C 发送带文档的消息，返回回复文本。
    与 api.pdf_chat._ask_agent_b_with_docs 对称，使用相同的 messages[] payload 格式。
    """
    content: list[dict] = [{"type": "text", "text": prompt}]
    if doc_items:
        content.append({"type": "document", "document": doc_items})
    payload = {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [{"role": "user", "content": content}],
    }

    logger.info(
        "Case2 Agent C 发送请求：conversation_id=%s，doc_items=%d 份（%s）",
        conversation_id,
        len(doc_items),
        [d["name"] for d in doc_items],
    )

    resp = await client.post(MESSAGE_URL, headers=agent_c_auth_headers(), json=payload)
    resp.raise_for_status()
    return extract_gptbots_reply(resp.json())
