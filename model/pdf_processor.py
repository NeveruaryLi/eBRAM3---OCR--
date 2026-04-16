"""
PDF 工具模块

使用 PyMuPDF (fitz) 读取 PDF 元信息及原生文字。
OCR 识别由飞桨 PaddleOCR 官方 API 处理。
"""

import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def get_page_count(pdf_bytes: bytes) -> int:
    """
    返回 PDF 的总页数。

    Args:
        pdf_bytes: PDF 文件的原始字节内容。

    Returns:
        整数页数。

    Raises:
        ValueError: 无法解析为有效 PDF。
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return len(doc)
    except Exception as exc:
        raise ValueError(f"无法解析 PDF：{exc}") from exc


def extract_native_text(pdf_bytes: bytes) -> list[str]:
    """
    用 PyMuPDF 提取每页的原生文字（适用于可选字 PDF）。
    纯扫描件返回空字符串。

    Args:
        pdf_bytes: PDF 文件的原始字节内容。

    Returns:
        list[str]，每个元素为对应页的原生文字（已去首尾空白）。
        若解析失败则返回空列表。
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            results: list[str] = []
            for page in doc:
                text = page.get_text("text").strip()
                results.append(text)
            return results
    except Exception as exc:
        logger.warning("PyMuPDF 原生文字提取失败：%s", exc)
        return []
