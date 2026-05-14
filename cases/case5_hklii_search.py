"""
cases/case5_hklii_search.py — Case 5 Handler 实现

案件类型：HKLII 案例检索与摘要（HKLII Case Search）
输入：用户关键词（由 /case5/scrape 爬取后存入 session_store）

处理流程：
  scrape 阶段（由 api/case5_routes.py 完成）：
    用户输入关键词 → Playwright 搜索 HKLII 前 10 条 → 并行抓取正文
    → 结果以 list[ScrapedResult] 存入 session_store[session_id]
    → session_metadata[session_id]["case_type"] = "case5"

  analyze 阶段（本 Handler）：
    读取 session_store 中的 list[ScrapedResult]
    → 构建 hklii_search_results.md（base64 编码）
    → 创建 Agent J conversation
    → 发送 text prompt + md document item
    → 结果以 result_key="HKLII 案例摘要" 写入 session_results_store

result_key：HKLII 案例摘要

SSE 事件 data schema（analyze 产出）：
  progress  {"stage": "analyzing", "message": str}
  result    {"result_key": "HKLII 案例摘要", "analysis_text": str, "conversation_id": str}
  error     {"message": str}
"""
from __future__ import annotations

import base64
import logging
import re
import time
from datetime import date
from typing import Any, AsyncGenerator

import httpx

from cases.base import CaseHandler, SseEvent
from model.config import (
    AGENT_J_API_KEY,
    CREATE_URL,
    MESSAGE_URL,
    agent_j_auth_headers,
    pick_conversation_id,
)
from model.utils import extract_gptbots_reply
from scraper.hklii import ScrapedResult

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────

_SUMMARY_KEY = "HKLII 案例摘要"

# 单条案例内容安全上限（仅兜底保护；GPT-4.1 context = 1M tokens，30MB md 上限）
# 触发场景：网页 selector 抓错，抓到整个页面 HTML 之类的异常情况
SAFETY_LIMIT_CHARS = 100_000

# md 内案例间的分隔线
_CASE_SEPARATOR = "=" * 40


