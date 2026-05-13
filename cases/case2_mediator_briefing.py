"""
cases/case2_mediator_briefing.py — Case 2 Handler 实现

案件类型：调解员简报生成（Mediator Briefing）
输入：甲方×3 + 乙方×3 + 共用×1，共 7 份 PDF

处理流程：
  Phase 1 — OCR（复用 Agent A 逐页提取）：7 份 PDF 依次抽取文本，存入 session_store
  Phase 2 — 合并 + Agent C 综合分析：
            将甲方材料、乙方材料、共用材料分别合并为 3 份 Markdown，
            一次性发送给 Agent C 生成调解员简报，
            结果以 result_key="调解员简报" 写入 session_results_store

result_key：调解员简报

SSE 事件 data schema（analyze 产出）：
  progress  {"stage": "ocr"|"analyzing", "message": str, "filename": str (ocr)}
  result    {"result_key": "调解员简报", "analysis_text": str, "conversation_id": str}
  error     {"message": str, "filename": str (ocr error) | ""}
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncGenerator

import httpx

from cases.base import CaseHandler, SseEvent
from model.config import (
    AGENT_C_API_KEY,
    CREATE_URL,
    agent_c_auth_headers,
    pick_conversation_id,
)

logger = logging.getLogger(__name__)

# 调解员简报的唯一下载键
_BRIEFING_KEY = "调解员简报"

# Case 2 固定文件命名前缀约定（前端上传时应按此命名）
# UC2_0 = 共用谈判结果材料
# UC2_1 = 甲乙方调解意向申请表
# UC2_2 = 甲乙方邮件/信函
# UC2_3 = 甲乙方证据提交
_PARTY_A_PREFIXES = ("UC2_1_mediation_intake_form (Party A)",
                     "UC2_2_email_from_Party_A",
                     "UC2_3_exihibit_submission (Party A)")
_PARTY_B_PREFIXES = ("UC2_1_mediation_intake_form (Party B)",
                     "UC2_2_letter_from_Party_B",
                     "UC2_3_exihibit_submission (Party B)")
_COMMON_PREFIXES  = ("UC2_0",)


def _classify_filename(filename: str) -> str:
    """
    根据文件名前缀判断所属方。
    返回 "甲方" / "乙方" / "通用"；无法识别时返回 "通用"。
    """
    for prefix in _PARTY_A_PREFIXES:
        if filename.startswith(prefix):
            return "甲方"
    for prefix in _PARTY_B_PREFIXES:
        if filename.startswith(prefix):
            return "乙方"
    return "通用"


class Case2MediatorBriefingHandler(CaseHandler):
    """Case 2：调解员简报生成。

    - 7 份 PDF（甲×3、乙×3、共×1）通过文件名前缀自动分组
    - Agent C 一次性接收三方合并材料，生成完整调解员简报
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
        综合分析 7 份材料并生成调解员简报。

        Phase 1：逐文件 OCR 提取（复用 session_store 中已有逐页摘要）
        Phase 2：合并三方材料 → Agent C → 写入 session_results_store

        Raises:
            ValueError: AGENT_C_API_KEY 未配置、session_id 不存在或文件数量不足。
        """
        # ── 前置校验 ──────────────────────────────────────────────────────
        if not AGENT_C_API_KEY:
            raise ValueError(
                "AGENT_C_API_KEY 未配置，Case 2 功能不可用，请在 .env 中设置该环境变量"
            )

        docs = session_store.get(session_id)
        if not docs:
            raise ValueError(f"会话 {session_id} 不存在或已过期，请重新上传文件")

        # 延迟导入避免循环依赖
        from api.pdf_chat import _build_doc_item  # noqa: PLC0415

        # ── Phase 1：按文件名前缀自动分组 ─────────────────────────────────
        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": f"正在整理 {len(docs)} 份材料（甲方、乙方、共用三方分组）...",
            },
        )

        party_a_docs = []
        party_b_docs = []
        common_docs  = []

        for doc in docs:
            party = _classify_filename(doc.filename)
            if party == "甲方":
                party_a_docs.append(doc)
            elif party == "乙方":
                party_b_docs.append(doc)
            else:
                common_docs.append(doc)

        logger.info(
            "Session %s Case2：甲方 %d 份，乙方 %d 份，共用 %d 份",
            session_id, len(party_a_docs), len(party_b_docs), len(common_docs),
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

        # 构造三方 document items
        doc_items: list[dict] = []

        def _build_party_items(party_docs: list, party_label: str) -> list[dict]:
            return [
                _build_doc_item(
                    d.filename,
                    d.summaries,
                    d.merged_fields,
                    party_label,
                )
                for d in party_docs
            ]

        doc_items.extend(_build_party_items(party_a_docs, "甲方"))
        doc_items.extend(_build_party_items(party_b_docs, "乙方"))
        doc_items.extend(_build_party_items(common_docs, ""))  # 共用材料不标注方向

        total_b64 = sum(len(it["base64_content"]) for it in doc_items)
        logger.info(
            "Session %s Case2：构造 %d 份 document item，base64 合计 %d 字节",
            session_id, len(doc_items), total_b64,
        )

        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": (
                    f"正在向 Agent C 发送全部 {len(doc_items)} 份材料"
                    f"（甲方 {len(party_a_docs)} 份、乙方 {len(party_b_docs)} 份、"
                    f"共用 {len(common_docs)} 份），请稍候..."
                ),
            },
        )

        # 调用 Agent C 生成调解员简报
        try:
            from api.pdf_chat import _ask_agent_b_with_docs  # noqa: PLC0415

            briefing_prompt = (
                "以下是本次调解案件三方提交的全部材料分析结果：\n"
                "  · 甲方材料：当事人 A 的调解意向申请表、相关邮件/信函及证据提交\n"
                "  · 乙方材料：当事人 B 的调解意向申请表、相关信函及证据提交\n"
                "  · 共用材料：此前谈判尝试的结果记录\n\n"
                "请基于上述全部材料，为调解员生成一份完整的结构化简报，内容应包括：\n"
                "  1. 案件概要（争议背景、涉案金额/事项）\n"
                "  2. 甲方立场与主要诉求\n"
                "  3. 乙方立场与主要诉求\n"
                "  4. 双方主要争议焦点对比\n"
                "  5. 关键证据与文件摘要\n"
                "  6. 此前谈判尝试及结果\n"
                "  7. 调解建议与潜在方向\n"
            )

            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as c_client:
                # 复用 _ask_agent_b_with_docs，传入 Agent C conversation_id 和 headers
                # 注意：此函数内部使用 agent_b_auth_headers，需在 Agent C headers 下重新请求
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
    与 api.pdf_chat._ask_agent_b 对称。
    """
    from model.config import MESSAGE_URL  # noqa: PLC0415

    payload = {
        "conversation_id": conversation_id,
        "user_id": "case2_user",
        "query": text,
    }
    resp = await client.post(MESSAGE_URL, headers=agent_c_auth_headers(), json=payload)
    resp.raise_for_status()
    data = resp.json()

    # 兼容 GPTBots 多层响应结构
    if isinstance(data, dict):
        for key in ("answer", "text", "message", "reply"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val
        # data.output[0].content.text
        output = data.get("output", [])
        if output and isinstance(output, list):
            first = output[0]
            if isinstance(first, dict):
                content = first.get("content", {})
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    return content["text"]
                if isinstance(content, str):
                    return content

    return str(data)


async def _ask_agent_c_with_docs(
    client: httpx.AsyncClient,
    conversation_id: str,
    prompt: str,
    doc_items: list[dict],
) -> str:
    """
    向 Agent C 发送带文档的消息，返回回复文本。
    与 api.pdf_chat._ask_agent_b_with_docs 对称。
    """
    from model.config import MESSAGE_URL  # noqa: PLC0415

    payload = {
        "conversation_id": conversation_id,
        "user_id": "case2_user",
        "query": prompt,
        "documents": doc_items,
    }
    resp = await client.post(MESSAGE_URL, headers=agent_c_auth_headers(), json=payload)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict):
        for key in ("answer", "text", "message", "reply"):
            val = data.get(key)
            if val and isinstance(val, str):
                return val
        output = data.get("output", [])
        if output and isinstance(output, list):
            first = output[0]
            if isinstance(first, dict):
                content = first.get("content", {})
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    return content["text"]
                if isinstance(content, str):
                    return content

    return str(data)
