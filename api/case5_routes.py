"""
api/case5_routes.py
~~~~~~~~~~~~~~~~~~~
Case 5 — HKLII 案例检索路由。

端点：
    POST /case5/scrape   — SSE 流式爬取，搜索 HKLII 并抓取正文内容

设计说明：
- Playwright sync API 不能在 asyncio 事件循环中直接调用；通过
  loop.run_in_executor() 在线程池里执行 search_hklii / attach_contents_parallel。
- 爬取结果写入 pdf_chat.session_store[session_id]（与 Case 1/2 保持一致）。
- session_metadata 写入 case_type="case5"，供 /pdf/session/analyze 路由正确分派。
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Form
from fastapi.responses import StreamingResponse

from api.pdf_chat import session_metadata, session_store
from scraper.hklii import (
    NoResultsFound,
    ScrapedResult,
    SearchTimeout,
    attach_contents_parallel,
    search_hklii,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/case5", tags=["case5"])

# 共享线程池（Playwright 每任务独立浏览器，线程数不需要太多）
_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="hklii-scraper")


# ---------------------------------------------------------------------------
# SSE 序列化辅助
# ---------------------------------------------------------------------------


def _sse(event_type: str, data: dict) -> str:
    """将事件序列化为 SSE 格式字符串。"""
    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.post("/scrape")
async def scrape_hklii(
    session_id: str = Form(...),
    keyword: str = Form(...),
    max_results: int = Form(default=10),
    content_concurrency: int = Form(default=3),
):
    """
    SSE 流式端点：搜索 HKLII 并并行抓取正文。

    事件序列：
        progress  stage=searching  — 开始搜索
        progress  stage=fetching   — 搜索完成，开始抓取正文
        scrape_complete            — 全部完成，携带摘要列表

    结果存储：
        session_store[session_id]    = list[ScrapedResult]
        session_metadata[session_id] = {"case_type": "case5", "keyword": keyword}
    """

    async def event_stream():
        loop = asyncio.get_event_loop()

        # ── 阶段 1：搜索 ──────────────────────────────────────────────────
        yield _sse(
            "progress",
            {
                "stage": "searching",
                "message": f"正在搜索 HKLII：{keyword}...",
            },
        )

        try:
            results: list[ScrapedResult] = await loop.run_in_executor(
                _EXECUTOR,
                lambda: search_hklii(
                    keyword,
                    max_results=max_results,
                    headless=True,
                ),
            )
        except NoResultsFound:
            logger.info(
                "HKLII 无结果 [session=%s, keyword=%s]", session_id, keyword
            )
            yield _sse(
                "no_results",
                {
                    "keyword": keyword,
                    "message": f"未找到关键词 \"{keyword}\" 的相关案例，请尝试其他关键词",
                },
            )
            return
        except SearchTimeout as exc:
            logger.warning("HKLII 搜索超时 [session=%s]: %s", session_id, exc)
            yield _sse(
                "search_timeout",
                {"message": "搜索超时，HKLII 可能暂时无法访问，请稍后重试"},
            )
            return
        except Exception as exc:
            logger.error("HKLII 搜索失败 [session=%s]: %s", session_id, exc)
            yield _sse(
                "error",
                {"message": f"搜索失败：{exc}"},
            )
            return

        if not results:
            yield _sse(
                "no_results",
                {
                    "keyword": keyword,
                    "message": "搜索未返回任何结果，请尝试其他关键词",
                },
            )
            return

        n = len(results)
        logger.info(
            "HKLII 搜索完成 [session=%s, keyword=%s]：%d 条结果",
            session_id,
            keyword,
            n,
        )

        # ── 阶段 2：抓取正文 ──────────────────────────────────────────────
        yield _sse(
            "progress",
            {
                "stage": "fetching",
                "message": (
                    f"已找到 {n} 条结果，正在抓取详情"
                    f"（约 {max(20, n * 5)}-{max(60, n * 10)} 秒）..."
                ),
                "count": n,
            },
        )

        try:
            await loop.run_in_executor(
                _EXECUTOR,
                lambda: attach_contents_parallel(
                    results,
                    content_concurrency=content_concurrency,
                    headless=True,
                ),
            )
        except Exception as exc:
            logger.error(
                "HKLII 正文抓取失败 [session=%s]: %s", session_id, exc
            )
            yield _sse(
                "error",
                {"message": f"正文抓取失败：{exc}"},
            )
            return

        logger.info(
            "HKLII 正文抓取完成 [session=%s]：字符数 %s",
            session_id,
            [len(r.content) for r in results],
        )

        # ── 写入 session store ───────────────────────────────────────────
        session_store[session_id] = results
        session_metadata[session_id] = {
            "case_type": "case5",
            "keyword": keyword,
        }

        # ── 阶段 3：完成 ─────────────────────────────────────────────────
        summary_list = [
            {
                "index": i + 1,
                "title": r.title,
                "doc_type": r.doc_type,
                "date": r.date,
                "url": r.url,
                "content_length": len(r.content),
            }
            for i, r in enumerate(results)
        ]

        yield _sse(
            "scrape_complete",
            {
                "count": n,
                "keyword": keyword,
                "results": summary_list,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
