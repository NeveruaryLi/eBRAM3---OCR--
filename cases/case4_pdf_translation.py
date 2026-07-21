"""Case 4 — page-faithful PDF image translation through GPTBots Agent I."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable
from urllib.parse import urlparse, urlsplit, urlunsplit

import fitz
import httpx

from cases.base import CaseHandler, SseEvent
from model.config import (
    AGENT_I_API_KEY,
    CREATE_URL,
    MESSAGES_URL,
    MESSAGE_URL,
    agent_i_auth_headers,
    pick_conversation_id,
)

logger = logging.getLogger(__name__)

PDF_RESULT_KEY = "译文PDF"
VALID_DIRECTIONS = {"en_to_zh_tw", "zh_tw_to_en"}
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 30
MAX_PAGE_IMAGE_BYTES = int(9.5 * 1024 * 1024)
MAX_OUTPUT_IMAGE_BYTES = 20 * 1024 * 1024
RENDER_DPIS = (150, 120, 96)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_AGENT_ATTEMPTS = 3
PAGE_TIMEOUT_SECONDS = 300.0
_OFFICIAL_IMAGE_HOST_SUFFIXES = ("gptbots.ai", "gptbots.com", "srcgptbots.com")


@dataclass
class Case4PdfDocument:
    filename: str
    pdf_bytes: bytes
    direction: str
    page_count: int
    page_sizes: list[tuple[float, float]]
    created_at: datetime = field(default_factory=datetime.utcnow)


def validate_case4_pdf(pdf_bytes: bytes, filename: str, direction: str) -> Case4PdfDocument:
    """Validate one Case 4 upload and retain immutable source metadata."""
    if direction not in VALID_DIRECTIONS:
        raise ValueError("翻译方向无效，请选择英文转繁体中文或繁体中文转英文")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("PDF 文件不能超过 25 MB")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("上传内容不是有效的 PDF 文件")
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF 文件已损坏或无法读取") from exc
    try:
        if document.needs_pass:
            raise ValueError("不支持加密或受密码保护的 PDF 文件")
        if document.page_count < 1:
            raise ValueError("PDF 文件不包含任何页面")
        if document.page_count > MAX_PAGES:
            raise ValueError(f"PDF 文件最多支持 {MAX_PAGES} 页")
        page_sizes = [
            (float(document[index].rect.width), float(document[index].rect.height))
            for index in range(document.page_count)
        ]
    finally:
        document.close()
    return Case4PdfDocument(
        filename=filename or "document.pdf",
        pdf_bytes=pdf_bytes,
        direction=direction,
        page_count=len(page_sizes),
        page_sizes=page_sizes,
    )


def _default_page_renderer(page: fitz.Page, dpi: int) -> bytes:
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
    return pixmap.tobytes("png")


def render_page_png(
    page: fitz.Page,
    max_bytes: int = MAX_PAGE_IMAGE_BYTES,
    renderer: Callable[[fitz.Page, int], bytes] = _default_page_renderer,
) -> tuple[bytes, int]:
    """Render at 150 DPI, then 120/96 only when the image is too large."""
    for dpi in RENDER_DPIS:
        image_bytes = renderer(page, dpi)
        if len(image_bytes) <= max_bytes:
            return image_bytes, dpi
    raise ValueError("页面图片即使降至 96 DPI 仍超过 9.5 MB，无法发送给 Agent I")


def build_agent_i_payload(
    conversation_id: str,
    png_bytes: bytes,
    direction: str,
    page_number: int,
    total_pages: int,
) -> dict[str, Any]:
    labels = {
        "en_to_zh_tw": (
            "ENGLISH_TO_TRADITIONAL_CHINESE (en_to_zh_tw). "
            "Translate every readable English element into formal Hong Kong Traditional Chinese."
        ),
        "zh_tw_to_en": (
            "TRADITIONAL_CHINESE_TO_ENGLISH (zh_tw_to_en). "
            "Translate every readable Traditional Chinese element into formal legal English."
        ),
    }
    if direction not in labels:
        raise ValueError("翻译方向无效")
    instruction = (
        f"Translation direction: {labels[direction]} "
        f"Page {page_number} of {total_pages}. Treat this image as the complete and only "
        "source page. Preserve exact structural counts and return exactly one complete "
        "translated page image."
    )
    return {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image", "image": [{
                    "base64_content": base64.b64encode(png_bytes).decode("ascii"),
                    "format": "png",
                    "name": f"page_{page_number:04d}.png",
                }]},
            ],
        }],
        "conversation_config": {
            "short_term_memory": False,
            "long_term_memory": False,
        },
    }


def _normalise_image_reference(value: Any) -> str | dict[str, Any] | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        if value.get("base64_content"):
            return value
        if value.get("url"):
            return str(value["url"])
        for key in ("image", "images"):
            nested = _normalise_image_reference(value.get(key))
            if nested is not None:
                return nested
    if isinstance(value, list):
        for item in value:
            nested = _normalise_image_reference(item)
            if nested is not None:
                return nested
    return None


def extract_blocking_image_reference(data: dict[str, Any]) -> str | dict[str, Any] | None:
    """Extract a directly returned URL or base64 image from a blocking response."""
    for output in data.get("output", []) if isinstance(data, dict) else []:
        if not isinstance(output, dict):
            continue
        content = output.get("content")
        if isinstance(content, dict):
            for key in ("image", "images", "base64_content", "url"):
                reference = _normalise_image_reference(content.get(key))
                if reference is not None:
                    return reference
    return None


def extract_assistant_image_reference(
    data: dict[str, Any],
    message_id: str | None = None,
) -> str | dict[str, Any] | None:
    """Extract only an Assistant image, never the user's source upload."""
    turns = data.get("conversation_content", []) if isinstance(data, dict) else []
    assistant_turns = [
        turn for turn in turns
        if isinstance(turn, dict) and turn.get("role") == "assistant"
    ]
    if message_id:
        matching = [
            turn for turn in assistant_turns
            if str(turn.get("message_id")) == message_id
        ]
        if matching:
            assistant_turns = matching
    for turn in assistant_turns:
        for content in turn.get("content", []):
            if not isinstance(content, dict):
                continue
            for branch in content.get("branch_content", []):
                if isinstance(branch, dict) and branch.get("type") == "image":
                    reference = _normalise_image_reference(branch.get("image"))
                    if reference is not None:
                        return reference
    return None


