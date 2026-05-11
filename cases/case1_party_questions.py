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
        raise NotImplementedError("S7: Case1Handler.generate_report() not yet implemented")

    async def followup_chat(
        self,
        session_id: str,
        text: str,
        conversation_id: str | None,
        session_results_store: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("S8: Case1Handler.followup_chat() not yet implemented")

    def get_downloadable_keys(
        self,
        session_id: str,
        session_results_store: dict[str, Any],
    ) -> list[str]:
        raise NotImplementedError("S9: Case1Handler.get_downloadable_keys() not yet implemented")
