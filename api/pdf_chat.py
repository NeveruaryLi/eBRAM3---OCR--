"""
PDF 相关路由

/pdf/chat        → 整个 PDF 发给 PaddleOCR API，OCR 完成后发给 GPTBots（兼容旧端点）
/pdf/pages/chat  → 整个 PDF 发给 PaddleOCR API，OCR 进度实时推送，逐页 GPTBots 分析，SSE 流式输出
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from model.config import (
    CREATE_URL,
    MESSAGE_URL,
    PADDLE_OCR_JOB_URL,
    PADDLE_OCR_MODEL,
    agent_b_auth_headers,
    auth_headers,
    paddle_ocr_headers,
    pick_conversation_id,
)
from model.pdf_processor import extract_native_text, get_page_count
from model.utils import extract_gptbots_reply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pdf", tags=["pdf"])

# PaddleOCR 可选参数（关闭不必要的处理步骤，加快速度）
_PADDLE_OPTIONS: dict = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}

# 轮询配置
_POLL_INTERVAL = 5    # 每次轮询间隔（秒）
_MAX_POLLS = 120      # 最大轮询次数（120 × 5s = 10 分钟）

# GPTBots 重试配置
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_AGENT_RETRIES = 3

# ── 多文档会话管理 ─────────────────────────────────────────────────────────────

SESSION_TTL_SECONDS: int = 7200  # 2 小时过期


@dataclass
class DocResult:
    """单份 PDF 的 Agent A 处理结果，用于多文档会话暂存。"""

    filename: str
    summaries: list[str]
    merged_fields: dict
    created_at: datetime = field(default_factory=datetime.utcnow)


# session_id → 该会话中已处理文档列表（内存暂存，服务重启后清空）
session_store: dict[str, list[DocResult]] = {}


async def start_cleanup_task() -> None:
    """
    后台清理任务：每 30 分钟扫描 session_store，
    删除超过 SESSION_TTL_SECONDS（2 小时）的条目，防止内存泄漏。
    """
    while True:
        await asyncio.sleep(1800)  # 30 分钟一次
        cutoff = datetime.utcnow() - timedelta(seconds=SESSION_TTL_SECONDS)
        expired = [
            sid for sid, docs in session_store.items()
            if docs and docs[0].created_at < cutoff
        ]
        for sid in expired:
            del session_store[sid]
        if expired:
            logger.info(
                "session_store 清理完成：删除 %d 个过期会话，当前剩余 %d 个",
                len(expired), len(session_store),
            )


# ── PaddleOCR API 封装 ────────────────────────────────────────────────────────

async def _submit_paddle_ocr_job(
    client: httpx.AsyncClient,
    pdf_bytes: bytes,
    filename: str = "document.pdf",
) -> str:
    """
    向 PaddleOCR 官方 API 提交 PDF OCR 任务，返回 jobId。

    Args:
        client:    httpx 异步客户端。
        pdf_bytes: PDF 文件原始字节。
        filename:  上传时使用的文件名（影响日志，不影响识别）。

    Returns:
        jobId 字符串。

    Raises:
        RuntimeError: 提交失败或响应格式异常。
    """
    try:
        resp = await client.post(
            PADDLE_OCR_JOB_URL,
            headers=paddle_ocr_headers(),
            data={
                "model": PADDLE_OCR_MODEL,
                "optionalPayload": json.dumps(_PADDLE_OPTIONS),
            },
            files={"file": (filename, pdf_bytes, "application/pdf")},
            timeout=60.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"PaddleOCR 任务提交失败（HTTP {exc.response.status_code}）："
            f"{exc.response.text[:300]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"PaddleOCR 任务提交异常：{exc}") from exc

    try:
        job_id: str = resp.json()["data"]["jobId"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"PaddleOCR 提交响应格式异常：{resp.text[:300]}") from exc

    logger.info("PaddleOCR 任务提交成功，jobId=%s", job_id)
    return job_id


def _parse_ocr_jsonl(jsonl_text: str, total_pages: int) -> list[str]:
    """
    解析 PaddleOCR JSONL 结果，返回每页 Markdown 文本列表。

    PaddleOCR 官方 API 的 JSONL 格式说明：
      - JSONL 可能包含多行，每行是一个独立 JSON 对象
      - 每行的 result.layoutParsingResults 是一个数组，每个元素对应一页
      - 多行按顺序拼接，共同覆盖整个 PDF（例如 6 页 PDF 可能是 5+1 两行）
      - layoutParsingResults[i].markdown.text = 该批次第 i+1 页的 Markdown 内容
      - 空白页的 markdown.text 为空字符串，而非缺失字段

    Args:
        jsonl_text:  JSONL 格式的 OCR 结果文本。
        total_pages: PDF 总页数（用于构造固定长度结果列表）。

    Returns:
        list[str]，长度为 total_pages，第 i 项为第 i+1 页的 Markdown 文本。
    """
    results: list[str] = [""] * total_pages
    lines = [ln for ln in jsonl_text.strip().splitlines() if ln.strip()]

    if not lines:
        logger.warning("JSONL 为空，无 OCR 结果可解析")
        return results

    # 收集所有行的 layoutParsingResults，按顺序拼接（覆盖全部页面）
    all_layout_results: list[dict] = []
    for line_no, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("JSONL 第 %d 行解析失败（跳过）：%s", line_no, exc)
            continue
        batch = obj.get("result", {}).get("layoutParsingResults", [])
        logger.debug("JSONL 第 %d 行：layoutParsingResults 共 %d 项", line_no, len(batch))
        all_layout_results.extend(batch)

    if not all_layout_results:
        logger.warning(
            "所有 JSONL 行的 layoutParsingResults 均为空，文档可能已加密或损坏"
        )
        return results

    if len(all_layout_results) > total_pages:
        logger.warning(
            "layoutParsingResults 总项数 (%d) 超过 total_pages (%d)，将截断",
            len(all_layout_results),
            total_pages,
        )

    for page_idx, page_block in enumerate(all_layout_results):
        if page_idx >= total_pages:
            break
        text = page_block.get("markdown", {}).get("text", "").strip()
        results[page_idx] = text

    logger.info(
        "JSONL 解析完成：%d 行，共 %d 个 layoutParsingResults 项，映射到 %d 页",
        len(lines),
        len(all_layout_results),
        total_pages,
    )
    return results


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    """将字典序列化为 SSE data 行（以双换行结尾）。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _ask_gptbots(
    client: httpx.AsyncClient,
    conversation_id: str,
    page_num: int,
    ocr_text: str,
) -> str:
    """将某页 OCR 文本发给 GPTBots，返回 Agent 回复文本。"""
    message_text = f"【第{page_num}页】\n{ocr_text}"
    payload = {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": message_text}],
            }
        ],
    }
    mr = await client.post(MESSAGE_URL, headers=auth_headers(), json=payload)
    mr.raise_for_status()
    return extract_gptbots_reply(mr.json())