def _decode_base64(value: str) -> bytes:
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Agent I 返回的图片 base64 无效") from exc
    if len(data) > MAX_OUTPUT_IMAGE_BYTES:
        raise ValueError("Agent I 返回的图片超过 20 MB")
    return data


def _validate_image_bytes(data: bytes) -> bytes:
    if not data:
        raise ValueError("Agent I 返回了空图片")
    if len(data) > MAX_OUTPUT_IMAGE_BYTES:
        raise ValueError("Agent I 返回的图片超过 20 MB")
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = data.startswith(b"\xff\xd8\xff")
    is_webp = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if not (is_png or is_jpeg or is_webp):
        raise ValueError("Agent I 返回的文件不是受支持的 PNG/JPEG/WebP 图片")
    try:
        pixmap = fitz.Pixmap(data)
        if pixmap.width < 1 or pixmap.height < 1:
            raise ValueError("Agent I 返回的图片尺寸无效")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Agent I 返回的图片无法解码") from exc
    return data


def _is_official_image_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in _OFFICIAL_IMAGE_HOST_SUFFIXES
    )


def _candidate_image_urls(url: str) -> list[str]:
    """Return the full GPTBots asset first, with its thumbnail as fallback.

    Conversation history exposes generated images through a ``/thumbnail/``
    path. The same official resource without that path segment is the original
    image and contains substantially more pixels.
    """
    if not _is_official_image_url(url):
        return [url]
    parts = urlsplit(url)
    if "/thumbnail/" not in parts.path:
        return [url]
    original_path = parts.path.replace("/thumbnail/", "/", 1)
    original_url = urlunsplit(
        (parts.scheme, parts.netloc, original_path, parts.query, parts.fragment)
    )
    return [original_url, url]


