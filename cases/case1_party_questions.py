"""
cases/case1_party_questions.py — Case 1 Handler 实现

案件类型：当事人谈判准备性问题生成（甲乙方各自独立 conversation）
result_key：甲方 / 乙方 / 通用（取决于上传材料）
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncGenerator

import httpx

from cases.base import CaseHandler, SseEvent
from model.config import (
    CREATE_URL,
    agent_b_auth_headers,
    pick_conversation_id,
)

logger = logging.getLogger(__name__)


class Case1Handler(CaseHandler):
    """Case 1：当事人准备性问题生成。

    - 甲乙方各自独立 conversation，串行处理
    - 每方输出一份 result（result_key = "甲方" / "乙方" / "通用"）
    - 报告下载：甲方问题清单 + 乙方问题清单（视上传材料而定）
    """

    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        """串行处理甲方→乙方，逐方 yield 进度与结果。"""
        # 延迟导入避免循环依赖（api.pdf_chat 通过 _get_handler 延迟导入 cases）
        from api.pdf_chat import (  # noqa: PLC0415
            _ask_agent_b_with_docs,
            _build_doc_item,
            session_metadata,
        )

        docs = session_store.get(session_id)
        if not docs:
            raise ValueError(f"会话 {session_id} 不存在或已过期，请重新上传文件")

        # ── 按 party 分组 ──────────────────────────────────────────────
        party_a_docs = [d for d in docs if d.party == "甲方"]
        party_b_docs = [d for d in docs if d.party == "乙方"]
        common_docs  = [d for d in docs if d.party == "通用"]

        has_a = bool(party_a_docs)
        has_b = bool(party_b_docs)

        if has_a and has_b:
            parties_to_process = [
                (
                    "甲方",
                    party_a_docs + common_docs,
                    "以下是甲方（Party A）提交的全部材料分析结果，"
                    "请基于这些材料进行综合分析：识别甲方的核心主张、"
                    "证据优势与薄弱点，并生成针对甲方的谈判准备问题清单。",
                ),
                (
                    "乙方",
                    party_b_docs + common_docs,
                    "以下是乙方（Party B）提交的全部材料分析结果，"
                    "请基于这些材料进行综合分析：识别乙方的核心主张、"
                    "证据优势与薄弱点，并生成针对乙方的谈判准备问题清单。",
                ),
            ]
        elif has_a:
            parties_to_process = [
                (
                    "甲方",
                    party_a_docs + common_docs,
                    "以下是甲方提交的全部材料分析结果，请进行综合分析，"
                    "识别争议焦点并生成针对性提问清单。",
                )
            ]
        elif has_b:
            parties_to_process = [
                (
                    "乙方",
                    party_b_docs + common_docs,
                    "以下是乙方提交的全部材料分析结果，请进行综合分析，"
                    "识别争议焦点并生成针对性提问清单。",
                )
            ]
        else:
            # 全为通用材料 — 退回单 conversation
            parties_to_process = [
                (
                    "通用",
                    common_docs,
                    f"以下是全部 {len(common_docs)} 份材料的分析结果，"
                    "请进行综合分析，识别争议焦点并生成针对性提问清单。",
                )
            ]

        # 只读 case_type，外层结构懒初始化（见下方 setdefault）
        # 全失败时不写入 session_results_store，与原路由 "if results_by_party" 行为一致
        case_type = session_metadata.get(session_id, {}).get("case_type", "case1")

        # ── 串行处理每一方 ────────────────────────────────────────────
        for party_name, party_docs, party_prompt in parties_to_process:
            yield SseEvent(
                type="progress",
                data={
                    "stage": "analyzing",
                    "result_key": party_name,
                    "message": f"正在为{party_name}创建分析会话（{len(party_docs)} 份材料）...",
                },
            )

            # 创建 Agent B conversation
            try:
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as init_client:
                    bcr = await init_client.post(
                        CREATE_URL,
                        headers=agent_b_auth_headers(),
                        json={"user_id": "session_analyze_user"},
                    )
                    bcr.raise_for_status()
                party_conv_id = pick_conversation_id(bcr.json())
                if not party_conv_id:
                    yield SseEvent(
                        type="error",
                        data={
                            "result_key": party_name,
                            "message": f"无法为{party_name}创建 Agent B 对话：{bcr.json()}",
                        },
                    )
                    continue
                logger.info(
                    "Session %s %s：Agent B conversation_id=%s",
                    session_id, party_name, party_conv_id,
                )
            except Exception as exc:
                yield SseEvent(
                    type="error",
                    data={
                        "result_key": party_name,
                        "message": f"创建{party_name} Agent B 对话失败：{exc}",
                    },
                )
                continue

            # 构造每份材料的 document item
            # party-specific 材料保留自身标签，通用材料不标注方向
            doc_items = [
                _build_doc_item(
                    d.filename,
                    d.summaries,
                    d.merged_fields,
                    d.party if d.party != "通用" else "",
                )
                for d in party_docs
            ]
            total_b64 = sum(len(it["base64_content"]) for it in doc_items)
            logger.info(
                "Session %s %s：构造 %d 份 document item，base64 合计 %d 字节",
                session_id, party_name, len(doc_items), total_b64,
            )

            # 一次请求发送所有材料 + 提示词
            try:
                async with httpx.AsyncClient(timeout=240.0, trust_env=False) as b_client:
                    analysis_result = await _ask_agent_b_with_docs(
                        b_client, party_conv_id, party_prompt, doc_items
                    )
                logger.info(
                    "Session %s %s：综合分析完成，回复长度 %d 字符",
                    session_id, party_name, len(analysis_result),
                )
            except Exception as exc:
                logger.error("Session %s %s：综合分析失败：%s", session_id, party_name, exc)
                yield SseEvent(
                    type="error",
                    data={
                        "result_key": party_name,
                        "message": f"综合分析失败：{exc}",
                    },
                )
                continue

            # 写入 session_results_store（必须在 yield result 之前完成）
            # setdefault 懒初始化：首方成功时创建外层结构，后续方直接复用
            session_results_store.setdefault(session_id, {
                "case_type": case_type,
                "created_at": time.time(),
                "results": {},
            })["results"][party_name] = {
                "analysis_text": analysis_result,
                "conversation_id": party_conv_id,
            }

            yield SseEvent(
                type="result",
                data={
                    "result_key": party_name,
                    "analysis_text": analysis_result,
                    "conversation_id": party_conv_id,
                },
            )

    async def generate_report(
        self,
        session_id: str,
        result_key: str,
        output_format: str,
        session_results_store: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        """生成报告，返回 (file_bytes, filename, media_type)。

        Args:
            session_id:            会话 ID。
            result_key:            "甲方" / "乙方" / "通用"。
            output_format:         "pdf" 或 "docx"（由路由层规范化后传入）。
            session_results_store: 全局结果存储。

        Returns:
            (file_bytes, filename, media_type)。
            若 output_format="pdf" 但 PDF 转换失败，report_generator 内部降级返回
            .docx，filename 会以 ".docx" 结尾，media_type 随之调整。

        Raises:
            ValueError: session_id 不存在，或 result_key 对应方分析结果未落盘
                        （S5 BREAKING：分析失败的方不写入 store）。
            RuntimeError: report_generator 内部异常（路由层转 HTTP 500）。
                          注意：路由层依赖 ValueError→400 / 其他异常→500，故此处
                          将 report_generator 的任何异常包装成 RuntimeError，
                          避免其内部 ValueError 被误判成业务校验失败（400）。
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            raise ValueError(f"会话 {session_id} 不存在或已过期")

        party_results = store_entry["results"]
        # 防御性校验：路由层通常已过滤无效 result_key，但 handler 不依赖调用方
        if result_key not in party_results:
            raise ValueError(
                f"{result_key} 的分析结果不存在，可能分析失败或尚未分析该方材料"
            )

        analysis_text = party_results[result_key]["analysis_text"]

        _PARTY_FILENAME_MAP = {
            "甲方": "10 Questions for Party A",
            "乙方": "10 Questions for Party B",
            "通用": "10 Questions",
        }
        base_name = _PARTY_FILENAME_MAP.get(result_key, "ebram_analysis")

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
        基于已完成的综合分析进行后续文字追问。

        conversation_id 为 None 时：创建独立追问 conversation，注入所有方 analysis_text
        作为上下文，再发送用户问题。
        conversation_id 有值时：直接复用该 conversation 发送追问。

        Returns:
            {"reply": str, "conversation_id": str}

        Raises:
            ValueError: session_id 不存在或分析尚未完成。
            RuntimeError: Agent B 创建/调用失败（路由层转 HTTP 500）。
        """
        # 延迟导入避免循环依赖
        from api.pdf_chat import _ask_agent_b  # noqa: PLC0415

        store_entry = session_results_store.get(session_id)
        if not store_entry:
            raise ValueError("会话不存在或尚未完成综合分析，请先进行综合分析")

        try:
            conv_id: str

            if conversation_id:
                conv_id = conversation_id
            else:
                # 设计说明：追问 conversation 独立于分析 conversation（避免重复发送 base64
                # document，节省 token）。通过文本方式注入双方 analysis_text 作为上下文，
                # Agent B 拥有全局视角，可跨方回答对比类问题。
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                    cr = await client.post(
                        CREATE_URL,
                        headers=agent_b_auth_headers(),
                        json={"user_id": "session_chat_user"},
                    )
                    cr.raise_for_status()
                conv_id = pick_conversation_id(cr.json())
                if not conv_id:
                    raise RuntimeError(f"无法创建 Agent B 对话：{cr.json()}")

                context_parts = [
                    f"## {p}综合分析结果\n\n{r['analysis_text']}"
                    for p, r in store_entry["results"].items()
                ]
                context_text = (
                    "以下是本案件的综合分析结果，请在回答后续问题时以此为背景：\n\n"
                    + "\n\n---\n\n".join(context_parts)
                )
                async with httpx.AsyncClient(timeout=180.0, trust_env=False) as ctx_client:
                    await _ask_agent_b(ctx_client, conv_id, context_text)
                logger.info(
                    "Session %s：已创建追问 conversation 并注入上下文，conv_id=%s",
                    session_id, conv_id,
                )

            # 发送用户追问
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as chat_client:
                reply = await _ask_agent_b(chat_client, conv_id, text)

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
        """返回当前 session 已成功分析、可下载报告的 result_key 列表。

        S5 BREAKING 的连锁：失败方不写入 session_results_store["results"]，
        所以直接列 keys 即可，不需要硬编码"甲方/乙方"。

        未来启用路由时建议：
          GET /session/downloadable_keys?session_id=<sid>
          → {"keys": [...]}
        当前前端通过 SSE result 事件本地推断可下载方，未调用此端点；
        此方法保留以完整实现 base.py CaseHandler 契约，供 Case 2/3 复用。

        Returns:
            list[str]: 例如 ["甲方"] 或 ["甲方", "乙方"] 或 []（全失败/未分析）
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            return []
        return list(store_entry["results"].keys())