class Case5HkliiSearchHandler(CaseHandler):
    """Case 5：HKLII 案例检索与摘要。

    - 输入：session_store 中已有的 list[ScrapedResult]（由 /case5/scrape 填充）
    - 处理：构建 md → Agent J 生成 10 条案例摘要
    - 输出：result_key="HKLII 案例摘要"，支持 Word/PDF 下载和追问
    """

    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        """
        读取已爬取的 ScrapedResult 列表，调用 Agent J 生成案例摘要。

        Raises:
            ValueError: AGENT_J_API_KEY 未配置、session_id 不存在或爬取结果为空。
        """
        # ── 前置校验 ──────────────────────────────────────────────────────
        if not AGENT_J_API_KEY:
            raise ValueError(
                "AGENT_J_API_KEY 未配置，Case 5 功能不可用，请在 .env 中设置该环境变量"
            )

        scraped_list: list[ScrapedResult] | None = session_store.get(session_id)
        if not scraped_list:
            raise ValueError(
                f"会话 {session_id} 不存在或已过期，请重新搜索"
            )

        keyword = scraped_list[0].keyword if scraped_list else ""
        n = len(scraped_list)

        logger.info(
            "Session %s Case5：keyword=%s，共 %d 条 ScrapedResult",
            session_id, keyword, n,
        )

        # ── 进度 1：创建 Agent J conversation ────────────────────────────
        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": f"正在为 {n} 条案例创建 Agent J 分析会话...",
            },
        )

        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as init_client:
                cr = await init_client.post(
                    CREATE_URL,
                    headers=agent_j_auth_headers(),
                    json={"user_id": "case5_analyze_user"},
                )
                cr.raise_for_status()
            conv_id = pick_conversation_id(cr.json())
            if not conv_id:
                raise RuntimeError(f"无法创建 Agent J 对话：{cr.json()}")
            logger.info(
                "Session %s Case5：Agent J conversation_id=%s", session_id, conv_id
            )
        except Exception as exc:
            raise ValueError(f"创建 Agent J 对话失败：{exc}") from exc

        # ── 构建 md document item ────────────────────────────────────────
        doc_item = _build_case5_doc_item(keyword, scraped_list)
        logger.info(
            "Session %s Case5：md 文件已构建，base64 后 %d bytes",
            session_id,
            len(doc_item["base64_content"]),
        )

        # ── 进度 2：调用 Agent J ──────────────────────────────────────────
        yield SseEvent(
            type="progress",
            data={
                "stage": "analyzing",
                "message": (
                    f"已将 {n} 条案例发送给 Agent J，正在生成摘要，请稍候..."
                    "（通常需要 1-3 分钟）"
                ),
            },
        )

        text_prompt = (
            "Please analyse the attached HKLII search results and provide a structured "
            "summary for each case. For every case, include: (1) case name and citation, "
            "(2) court and date, (3) key legal issues and holdings in 2-3 sentences, "
            "(4) relevance to the search keyword. "
            "Follow the output structure defined in your role configuration."
        )

        try:
            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as j_client:
                summary_text = await _ask_agent_j(
                    j_client, conv_id, text_prompt, doc_item
                )
            logger.info(
                "Session %s Case5：Agent J 摘要生成完成，回复长度 %d 字符",
                session_id, len(summary_text),
            )
        except Exception as exc:
            logger.error(
                "Session %s Case5：Agent J 调用失败：%s", session_id, exc
            )
            yield SseEvent(
                type="error",
                data={"message": f"案例摘要生成失败：{exc}"},
            )
            return

        # ── 写入 session_results_store（必须在 yield result 之前完成）────
        session_results_store[session_id] = {
            "case_type": "case5",
            "created_at": time.time(),
            "results": {
                _SUMMARY_KEY: {
                    "analysis_text": summary_text,
                    "conversation_id": conv_id,
                    "keyword": keyword,
                },
            },
        }

        yield SseEvent(
            type="result",
            data={
                "result_key": _SUMMARY_KEY,
                "analysis_text": summary_text,
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
        生成 HKLII 案例摘要报告文件。

        Args:
            result_key:    必须为 "HKLII 案例摘要"。
            output_format: "pdf" 或 "docx"。

        Returns:
            (file_bytes, filename, media_type)
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
        base_name = "HKLII Case Summary"

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
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
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
        基于 HKLII 案例摘要进行后续文字追问。

        conversation_id 有值时：直接复用 analyze 阶段的 Agent J conversation（推荐）。
        conversation_id 为 None 时：创建新 conversation，注入摘要全文作为上下文。

        Returns:
            {"conversation_id": str, "reply": str}
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            raise ValueError("会话不存在或尚未完成 HKLII 摘要分析，请先进行综合分析")

        results = store_entry.get("results", {})
        if _SUMMARY_KEY not in results:
            raise ValueError("案例摘要尚未生成，请先完成综合分析")

        try:
            conv_id: str

            if conversation_id:
                # 直接复用 analyze conversation，Agent J 已有完整上下文
                conv_id = conversation_id
            else:
                # 创建新 conversation，注入摘要文本作为上下文
                async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                    cr = await client.post(
                        CREATE_URL,
                        headers=agent_j_auth_headers(),
                        json={"user_id": "case5_chat_user"},
                    )
                    cr.raise_for_status()
                conv_id = pick_conversation_id(cr.json())
                if not conv_id:
                    raise RuntimeError(f"无法创建 Agent J 追问对话：{cr.json()}")

                summary_text = results[_SUMMARY_KEY]["analysis_text"]
                keyword = results[_SUMMARY_KEY].get("keyword", "")
                context_text = (
                    f'以下是关键词 “{keyword}” 在 HKLII 中的案例摘要，'
                    "请在回答后续问题时以此为背景：\n\n" + summary_text
                )
                async with httpx.AsyncClient(timeout=180.0, trust_env=False) as ctx_client:
                    await _ask_agent_j(ctx_client, conv_id, context_text, doc_item=None)
                logger.info(
                    "Session %s Case5：已创建追问 conversation 并注入摘要上下文，conv_id=%s",
                    session_id, conv_id,
                )

            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as chat_client:
                reply = await _ask_agent_j(chat_client, conv_id, text, doc_item=None)

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

        Case 5 固定只有一个 result_key："HKLII 案例摘要"。
        分析失败时返回空列表。
        """
        store_entry = session_results_store.get(session_id)
        if not store_entry:
            return []
        return [k for k in store_entry.get("results", {}) if k == _SUMMARY_KEY]


# ── Agent J 私有调用函数 ──────────────────────────────────────────────────────


async def _ask_agent_j(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
    doc_item: dict | None = None,
) -> str:
    """
    向 Agent J 发送消息（可选附带 document item），返回回复文本。

    GPTBots payload 格式（对齐 D1 / Case 2 验证格式）：
    {
        "conversation_id": ...,
        "response_mode": "blocking",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": ...},
            {"type": "document", "document": [...]}  ← doc_item 非 None 时追加
        ]}]
    }
    """
    content: list[dict] = [{"type": "text", "text": text}]
    if doc_item is not None:
        content.append({"type": "document", "document": [doc_item]})

    payload = {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [{"role": "user", "content": content}],
    }

    logger.info(
        "Case5 Agent J 发送请求：conversation_id=%s，携带文档=%s",
        conversation_id,
        doc_item["name"] if doc_item else "无",
    )

    resp = await client.post(MESSAGE_URL, headers=agent_j_auth_headers(), json=payload)
    resp.raise_for_status()
    return extract_gptbots_reply(resp.json())


# ── md 文档构造辅助 ──────────────────────────────────────────────────────────


def _has_encoding_issue(content: str) -> bool:
    """
    检测正文是否存在编码异常。

    检测策略：
    - U+FFFD（替换字符）：Playwright 遇到无效 Unicode 时插入
    - 连续出现 2+ 个 Latin Extended 字符（0xC0-0xFF）：
      典型的 UTF-8 字节被当作 Latin-1 解码时产生的乱码特征
      例：'è¿' = U+00E8 U+00BF（实为"近"字的 UTF-8 三字节序列的前两字节）
    """
    if "�" in content:
        return True
    # Latin Extended 字符连续出现（≥2）——正常英文法律文本不应出现
    return bool(re.search(r"[À-ÿ]{2,}", content))


def _build_case5_doc_item(
    keyword: str,
    results: list[ScrapedResult],
) -> dict:
    """
    将 list[ScrapedResult] 序列化为 GPTBots document item（base64 编码的 md 文件）。

    Markdown 结构：
        # HKLII Search Results: "{keyword}"
        Total: N results | Searched: YYYY-MM-DD

        ========================================
        ## Case 1: {title}

        - **Citation**: {subtitle}
        - **Court / Database**: {database}
        - **Date**: {date}
        - **URL**: {url}
        - **Content Length**: 10,251 chars

        [Warning: encoding issue detected in content]   ← 仅乱码时显示

        ### Full Content

        {content（超过 SAFETY_LIMIT_CHARS 才截断）}

        [Note: content exceeded safety limit, truncated from X to 100,000 chars]
        ========================================

    Args:
        keyword: 搜索关键词（溯源用）
        results: ScrapedResult 列表

    Returns:
        GPTBots document item dict，格式为：
        {"base64_content": str, "format": "md", "name": "hklii_search_results.md"}
    """
    today = date.today().isoformat()
    header = (
        f'# HKLII Search Results: "{keyword}"\n'
        f"Total: {len(results)} results | Searched: {today}\n"
    )

    case_blocks: list[str] = []
    for i, result in enumerate(results, start=1):
        content = result.content or ""

        # 内容为空：抓取失败
        if not content:
            content_section = "[Content unavailable - scraping failed]"
            content_length_display = "N/A (scraping failed)"
            encoding_warning = ""
            truncation_note = ""
        else:
            original_len = len(content)
            truncated = original_len > SAFETY_LIMIT_CHARS

            if truncated:
                content = content[:SAFETY_LIMIT_CHARS]
                truncation_note = (
                    f"\n\n[Note: content exceeded safety limit, "
                    f"truncated from {original_len:,} to {SAFETY_LIMIT_CHARS:,} chars]"
                )
            else:
                truncation_note = ""

            encoding_warning = (
                "\n[Warning: encoding issue detected in content]\n"
                if _has_encoding_issue(content)
                else ""
            )
            content_section = content + truncation_note
            content_length_display = f"{original_len:,} chars"

        block = (
            f"{_CASE_SEPARATOR}\n"
            f"## Case {i}: {result.title}\n\n"
            f"- **Citation**: {result.subtitle or 'N/A'}\n"
            f"- **Court / Database**: {result.database or 'N/A'}\n"
            f"- **Date**: {result.date or 'N/A'}\n"
            f"- **URL**: {result.url}\n"
            f"- **Content Length**: {content_length_display}\n"
            f"{encoding_warning}\n"
            f"### Full Content\n\n"
            f"{content_section}\n"
            f"{_CASE_SEPARATOR}"
        )
        case_blocks.append(block)

    body = "\n\n".join(case_blocks)
    md_content = f"{header}\n{body}\n"

    b64 = base64.b64encode(md_content.encode("utf-8")).decode("utf-8")
    return {"base64_content": b64, "format": "md", "name": "hklii_search_results.md"}