async def _ask_gptbots_with_retry(
    client: httpx.AsyncClient,
    conversation_id: str,
    page_num: int,
    ocr_text: str,
) -> str:
    """
    带指数退避重试的 GPTBots 调用。

    对 _RETRY_STATUS 中的 HTTP 状态码（429/500/502/503/504）进行重试，
    最多重试 _MAX_AGENT_RETRIES 次，等待时间为 1s、2s、4s（指数退避）。
    其他异常或重试耗尽后直接抛出。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_AGENT_RETRIES):
        try:
            return await _ask_gptbots(client, conversation_id, page_num, ocr_text)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRY_STATUS and attempt < _MAX_AGENT_RETRIES - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "第 %d 页 GPTBots 调用失败（HTTP %d），%.0fs 后重试（第 %d/%d 次）",
                    page_num, exc.response.status_code, wait, attempt + 1, _MAX_AGENT_RETRIES,
                )
                await asyncio.sleep(wait)
                last_exc = exc
                continue
            raise
    raise last_exc  # type: ignore[misc]


# ── Agent B 工具函数 ──────────────────────────────────────────────────────────

_PART_A_RE = re.compile(r"===PART_A_START===(.*?)===PART_A_END===", re.DOTALL)
_PART_B_RE = re.compile(r"===PART_B_START===(.*?)===PART_B_END===", re.DOTALL)

_AGENT_B_PROMPT_PREFIX = (
    "以下是一份法律文档的完整分析结果，包含逐页摘要和结构化提取字段，"
    "请基于这些信息进行综合分析。\n\n"
)


def _parse_agent_a_response(response: str) -> tuple[str, dict | None]:
    """
    从 Agent A 的回复中提取 PART_A 摘要和 PART_B JSON 字段。

    Returns:
        (part_a_text, part_b_dict)
        part_b_dict 在解析失败时返回 None。
    """
    part_a = ""
    a_match = _PART_A_RE.search(response)
    if a_match:
        part_a = a_match.group(1).strip()

    part_b: dict | None = None
    b_match = _PART_B_RE.search(response)
    if b_match:
        try:
            part_b = json.loads(b_match.group(1).strip())
        except json.JSONDecodeError as exc:
            logger.warning("PART_B JSON 解析失败（跳过该页字段）：%s", exc)

    return part_a, part_b


def _merge_part_b(part_b_list: list[dict]) -> dict:
    """
    合并多页的 PART_B 字段：
    - 数组字段（timeline_events / amounts / contract_terms 等）：追加所有页的条目
    - 对象字段（submitting_party_info / case_overview 等）：取第一个非空值
    """
    merged: dict = {}
    for page_data in part_b_list:
        if not isinstance(page_data, dict):
            continue
        for key, value in page_data.items():
            if key not in merged:
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged[key], list):
                merged[key] = merged[key] + value  # 不可变：创建新列表
    return merged


def _build_agent_b_input(summaries: list[str], merged_fields: dict) -> str:
    """构造发给 Agent B 的输入文本（Markdown + JSON 代码块格式）。"""
    filled = [(i + 1, s) for i, s in enumerate(summaries) if s.strip()]
    summary_block = "\n\n".join(f"[Page {page_num}]\n{text}" for page_num, text in filled)
    fields_json = json.dumps(merged_fields, ensure_ascii=False, indent=2)

    return (
        f"{_AGENT_B_PROMPT_PREFIX}"
        f"## Document Summary ({len(filled)} pages)\n\n"
        f"{summary_block}\n\n"
        f"---\n\n"
        f"## Structured Data\n\n"
        f"```json\n{fields_json}\n```\n"
    )


def _build_doc_message(doc_index: int, doc: DocResult) -> str:
    """
    构造发给 Agent B 的单份材料消息（多文档综合分析时使用）。
    格式：材料序号+文件名标注 + Markdown 摘要 + JSON 代码块。
    """
    filled = [(i + 1, s) for i, s in enumerate(doc.summaries) if s.strip()]
    summary_block = "\n\n".join(f"[Page {p}]\n{t}" for p, t in filled)
    fields_json = json.dumps(doc.merged_fields, ensure_ascii=False, indent=2)
    return (
        f"以下是材料{doc_index}《{doc.filename}》的分析结果，"
        f"包含逐页摘要和结构化提取字段：\n\n"
        f"## Document Summary ({len(filled)} pages)\n\n"
        f"{summary_block}\n\n"
        f"---\n\n"
        f"## Structured Data\n\n"
        f"```json\n{fields_json}\n```\n"
    )


async def _ask_agent_b(
    client: httpx.AsyncClient,
    conversation_id: str,
    input_text: str,
) -> str:
    """将合并后的文档分析结果发给 Agent B，返回综合分析回复。"""
    payload = {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": input_text}],
            }
        ],
    }
    mr = await client.post(MESSAGE_URL, headers=agent_b_auth_headers(), json=payload)
    mr.raise_for_status()
    return extract_gptbots_reply(mr.json())


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.post("/chat")
async def pdf_chat(pdf_file: UploadFile = File(...)):
    """
    将整个 PDF 发给 PaddleOCR API，OCR 完成后把全文 Markdown 发给 GPTBots。
    适合小文档快速测试；大文档建议使用 /pdf/pages/chat。
    """
    pdf_bytes = await pdf_file.read()
    filename = pdf_file.filename or "document.pdf"

    try:
        total_pages = get_page_count(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── 提交 + 轮询 ──────────────────────────────────────────────────────────
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            job_id = await _submit_paddle_ocr_job(client, pdf_bytes, filename)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"OCR 提交失败：{exc}")

        jsonl_url: str | None = None
        confirmed_pages = total_pages
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                pr = await client.get(
                    f"{PADDLE_OCR_JOB_URL}/{job_id}",
                    headers=paddle_ocr_headers(),
                    timeout=30.0,
                )
                pr.raise_for_status()
            except Exception as exc:
                logger.warning("轮询异常，将继续重试：%s", exc)
                continue
            data = pr.json().get("data", {})
            state = data.get("state", "unknown")
            if state == "done":
                try:
                    jsonl_url = data["resultUrl"]["jsonUrl"]
                except (KeyError, TypeError) as exc:
                    raise HTTPException(status_code=502, detail=f"resultUrl 格式异常：{exc}")
                confirmed_pages = data.get("extractProgress", {}).get("totalPages") or total_pages
                break
            if state == "failed":
                raise HTTPException(
                    status_code=502,
                    detail=f"OCR 任务失败：{data.get('errorMsg', '未知错误')}",
                )
        else:
            raise HTTPException(status_code=504, detail="OCR 任务超时")

    # ── 下载 JSONL（独立 client，最多重试 3 次）───────────────────────────────
    logger.info("开始下载 JSONL，URL 前缀：%s", jsonl_url[:80] if jsonl_url else "None")
    jsonl_text: str | None = None
    last_dl_error: Exception | None = None
    for dl_attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=90.0, trust_env=False) as dl_client:
                jr = await dl_client.get(jsonl_url)
                jr.raise_for_status()
            jsonl_text = jr.text
            logger.info("JSONL 下载成功（第 %d 次），大小 %d bytes", dl_attempt, len(jsonl_text))
            break
        except Exception as exc:
            last_dl_error = exc
            logger.warning(
                "JSONL 下载第 %d 次失败（%s: %s），%s",
                dl_attempt, type(exc).__name__, exc,
                "将重试" if dl_attempt < 3 else "放弃",
            )
            if dl_attempt < 3:
                await asyncio.sleep(2)
    if jsonl_text is None:
        logger.error("JSONL 下载全部失败：%s", last_dl_error, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"下载 OCR 结果失败（{type(last_dl_error).__name__}）：{last_dl_error}",
        )

    ocr_results = _parse_ocr_jsonl(jsonl_text, confirmed_pages)
    # 对 OCR 空白页进行原生文字回退
    empty_count = sum(1 for r in ocr_results if not r)
    if empty_count:
        native_texts = extract_native_text(pdf_bytes)
        for idx, text in enumerate(ocr_results):
            if not text and idx < len(native_texts) and native_texts[idx]:
                ocr_results[idx] = native_texts[idx]
                logger.info("第 %d 页 OCR 为空，已用原生文字回退（%d 字）", idx + 1, len(native_texts[idx]))
    parts = [
        f"【第{i+1}页】\n{text}" if text else f"【第{i+1}页】\n[空白页]"
        for i, text in enumerate(ocr_results)
    ]
    ocr_content = "\n\n".join(parts)

    # ── 直接 HTTP 调用 GPTBots（去除 LangGraph 层）──────────────────────────
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as agent_client:
            cr = await agent_client.post(
                CREATE_URL,
                headers=auth_headers(),
                json={"user_id": "pdf_chat_user"},
            )
            cr.raise_for_status()
            conv_id = pick_conversation_id(cr.json())
            if not conv_id:
                raise HTTPException(status_code=502, detail=f"无法获取 conversation_id：{cr.json()}")

            payload = {
                "conversation_id": conv_id,
                "response_mode": "blocking",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": ocr_content}]}
                ],
            }
            mr = await agent_client.post(MESSAGE_URL, headers=auth_headers(), json=payload)
            mr.raise_for_status()
            reply = extract_gptbots_reply(mr.json())
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}")

    return {
        "filename": filename,
        "ocr_content": ocr_content,
        "response": reply,
    }


@router.post("/pages/chat")
async def pdf_pages_chat(
    pdf_file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
):
    """
    将 PDF 发给 PaddleOCR API，OCR 进度实时推送给前端；
    OCR 完成后逐页发给 GPTBots Agent A 分析，通过 SSE 流式输出。

    运行模式由 session_id 参数决定：
      - session_id 为空（单文档模式）：处理完成后直接调用 Agent B 综合分析。
      - session_id 有值（多文档模式）：结果暂存到 session_store，发送 doc_complete 事件，
        不调用 Agent B（等待前端触发 /pdf/session/analyze）。

    SSE 事件（两种模式共有）：
      {"type": "start",       "total_pages": N, ...}
      {"type": "stage",       "stage": "ocr"|"agent_init"|"agent", ...}
      {"type": "progress",    "stage": "ocr",   "extracted": N, "total": M, ...}
      {"type": "ocr_done",    "success": N, "failed": M, ...}
      {"type": "progress",    "stage": "agent", "page": N, "total": M, ...}
      {"type": "page_result", "page": N, ..., "status": "success"|"agent_failed", ...}
      {"type": "complete",    "total_pages": N, "success_count": N, ..., ...}
      {"type": "error",       "message": "...", ...}

    单文档模式额外事件：
      {"type": "agent_b_start",  "message": "..."}
      {"type": "agent_b_result", "content": "...", "status": "success"|"failed"}

    多文档模式额外事件：
      {"type": "doc_complete", "filename": "...", "session_id": "...", "doc_index": N, ...}
    """
    pdf_bytes = await pdf_file.read()
    filename = pdf_file.filename or "document.pdf"
    incoming_conv_id = conversation_id
    incoming_session_id = session_id
    logger.info(
        "收到 PDF：%s，大小 %d bytes，conv_id=%s，session_id=%s",
        filename, len(pdf_bytes), incoming_conv_id, incoming_session_id,
    )

    async def stream() -> AsyncIterator[str]:
        try:
            # ── 1. 获取页数 ────────────────────────────────────────────────
            try:
                total_pages = get_page_count(pdf_bytes)
            except ValueError as exc:
                yield _sse({"type": "error", "message": str(exc)})
                return

            yield _sse({
                "type": "start",
                "total_pages": total_pages,
                "message": f"共 {total_pages} 页，正在提交 OCR 任务...",
            })

            # ── 2. 提交 PaddleOCR 任务 ────────────────────────────────────
            yield _sse({"type": "stage", "stage": "ocr", "message": "正在提交 PaddleOCR 任务..."})

            async with httpx.AsyncClient(trust_env=False) as client:
                try:
                    job_id = await _submit_paddle_ocr_job(client, pdf_bytes, filename)
                except RuntimeError as exc:
                    yield _sse({"type": "error", "message": f"OCR 任务提交失败：{exc}"})
                    return

                yield _sse({
                    "type": "stage",
                    "stage": "ocr",
                    "message": f"任务已提交（jobId={job_id}），等待处理...",
                })

                # ── 3. 轮询进度，实时推送 SSE ─────────────────────────────
                jsonl_url: str | None = None
                confirmed_pages = total_pages

                for attempt in range(1, _MAX_POLLS + 1):
                    await asyncio.sleep(_POLL_INTERVAL)
                    try:
                        pr = await client.get(
                            f"{PADDLE_OCR_JOB_URL}/{job_id}",
                            headers=paddle_ocr_headers(),
                            timeout=30.0,
                        )
                        pr.raise_for_status()
                    except Exception as exc:
                        logger.warning("轮询第 %d 次异常，将继续重试：%s", attempt, exc)
                        continue

                    data = pr.json().get("data", {})
                    state = data.get("state", "unknown")
                    progress = data.get("extractProgress", {})
                    extracted = progress.get("extractedPages", 0)
                    total_p = progress.get("totalPages", total_pages)

                    yield _sse({
                        "type": "progress",
                        "stage": "ocr",
                        "extracted": extracted,
                        "total": total_p,
                        "message": f"OCR 处理中：{extracted}/{total_p} 页",
                    })

                    if state == "done":
                        try:
                            jsonl_url = data["resultUrl"]["jsonUrl"]
                        except (KeyError, TypeError) as exc:
                            yield _sse({"type": "error", "message": f"resultUrl 格式异常：{exc}"})
                            return
                        confirmed_pages = total_p or total_pages
                        break

                    if state == "failed":
                        error_msg = data.get("errorMsg", "未知错误")
                        yield _sse({"type": "error", "message": f"OCR 任务失败：{error_msg}"})
                        return
                else:
                    yield _sse({"type": "error", "message": "OCR 任务超时（等待超过 10 分钟）"})
                    return

            # ── 4. 下载并解析 JSONL ───────────────────────────────────
            # 用独立 client 避免长期轮询后连接池状态不佳，并加最多 3 次重试
            logger.info("开始下载 JSONL，URL 前缀：%s", jsonl_url[:80] if jsonl_url else "None")
            jsonl_text: str | None = None
            last_dl_error: Exception | None = None
            for dl_attempt in range(1, 4):
                try:
                    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as dl_client:
                        jr = await dl_client.get(jsonl_url)
                        jr.raise_for_status()
                    jsonl_text = jr.text
                    logger.info(
                        "JSONL 下载成功（第 %d 次），大小 %d bytes",
                        dl_attempt, len(jsonl_text),
                    )
                    break
                except Exception as exc:
                    last_dl_error = exc
                    logger.warning(
                        "JSONL 下载第 %d 次失败（%s: %s），%s",
                        dl_attempt,
                        type(exc).__name__,
                        exc,
                        "将重试" if dl_attempt < 3 else "放弃",
                    )
                    if dl_attempt < 3:
                        await asyncio.sleep(2)

            if jsonl_text is None:
                logger.error("JSONL 下载全部失败：%s", last_dl_error, exc_info=True)
                yield _sse({
                    "type": "error",
                    "message": f"下载 OCR 结果失败（{type(last_dl_error).__name__}）：{last_dl_error}",
                })
                return

            ocr_results = _parse_ocr_jsonl(jsonl_text, confirmed_pages)

            # 对 OCR 空白页进行原生文字回退
            empty_pages = [i for i, r in enumerate(ocr_results) if not r]
            logger.info("OCR 空白页索引: %s (共 %d 页)", empty_pages, confirmed_pages)
            if empty_pages:
                native_texts = extract_native_text(pdf_bytes)
                logger.info("原生文字提取完成，共 %d 页，各页字数: %s",
                            len(native_texts), [len(t) for t in native_texts])
                for idx in empty_pages:
                    if idx < len(native_texts) and native_texts[idx]:
                        ocr_results[idx] = native_texts[idx]
                        logger.info(
                            "第 %d 页 OCR 为空，已用原生文字回退（%d 字）",
                            idx + 1, len(native_texts[idx]),
                        )
                    else:
                        logger.warning(
                            "第 %d 页 OCR 为空且无原生文字（native_texts 长度=%d）",
                            idx + 1, len(native_texts),
                        )

            ocr_ok = sum(1 for r in ocr_results if r)

            yield _sse({
                "type": "ocr_done",
                "success": ocr_ok,
                "failed": confirmed_pages - ocr_ok,
                "message": f"OCR 完成：{ocr_ok}/{confirmed_pages} 页有内容",
            })

            # ── 5. 创建 / 复用 GPTBots 对话 ──────────────────────────────
            conv_id: str | None = None  # 确保在所有分支后均已赋值
            if incoming_conv_id:
                conv_id = incoming_conv_id
                logger.info("复用已有 conversation_id: %s", conv_id)
                yield _sse({
                    "type": "stage",
                    "stage": "agent_init",
                    "message": "复用已有对话会话，开始 AI 分析...",
                })
            else:
                yield _sse({
                    "type": "stage",
                    "stage": "agent_init",
                    "message": "正在初始化 AI 对话会话...",
                })
                try:
                    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as init_client:
                        cr = await init_client.post(
                            CREATE_URL,
                            headers=auth_headers(),
                            json={"user_id": "pdf_pages_user"},
                        )
                        cr.raise_for_status()
                    conv_id = pick_conversation_id(cr.json())
                    if not conv_id:
                        yield _sse({
                            "type": "error",
                            "message": f"无法获取 conversation_id：{cr.json()}",
                        })
                        return
                    logger.info("创建新 conversation_id: %s", conv_id)
                except Exception as exc:
                    yield _sse({"type": "error", "message": f"创建 GPTBots 对话失败：{exc}"})
                    return

            # ── 6. 逐页 GPTBots 分析（带指数退避重试）────────────────────
            yield _sse({"type": "stage", "stage": "agent", "message": "开始逐页 AI 分析..."})
            results: list[dict] = []

            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as agent_client:
                for i, ocr_text in enumerate(ocr_results):
                    page_num = i + 1

                    yield _sse({
                        "type": "progress",
                        "stage": "agent",
                        "page": page_num,
                        "total": confirmed_pages,
                        "message": f"正在分析第 {page_num}/{confirmed_pages} 页...",
                    })

                    try:
                        reply = await _ask_gptbots_with_retry(
                            agent_client, conv_id, page_num, ocr_text
                        )
                        record: dict = {
                            "page": page_num,
                            "md_content": ocr_text,
                            "agent_response": reply,
                            "status": "success",
                        }
                        logger.info("第 %d 页分析完成", page_num)
                    except Exception as exc:
                        logger.error("第 %d 页 GPTBots 调用失败：%s", page_num, exc)
                        record = {
                            "page": page_num,
                            "md_content": ocr_text,
                            "agent_response": "",
                            "status": "agent_failed",
                            "error": str(exc),
                        }

                    results.append(record)
                    yield _sse({"type": "page_result", **record})

            success_count = sum(1 for r in results if r["status"] == "success")

            # ── 7. 解析 PART_A/B，按模式分支处理 ────────────────────────────
            summaries: list[str] = []
            part_b_list: list[dict] = []
            for r in results:
                if r["status"] == "success":
                    part_a, part_b = _parse_agent_a_response(r["agent_response"])
                    summaries.append(part_a)
                    if part_b:
                        part_b_list.append(part_b)
                else:
                    summaries.append("")
            merged_fields = _merge_part_b(part_b_list)

            if incoming_session_id:
                # ── 多文档模式：暂存结果，不调用 Agent B ─────────────────────
                doc = DocResult(
                    filename=filename,
                    summaries=summaries,
                    merged_fields=merged_fields,
                )
                if incoming_session_id not in session_store:
                    session_store[incoming_session_id] = []
                session_store[incoming_session_id].append(doc)
                doc_index = len(session_store[incoming_session_id])
                logger.info(
                    "session %s：已暂存材料 %d《%s》，当前共 %d 份",
                    incoming_session_id, doc_index, filename,
                    len(session_store[incoming_session_id]),
                )
                yield _sse({
                    "type": "doc_complete",
                    "filename": filename,
                    "session_id": incoming_session_id,
                    "doc_index": doc_index,
                    "success_pages": success_count,
                    "total_pages": confirmed_pages,
                    "message": f"《{filename}》处理完成，共 {success_count}/{confirmed_pages} 页",
                })
                yield _sse({
                    "type": "complete",
                    "total_pages": confirmed_pages,
                    "success_count": success_count,
                    "message": f"处理完成！{success_count}/{confirmed_pages} 页分析成功",
                    # 多文档模式：Agent B conversation 在 /session/analyze 时创建，不在此处返回
                })
                logger.info(
                    "PDF 多文档模式处理完成：%s，%d/%d 页成功",
                    filename, success_count, confirmed_pages,
                )

            else:
                # ── 单文档模式：调用 Agent B 综合分析（原有逻辑）────────────
                yield _sse({"type": "agent_b_start", "message": "正在进行综合分析..."})

                agent_b_input = _build_agent_b_input(summaries, merged_fields)
                logger.info(
                    "Agent B 输入构造完成：%d 页摘要，%d 页有效字段，输入长度 %d 字符",
                    len([s for s in summaries if s]),
                    len(part_b_list),
                    len(agent_b_input),
                )

                agent_b_reply = ""
                agent_b_status = "success"
                try:
                    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as b_init_client:
                        bcr = await b_init_client.post(
                            CREATE_URL,
                            headers=agent_b_auth_headers(),
                            json={"user_id": "pdf_pages_user"},
                        )
                        bcr.raise_for_status()
                    agent_b_conv_id = pick_conversation_id(bcr.json())
                    if not agent_b_conv_id:
                        raise RuntimeError(f"无法获取 Agent B conversation_id：{bcr.json()}")
                    logger.info("Agent B conversation_id: %s", agent_b_conv_id)

                    async with httpx.AsyncClient(timeout=180.0, trust_env=False) as b_client:
                        agent_b_reply = await _ask_agent_b(b_client, agent_b_conv_id, agent_b_input)

                    logger.info("Agent B 综合分析完成，回复长度 %d 字符", len(agent_b_reply))
                except Exception as exc:
                    logger.error("Agent B 调用失败：%s", exc)
                    agent_b_reply = f"综合分析失败：{exc}"
                    agent_b_status = "failed"

                yield _sse({
                    "type": "agent_b_result",
                    "content": agent_b_reply,
                    "status": agent_b_status,
                })
                yield _sse({
                    "type": "complete",
                    "total_pages": confirmed_pages,
                    "success_count": success_count,
                    "conversation_id": agent_b_conv_id,  # 返回 Agent B 的 conversation_id 供后续文字追问
                    "message": f"处理完成！{success_count}/{confirmed_pages} 页分析成功",
                })
                logger.info(
                    "PDF 单文档处理完成：%s，%d/%d 页成功",
                    filename, success_count, confirmed_pages,
                )

        except Exception as exc:
            logger.exception("PDF 逐页处理出现未预期的错误")
            yield _sse({"type": "error", "message": f"服务器内部错误：{exc}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/session/analyze")
async def session_analyze(
    session_id: str = Form(...),
    agent_b_conversation_id: str | None = Form(default=None),
):
    """
    多文档综合分析端点。

    读取 session_store[session_id] 中所有已处理的文档，依次发给 Agent B（多轮对话），
    最后发送综合分析请求，SSE 流式输出结果。

    参数：
      session_id               — 必填，对应 session_store 中暂存的文档列表。
      agent_b_conversation_id  — 可选，若前端已有 Agent B 对话（如先发过文字消息），
                                  则直接复用，使综合分析与之前的对话共享上下文。

    SSE 事件：
      {"type": "analysis_start",    "doc_count": N, "message": "..."}
      {"type": "analysis_sending",  "doc_index": N, "doc_count": N, "filename": "...", "message": "..."}
      {"type": "analysis_result",   "content": "...", "status": "success"|"failed"}
      {"type": "analysis_complete", "doc_count": N, "conversation_id": "...", "message": "..."}
      {"type": "error",             "message": "..."}
    """
    docs = session_store.get(session_id)
    incoming_agent_b_conv_id = agent_b_conversation_id

    async def stream() -> AsyncIterator[str]:
        if not docs:
            yield _sse({"type": "error", "message": f"会话 {session_id} 不存在或已过期，请重新上传文件"})
            return

        try:
            doc_count = len(docs)
            yield _sse({
                "type": "analysis_start",
                "doc_count": doc_count,
                "message": f"开始综合分析，共 {doc_count} 份材料...",
            })

            # 决定 Agent B conversation_id：复用已有对话 或 新建
            if incoming_agent_b_conv_id:
                agent_b_conv_id = incoming_agent_b_conv_id
                logger.info(
                    "Session %s：复用已有 Agent B conversation_id=%s",
                    session_id, agent_b_conv_id,
                )
                yield _sse({
                    "type": "analysis_start",
                    "doc_count": doc_count,
                    "message": f"复用已有对话会话，开始综合分析，共 {doc_count} 份材料...",
                })
            else:
                try:
                    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as init_client:
                        bcr = await init_client.post(
                            CREATE_URL,
                            headers=agent_b_auth_headers(),
                            json={"user_id": "session_analyze_user"},
                        )
                        bcr.raise_for_status()
                    agent_b_conv_id = pick_conversation_id(bcr.json())
                    if not agent_b_conv_id:
                        yield _sse({"type": "error", "message": f"无法创建 Agent B 对话：{bcr.json()}"})
                        return
                    logger.info("Session %s：新建 Agent B conversation_id=%s", session_id, agent_b_conv_id)
                except Exception as exc:
                    yield _sse({"type": "error", "message": f"创建 Agent B 对话失败：{exc}"})
                    return

            # 逐份发送材料，然后请求综合分析
            async with httpx.AsyncClient(timeout=180.0, trust_env=False) as b_client:
                for i, doc in enumerate(docs):
                    doc_index = i + 1
                    yield _sse({
                        "type": "analysis_sending",
                        "doc_index": doc_index,
                        "doc_count": doc_count,
                        "filename": doc.filename,
                        "message": f"正在发送材料 {doc_index}/{doc_count}：《{doc.filename}》...",
                    })

                    doc_msg = _build_doc_message(doc_index, doc)
                    logger.info(
                        "Session %s：向 Agent B 发送材料 %d《%s》，消息长度 %d 字符",
                        session_id, doc_index, doc.filename, len(doc_msg),
                    )
                    try:
                        await _ask_agent_b(b_client, agent_b_conv_id, doc_msg)
                    except Exception as exc:
                        logger.error(
                            "Session %s：材料 %d 发送 Agent B 失败：%s",
                            session_id, doc_index, exc,
                        )
                        yield _sse({"type": "error", "message": f"发送材料 {doc_index} 失败：{exc}"})
                        return

                # 综合分析请求
                final_msg = (
                    f"以上是全部 {doc_count} 份材料的完整分析结果，"
                    f"请基于所有材料进行综合分析。"
                )
                yield _sse({
                    "type": "analysis_sending",
                    "doc_index": doc_count + 1,
                    "doc_count": doc_count,
                    "filename": "",
                    "message": "正在请求综合分析...",
                })
                logger.info("Session %s：发送综合分析请求，消息长度 %d 字符", session_id, len(final_msg))

                analysis_result = ""
                analysis_status = "success"
                try:
                    analysis_result = await _ask_agent_b(b_client, agent_b_conv_id, final_msg)
                    logger.info(
                        "Session %s：综合分析完成，回复长度 %d 字符",
                        session_id, len(analysis_result),
                    )
                except Exception as exc:
                    logger.error("Session %s：综合分析失败：%s", session_id, exc)
                    analysis_result = f"综合分析失败：{exc}"
                    analysis_status = "failed"

                yield _sse({
                    "type": "analysis_result",
                    "content": analysis_result,
                    "status": analysis_status,
                })
                yield _sse({
                    "type": "analysis_complete",
                    "doc_count": doc_count,
                    "conversation_id": agent_b_conv_id,  # 返回 Agent B 的 conversation_id 供后续文字追问
                    "message": f"综合分析完成，共分析 {doc_count} 份材料",
                })

        except Exception as exc:
            logger.exception("Session %s：综合分析出现未预期的错误", session_id)
            yield _sse({"type": "error", "message": f"服务器内部错误：{exc}"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