async def _image_bytes_from_reference(
    client: httpx.AsyncClient,
    reference: str | dict[str, Any],
) -> bytes:
    if isinstance(reference, dict):
        if reference.get("base64_content"):
            return _validate_image_bytes(_decode_base64(str(reference["base64_content"])))
        reference = str(reference.get("url") or "")
    if reference.startswith("data:"):
        return _validate_image_bytes(_decode_base64(reference))
    if not reference.startswith("https://"):
        return _validate_image_bytes(_decode_base64(reference))
    if not _is_official_image_url(reference):
        raise ValueError("Agent I 图片地址不是 GPTBots 官方 HTTPS 域名")
    last_error: Exception | None = None
    for candidate_url in _candidate_image_urls(reference):
        try:
            response = await client.get(
                candidate_url,
                follow_redirects=True,
                timeout=60.0,
            )
            response.raise_for_status()
            if int(response.headers.get("content-length") or 0) > MAX_OUTPUT_IMAGE_BYTES:
                raise ValueError("Agent I 返回的图片超过 20 MB")
            return _validate_image_bytes(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            if candidate_url == reference:
                raise
            logger.warning("Agent I 原始译图下载失败，将回退缩略图：%s", type(exc).__name__)
    raise ValueError("Agent I 译图下载失败") from last_error


def assemble_translated_pdf(
    translated_images: list[bytes],
    page_sizes: list[tuple[float, float]],
) -> bytes:
    """Place translated images on white pages using original page dimensions."""
    if len(translated_images) != len(page_sizes) or not translated_images:
        raise ValueError("译图数量与原 PDF 页数不一致")
    output = fitz.open()
    try:
        for image_bytes, (page_width, page_height) in zip(translated_images, page_sizes):
            pixmap = fitz.Pixmap(image_bytes)
            image_ratio = pixmap.width / pixmap.height
            page_ratio = page_width / page_height
            if image_ratio >= page_ratio:
                target_width = page_width
                target_height = page_width / image_ratio
            else:
                target_height = page_height
                target_width = page_height * image_ratio
            left = (page_width - target_width) / 2
            top = (page_height - target_height) / 2
            page = output.new_page(width=page_width, height=page_height)
            page.draw_rect(page.rect, color=None, fill=(1, 1, 1), overlay=False)
            page.insert_image(
                fitz.Rect(left, top, left + target_width, top + target_height),
                stream=image_bytes,
                keep_proportion=True,
            )
        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_AGENT_ATTEMPTS):
        try:
            response = await client.post(
                url, headers=headers, json=json, timeout=PAGE_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code not in RETRYABLE_STATUS or attempt == MAX_AGENT_ATTEMPTS - 1:
                raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt == MAX_AGENT_ATTEMPTS - 1:
                raise
        await asyncio.sleep(2**attempt)
    raise RuntimeError("Agent I 请求失败") from last_error


class Case4PdfTranslationHandler(CaseHandler):
    """Translate every page serially and expose one downloadable PDF."""

    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        documents = session_store.get(session_id)
        if not documents or not isinstance(documents[0], Case4PdfDocument):
            raise ValueError("Case 4 会话不存在或已过期，请重新上传 PDF")
        if not AGENT_I_API_KEY:
            raise ValueError("Case 4 暂不可用：未配置 AGENT_I_API_KEY")
        source = documents[0]
        headers = agent_i_auth_headers()

        async with httpx.AsyncClient(trust_env=False, timeout=PAGE_TIMEOUT_SECONDS) as client:
            create = await _post_with_retry(
                client,
                CREATE_URL,
                headers=headers,
                json={"user_id": f"case4_{session_id}"},
            )
            conversation_id = pick_conversation_id(create.json())
            if not conversation_id:
                raise ValueError("创建 Agent I 对话失败：响应中没有 conversation_id")

            translated_images: list[bytes] = []
            pdf = fitz.open(stream=source.pdf_bytes, filetype="pdf")
            try:
                for index in range(source.page_count):
                    page_number = index + 1
                    yield SseEvent("progress", {
                        "stage": "split", "page": page_number, "total": source.page_count,
                        "message": f"正在拆分第 {page_number}/{source.page_count} 页...",
                    })
                    png_bytes, dpi = render_page_png(pdf[index])
                    yield SseEvent("progress", {
                        "stage": "translate", "page": page_number, "total": source.page_count,
                        "dpi": dpi, "status": "processing",
                        "message": f"Agent I 正在翻译第 {page_number}/{source.page_count} 页...",
                    })
                    response = await _post_with_retry(
                        client,
                        MESSAGE_URL,
                        headers=headers,
                        json=build_agent_i_payload(
                            conversation_id, png_bytes, source.direction,
                            page_number, source.page_count,
                        ),
                    )
                    response_data = response.json()
                    reference = extract_blocking_image_reference(response_data)
                    message_id = str(response_data.get("message_id") or "") or None
                    if reference is None:
                        for _ in range(5):
                            details = await client.get(
                                MESSAGES_URL,
                                headers=headers,
                                params={"conversation_id": conversation_id, "page": 1, "page_size": 100},
                                timeout=60.0,
                            )
                            details.raise_for_status()
                            reference = extract_assistant_image_reference(
                                details.json(), message_id=message_id
                            )
                            if reference is not None:
                                break
                            await asyncio.sleep(1)
                    if reference is None:
                        raise ValueError(f"第 {page_number} 页：Agent I 未返回译图")
                    translated_images.append(
                        await _image_bytes_from_reference(client, reference)
                    )
                    yield SseEvent("progress", {
                        "stage": "translate", "page": page_number, "total": source.page_count,
                        "dpi": dpi, "status": "done",
                        "message": f"第 {page_number}/{source.page_count} 页翻译完成",
                    })
            except Exception as exc:
                raise ValueError(f"PDF 翻译失败，未生成部分文件：{exc}") from exc
            finally:
                pdf.close()

        yield SseEvent("progress", {
            "stage": "merge", "page": source.page_count, "total": source.page_count,
            "message": "正在按原始页序合并译文 PDF...",
        })
        translated_pdf = assemble_translated_pdf(translated_images, source.page_sizes)
        download_name = f"{Path(source.filename).stem}_译文.pdf"
        session_results_store[session_id] = {
            "case_type": "case4",
            "created_at": datetime.utcnow(),
            "results": {PDF_RESULT_KEY: {
                "pdf_bytes": translated_pdf,
                "filename": download_name,
                "conversation_id": conversation_id,
                "page_count": source.page_count,
                "direction": source.direction,
            }},
        }
        yield SseEvent("result", {
            "result_key": PDF_RESULT_KEY,
            "conversation_id": conversation_id,
            "page_count": source.page_count,
            "filename": download_name,
            "message": "译文 PDF 已生成",
        })

    async def generate_report(
        self,
        session_id: str,
        result_key: str,
        output_format: str,
        session_results_store: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        if output_format.lower() != "pdf":
            raise ValueError("Case 4 只提供 PDF 下载")
        if result_key != PDF_RESULT_KEY:
            raise ValueError("无效的 Case 4 下载结果")
        result = session_results_store.get(session_id, {}).get("results", {}).get(result_key)
        if not result:
            raise ValueError("译文 PDF 尚未生成或已过期")
        return result["pdf_bytes"], result["filename"], "application/pdf"

    async def followup_chat(
        self,
        session_id: str,
        text: str,
        conversation_id: str | None,
        session_results_store: dict[str, Any],
    ) -> dict[str, Any]:
        raise ValueError("Case 4 不支持追问，请下载译文 PDF")

    def get_downloadable_keys(
        self,
        session_id: str,
        session_results_store: dict[str, Any],
    ) -> list[str]:
        results = session_results_store.get(session_id, {}).get("results", {})
        return [PDF_RESULT_KEY] if PDF_RESULT_KEY in results else []
