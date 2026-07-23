"""Case 6B — evidence-grounded service agreement drafting."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import fitz
import httpx
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from cases.base import CaseHandler, SseEvent
from model.config import (
    AGENT_L_API_KEY,
    CREATE_URL,
    MESSAGES_URL,
    MESSAGE_URL,
    agent_l_auth_headers,
    auth_headers,
    pick_conversation_id,
)
from model.pdf_processor import extract_native_text
from model.utils import extract_gptbots_reply

logger = logging.getLogger(__name__)

RESULT_KEY = "协议草案"
MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
MAX_MATERIAL_BYTES = 25 * 1024 * 1024
MAX_XLSX_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_MATERIALS = 20
MAX_PDF_PAGES = 30
MAX_OCR_UNITS = 60
MAX_XLSX_SHEETS = 20
MAX_XLSX_CELLS = 10_000
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
SIGNATURE_KINDS = {"signature_name", "signature", "signature_date"}
ALLOWED_AGENT_STATUSES = {
    "FILLED",
    "NEEDS_CONFIRMATION",
    "REMOVE",
    "LEAVE_BLANK",
}
_BLANK_RE = re.compile(r"_{4,}")
_LOCATOR_RE = re.compile(
    r"^(?:paragraph:(?P<paragraph>\d+)|"
    r"table:(?P<table>\d+)/row:(?P<row>\d+)/cell:(?P<cell>\d+)/paragraph:(?P<cellpara>\d+))"
    r"/blank:(?P<blank>\d+)$"
)


@dataclass
class TemplateRecord:
    filename: str
    content: bytes
    language: str = "en"


@dataclass
class MaterialRecord:
    material_id: str
    filename: str
    file_type: str
    content: bytes
    units: int
    status: str = "pending"
    facts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    conversation_id: str | None = None


@dataclass
class Case6BSession:
    template: TemplateRecord
    materials: list[MaterialRecord]
    field_manifest: dict[str, Any]
    fields: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    full_summary: dict[str, Any] | None = None
    review_version: int = 0
    generated_docx: bytes | None = None
    generated_pdf: bytes | None = None
    pdf_error: str | None = None
    agent_l_conversation_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def _open_docx(content: bytes) -> Document:
    if len(content) > MAX_TEMPLATE_BYTES:
        raise ValueError("DOCX 模板不能超过 10 MB")
    if not content.startswith(b"PK"):
        raise ValueError("模板不是有效的 DOCX 文件")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("DOCX 模板解压后体积过大")
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise ValueError("模板不是有效的 DOCX 文件")
            xml = archive.read("word/document.xml")
            unsupported = (
                (b"<w:sdt" in xml, "Word 内容控件"),
                (b"<w:txbxContent" in xml, "文本框"),
                (b"MERGEFIELD" in xml, "邮件合并域"),
            )
            for present, label in unsupported:
                if present:
                    raise ValueError(f"V1 暂不支持包含{label}的 DOCX 模板")
        return Document(io.BytesIO(content))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("DOCX 模板已损坏或无法读取") from exc


def validate_template(content: bytes, filename: str) -> TemplateRecord:
    if not filename.lower().endswith(".docx"):
        raise ValueError("模板仅支持 DOCX 格式，请先将 PDF 或 DOC 转换为 DOCX")
    document = _open_docx(content)
    text_parts = [paragraph.text for paragraph in document.paragraphs]
    text_parts.extend(
        paragraph.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    )
    if not any(_BLANK_RE.search(text) for text in text_parts):
        raise ValueError("DOCX 模板中没有可识别的连续下划线占位符")
    text = "\n".join(text_parts)
    chinese_chars = len(re.findall(r"[\u3400-\u9fff]", text))
    language = "zh_tw" if chinese_chars > max(20, len(text) // 8) else "en"
    return TemplateRecord(filename=filename, content=content, language=language)


def _placeholder_context(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 55)
    end = min(len(text), match.end() + 55)
    return text[start:match.start()] + "[blank]" + text[match.end():end]


def _field_hint(text: str, blank_index: int, group_index: int | None) -> dict[str, Any]:
    lowered = text.lower()
    if group_index is not None:
        is_price = blank_index == 2
        return {
            "field_kind": "repeatable_price" if is_price else "repeatable_service",
            "group_key": "services",
            "group_index": group_index,
            "required": False,
            "resolution_policy": "remove_if_unused",
        }
    if "signature" in lowered:
        kind = "signature"
    elif ("service provider" in lowered or "client" in lowered) and "name:" in lowered:
        kind = "signature_name"
    elif "date:" in lowered:
        kind = "signature_date"
    else:
        kind = "scalar"
    return {
        "field_kind": kind,
        "group_key": None,
        "group_index": None,
        "required": kind not in SIGNATURE_KINDS,
        "resolution_policy": "leave_blank" if kind in SIGNATURE_KINDS else "fill",
    }


def extract_template_manifest(content: bytes) -> dict[str, Any]:
    document = _open_docx(content)
    fields: list[dict[str, Any]] = []
    service_index = 0
    for paragraph_index, paragraph in enumerate(document.paragraphs):
        matches = list(_BLANK_RE.finditer(paragraph.text))
        if not matches:
            continue
        is_service = len(matches) == 2 and "price" in paragraph.text.lower()
        if is_service:
            service_index += 1
        for blank_index, match in enumerate(matches, 1):
            hint = _field_hint(
                paragraph.text,
                blank_index,
                service_index if is_service else None,
            )
            fields.append(
                {
                    "field_id": f"p{paragraph_index:03d}_f{blank_index:02d}",
                    "locator": f"paragraph:{paragraph_index}/blank:{blank_index}",
                    "context": _placeholder_context(paragraph.text, match),
                    **hint,
                }
            )
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                for paragraph_index, paragraph in enumerate(cell.paragraphs):
                    for blank_index, match in enumerate(
                        _BLANK_RE.finditer(paragraph.text), 1
                    ):
                        hint = _field_hint(paragraph.text, blank_index, None)
                        fields.append(
                            {
                                "field_id": (
                                    f"t{table_index:03d}_r{row_index:03d}_"
                                    f"c{cell_index:03d}_p{paragraph_index:03d}_"
                                    f"f{blank_index:02d}"
                                ),
                                "locator": (
                                    f"table:{table_index}/row:{row_index}/"
                                    f"cell:{cell_index}/paragraph:{paragraph_index}/"
                                    f"blank:{blank_index}"
                                ),
                                "context": _placeholder_context(
                                    paragraph.text, match
                                ),
                                **hint,
                            }
                        )
    if not fields:
        raise ValueError("DOCX 模板中没有可识别的连续下划线占位符")
    if len(fields) > 200:
        raise ValueError("DOCX 模板最多支持 200 个下划线占位符")
    return {"template_language": "en", "fields": fields}


def _validate_pdf(content: bytes) -> int:
    if not content.startswith(b"%PDF-"):
        raise ValueError("文件内容不是有效的 PDF")
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF 文件已损坏或无法读取") from exc
    try:
        if document.needs_pass:
            raise ValueError("不支持加密或受密码保护的 PDF")
        if document.page_count < 1:
            raise ValueError("PDF 文件不包含页面")
        if document.page_count > MAX_PDF_PAGES:
            raise ValueError(f"单个 PDF 最多支持 {MAX_PDF_PAGES} 页")
        return document.page_count
    finally:
        document.close()


def _validate_image(content: bytes) -> None:
    try:
        pixmap = fitz.Pixmap(content)
        if pixmap.width < 1 or pixmap.height < 1:
            raise ValueError("图片尺寸无效")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("图片已损坏或无法读取") from exc


def _workbook_info(content: bytes) -> tuple[int, int]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("XLSX 解压后体积过大")
    except zipfile.BadZipFile as exc:
        raise ValueError("XLSX 文件已损坏或无法读取") from exc
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl，无法读取 XLSX 材料") from exc
    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except Exception as exc:
        raise ValueError("XLSX 文件已损坏或无法读取") from exc
    try:
        sheets = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        if not sheets:
            raise ValueError("XLSX 不包含可见工作表")
        if len(sheets) > MAX_XLSX_SHEETS:
            raise ValueError(f"XLSX 最多支持 {MAX_XLSX_SHEETS} 个可见工作表")
        cells = 0
        nonempty_sheets = 0
        for sheet in sheets:
            sheet_cells = sum(
                1
                for row in sheet.iter_rows()
                for cell in row
                if cell.value not in (None, "")
            )
            cells += sheet_cells
            nonempty_sheets += int(sheet_cells > 0)
            if cells > MAX_XLSX_CELLS:
                raise ValueError(f"XLSX 最多支持 {MAX_XLSX_CELLS} 个有效单元格")
        if not cells:
            raise ValueError("XLSX 不包含可读取的数据")
        return nonempty_sheets, cells
    finally:
        workbook.close()


def validate_material(content: bytes, filename: str) -> MaterialRecord:
    suffix = Path(filename).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg", ".jfif", ".xlsx"}
    if suffix not in allowed:
        raise ValueError("材料仅支持 PDF、PNG、JPG、JPEG、JFIF 或 XLSX")
    max_bytes = MAX_XLSX_BYTES if suffix == ".xlsx" else MAX_MATERIAL_BYTES
    if len(content) > max_bytes:
        limit = "10 MB" if suffix == ".xlsx" else "25 MB"
        raise ValueError(f"材料《{filename}》不能超过 {limit}")
    if not content:
        raise ValueError(f"材料《{filename}》为空")
    if suffix == ".pdf":
        units = _validate_pdf(content)
        file_type = "pdf"
    elif suffix == ".xlsx":
        if not content.startswith(b"PK"):
            raise ValueError("文件内容不是有效的 XLSX")
        units, _ = _workbook_info(content)
        file_type = "xlsx"
    else:
        _validate_image(content)
        units = 1
        file_type = "image"
    return MaterialRecord(
        material_id=uuid.uuid4().hex,
        filename=filename,
        file_type=file_type,
        content=content,
        units=units,
    )


def image_to_pdf(content: bytes) -> bytes:
    pixmap = fitz.Pixmap(content)
    width = float(pixmap.width)
    height = float(pixmap.height)
    document = fitz.open()
    try:
        page = document.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=content, keep_proportion=True)
        return document.tobytes(deflate=True)
    finally:
        document.close()


def ocr_submission_filename(filename: str, file_type: str) -> str:
    """PaddleOCR validates the uploaded filename in addition to its MIME type."""
    if file_type == "image":
        return f"{Path(filename).stem}.pdf"
    return filename


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def extract_xlsx_markdown(content: bytes, filename: str) -> tuple[str, int]:
    from openpyxl import load_workbook

    workbook = load_workbook(
        io.BytesIO(content),
        read_only=True,
        data_only=False,
        keep_links=False,
    )
    sections: list[str] = [f"# {filename}"]
    units = 0
    total_cells = 0
    try:
        for sheet in workbook.worksheets:
            if sheet.sheet_state != "visible":
                continue
            lines: list[str] = []
            for row in sheet.iter_rows():
                cells = []
                for cell in row:
                    if cell.value in (None, ""):
                        continue
                    total_cells += 1
                    if total_cells > MAX_XLSX_CELLS:
                        raise ValueError(
                            f"XLSX 最多支持 {MAX_XLSX_CELLS} 个有效单元格"
                        )
                    cells.append(f"{cell.coordinate}: {_format_cell(cell.value)}")
                if cells:
                    lines.append(" | ".join(cells))
            if lines:
                units += 1
                sections.extend((f"\n## Sheet: {sheet.title}", *lines))
    finally:
        workbook.close()
    if not units:
        raise ValueError("XLSX 不包含可读取的数据")
    return "\n".join(sections), units


def parse_agent_json(text: str) -> dict[str, Any]:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL)
    if fenced:
        value = fenced.group(1)
    else:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Agent 返回的 JSON 格式无效") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Agent 必须返回 JSON Object")
    if isinstance(parsed.get("error"), dict):
        raise ValueError(parsed["error"].get("message") or "Agent 拒绝了输入")
    return parsed


def build_agent_l_template_payload(
    conversation_id: str,
    template: TemplateRecord,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    text = (
        "[CASE6B_PHASE:TEMPLATE_PARSE]\n"
        "Parse the attached DOCX using this application-generated placeholder manifest.\n"
        f"template_placeholders:\n{json.dumps(manifest, ensure_ascii=False)}"
    )
    return {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "document",
                        "document": [
                            {
                                "base64_content": base64.b64encode(
                                    template.content
                                ).decode("ascii"),
                                "format": "docx",
                                "name": template.filename,
                            }
                        ],
                    },
                ],
            }
        ],
        "conversation_config": {
            "short_term_memory": True,
            "long_term_memory": False,
        },
    }


def build_agent_l_fill_payload(
    conversation_id: str,
    field_list: dict[str, Any],
    full_summary: dict[str, Any],
) -> dict[str, Any]:
    text = (
        "[CASE6B_PHASE:FIELD_FILL]\n"
        f"field_list:\n{json.dumps(field_list, ensure_ascii=False)}\n"
        f"full_summary:\n{json.dumps(full_summary, ensure_ascii=False)}"
    )
    return {
        "conversation_id": conversation_id,
        "response_mode": "blocking",
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        "conversation_config": {
            "short_term_memory": True,
            "long_term_memory": False,
        },
    }


def _paragraph_for_locator(document: Document, locator: str):
    match = _LOCATOR_RE.match(locator)
    if not match:
        raise ValueError(f"无效的模板字段定位：{locator}")
    if match.group("paragraph") is not None:
        paragraph = document.paragraphs[int(match.group("paragraph"))]
    else:
        paragraph = (
            document.tables[int(match.group("table"))]
            .rows[int(match.group("row"))]
            .cells[int(match.group("cell"))]
            .paragraphs[int(match.group("cellpara"))]
        )
    return paragraph, int(match.group("blank"))


def _set_highlight(run) -> None:
    run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    rpr = run._r.get_or_add_rPr()
    highlight = rpr.find(qn("w:highlight"))
    if highlight is None:
        highlight = OxmlElement("w:highlight")
        rpr.append(highlight)
    highlight.set(qn("w:val"), "yellow")


def _replace_blank(paragraph, blank_index: int, replacement: str, highlight: bool) -> None:
    runs = list(paragraph.runs)
    text = "".join(run.text for run in runs)
    matches = list(_BLANK_RE.finditer(text))
    if blank_index < 1 or blank_index > len(matches):
        raise ValueError("模板占位符数量已变化，无法安全回填")
    target = matches[blank_index - 1]
    offsets: list[tuple[int, int, Any]] = []
    cursor = 0
    for run in runs:
        offsets.append((cursor, cursor + len(run.text), run))
        cursor += len(run.text)
    start_entry = next(item for item in offsets if item[0] <= target.start() < item[1])
    end_entry = next(item for item in offsets if item[0] < target.end() <= item[1])
    start_run = start_entry[2]
    end_run = end_entry[2]
    before = start_run.text[: target.start() - start_entry[0]]
    after = end_run.text[target.end() - end_entry[0] :]

    start_run.text = before
    replacement_xml = deepcopy(start_run._r)
    for child in list(replacement_xml):
        if child.tag != qn("w:rPr"):
            replacement_xml.remove(child)
    text_node = OxmlElement("w:t")
    text_node.set(qn("xml:space"), "preserve")
    text_node.text = replacement
    replacement_xml.append(text_node)
    start_run._r.addnext(replacement_xml)
    replacement_run = next(
        run for run in paragraph.runs if run._r is replacement_xml
    )
    if highlight:
        _set_highlight(replacement_run)

    if start_run is end_run:
        after_xml = deepcopy(start_run._r)
        for child in list(after_xml):
            if child.tag != qn("w:rPr"):
                after_xml.remove(child)
        after_node = OxmlElement("w:t")
        after_node.set(qn("xml:space"), "preserve")
        after_node.text = after
        after_xml.append(after_node)
        replacement_xml.addnext(after_xml)
    else:
        started = False
        for _, _, run in offsets:
            if run is start_run:
                started = True
                continue
            if not started:
                continue
            if run is end_run:
                run.text = after
                break
            run.text = ""


def render_draft_docx(
    template_bytes: bytes,
    manifest: dict[str, Any],
    fields: list[dict[str, Any]],
    template_language: str,
) -> bytes:
    document = _open_docx(template_bytes)
    manifest_by_id = {item["field_id"]: item for item in manifest["fields"]}
    fields_by_id = {item["field_id"]: item for item in fields}
    if set(fields_by_id) != set(manifest_by_id):
        raise ValueError("字段结果与模板占位符不一致")

    remove_paragraphs: set[int] = set()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for definition in manifest["fields"]:
        group_index = definition.get("group_index")
        if group_index is not None:
            grouped.setdefault(int(group_index), []).append(
                fields_by_id[definition["field_id"]]
            )
    for group_index, group_fields in grouped.items():
        statuses = {item["status"] for item in group_fields}
        if statuses == {"REMOVE"}:
            locator = next(
                item["locator"]
                for item in manifest["fields"]
                if item.get("group_index") == group_index
            )
            match = _LOCATOR_RE.match(locator)
            if match and match.group("paragraph") is not None:
                remove_paragraphs.add(int(match.group("paragraph")))
        elif "REMOVE" in statuses:
            raise ValueError("服务名称和价格必须成对保留或删除")

    replacements: dict[int, list[tuple[int, str, bool]]] = {}
    table_replacements: list[tuple[str, str, bool]] = []
    marker = "[待確認]" if template_language == "zh_tw" else "[TO BE CONFIRMED]"
    for field_id, definition in manifest_by_id.items():
        result = fields_by_id[field_id]
        status = result["status"]
        if status in {"LEAVE_BLANK", "REMOVE"}:
            continue
        replacement = result.get("value", "") if status in {"FILLED", "USER_CONFIRMED"} else marker
        highlight = status == "NEEDS_CONFIRMATION"
        match = _LOCATOR_RE.match(definition["locator"])
        if not match:
            raise ValueError("模板字段定位无效")
        if match.group("paragraph") is not None:
            paragraph_index = int(match.group("paragraph"))
            replacements.setdefault(paragraph_index, []).append(
                (int(match.group("blank")), replacement, highlight)
            )
        else:
            table_replacements.append(
                (definition["locator"], replacement, highlight)
            )

    for paragraph_index, values in replacements.items():
        paragraph = document.paragraphs[paragraph_index]
        for blank_index, replacement, highlight in sorted(values, reverse=True):
            _replace_blank(paragraph, blank_index, replacement, highlight)
    for locator, replacement, highlight in table_replacements:
        paragraph, blank_index = _paragraph_for_locator(document, locator)
        _replace_blank(paragraph, blank_index, replacement, highlight)
    for paragraph_index in sorted(remove_paragraphs, reverse=True):
        paragraph = document.paragraphs[paragraph_index]
        paragraph._element.getparent().remove(paragraph._element)

    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def apply_review_changes(
    session: Case6BSession,
    version: int,
    field_updates: list[dict[str, Any]],
    service_rows: list[dict[str, Any]],
) -> int:
    if version != session.review_version:
        raise ValueError("审阅内容已更新，请刷新后再提交")
    fields_by_id = {field["field_id"]: field for field in session.fields}
    manifest_by_id = {
        field["field_id"]: field for field in session.field_manifest["fields"]
    }
    for update in field_updates:
        field_id = str(update.get("field_id", ""))
        if field_id not in fields_by_id or field_id not in manifest_by_id:
            raise ValueError(f"未知字段：{field_id}")
        if manifest_by_id[field_id].get("field_kind") in SIGNATURE_KINDS:
            raise ValueError("签名及签署日期字段必须保持空白")
        if manifest_by_id[field_id].get("group_key") == "services":
            raise ValueError("服务字段必须通过 service_rows 成对更新")
        value = str(update.get("value", "")).strip()
        if value == str(fields_by_id[field_id].get("value", "")).strip():
            continue
        fields_by_id[field_id].setdefault("original", deepcopy(fields_by_id[field_id]))
        fields_by_id[field_id]["status"] = (
            "USER_CONFIRMED" if value else "NEEDS_CONFIRMATION"
        )
        fields_by_id[field_id]["value"] = value
        fields_by_id[field_id]["evidence"] = (
            [{"source": "User confirmation", "fact": value}] if value else []
        )
    for row in service_rows:
        group_index = int(row.get("group_index", 0))
        definitions = [
            definition
            for definition in session.field_manifest["fields"]
            if definition.get("group_key") == "services"
            and definition.get("group_index") == group_index
        ]
        if len(definitions) != 2:
            raise ValueError(f"未知服务行：{group_index}")
        action = row.get("action")
        name = str(row.get("name", "")).strip()
        price = str(row.get("price", "")).strip()
        if action == "remove":
            status = "REMOVE"
            name = price = ""
        elif action == "keep" and name and price:
            status = "USER_CONFIRMED"
        else:
            raise ValueError("保留服务行时必须同时填写服务名称和价格")
        current_values = {
            definition["field_kind"]: fields_by_id[definition["field_id"]]
            for definition in definitions
        }
        current_action = (
            "remove"
            if all(item.get("status") == "REMOVE" for item in current_values.values())
            else "keep"
        )
        if (
            action == current_action
            and name
            == str(current_values["repeatable_service"].get("value", "")).strip()
            and price
            == str(current_values["repeatable_price"].get("value", "")).strip()
        ):
            continue
        for definition in definitions:
            target = fields_by_id[definition["field_id"]]
            target.setdefault("original", deepcopy(target))
            target["status"] = status
            target["value"] = (
                price if definition["field_kind"] == "repeatable_price" else name
            )
            target["evidence"] = (
                [{"source": "User confirmation", "fact": target["value"]}]
                if status == "USER_CONFIRMED"
                else []
            )
    session.review_version += 1
    session.generated_docx = None
    session.generated_pdf = None
    session.pdf_error = None
    session.updated_at = datetime.utcnow()
    return session.review_version


def review_payload(session: Case6BSession) -> dict[str, Any]:
    manifest = {
        field["field_id"]: field for field in session.field_manifest.get("fields", [])
    }
    fields = []
    for value in session.fields:
        definition = manifest.get(value["field_id"], {})
        fields.append(
            {
                **value,
                "label": definition.get("label")
                or definition.get("semantic_key")
                or value["field_id"],
                "semantic_key": definition.get("semantic_key", ""),
                "field_kind": definition.get("field_kind", "scalar"),
                "group_key": definition.get("group_key"),
                "group_index": definition.get("group_index"),
                "required": bool(definition.get("required")),
                "editable": definition.get("field_kind") not in SIGNATURE_KINDS,
            }
        )
    return {
        "version": session.review_version,
        "template_language": session.template.language,
        "fields": fields,
        "conflicts": session.conflicts,
        "unresolved_count": sum(
            1
            for field in fields
            if field["required"] and field["status"] == "NEEDS_CONFIRMATION"
        ),
        "docx_ready": session.generated_docx is not None,
        "pdf_ready": session.generated_pdf is not None,
        "pdf_error": session.pdf_error,
    }


def convert_docx_to_pdf(docx_bytes: bytes, filename: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ebram_case6b_") as temp_dir:
        directory = Path(temp_dir)
        safe_name = Path(filename).name or "service-agreement.docx"
        source = directory / safe_name
        source.write_bytes(docx_bytes)
        candidates = [
            shutil.which("soffice"),
            r"C:\Program Files\LibreOffice\program\soffice.exe",
        ]
        for candidate in candidates:
            if not candidate or not Path(candidate).exists():
                continue
            try:
                subprocess.run(
                    [
                        str(candidate),
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(directory),
                        str(source),
                    ],
                    check=True,
                    timeout=120,
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = source.with_suffix(".pdf")
                if output.exists() and output.read_bytes().startswith(b"%PDF-"):
                    return output.read_bytes()
            except (OSError, subprocess.SubprocessError):
                logger.warning("LibreOffice PDF 转换失败", exc_info=True)
        try:
            from docx2pdf import convert

            output = source.with_suffix(".pdf")
            convert(str(source), str(output))
            data = output.read_bytes()
            if data.startswith(b"%PDF-"):
                return data
        except Exception:
            logger.warning("Word/docx2pdf PDF 转换失败", exc_info=True)
    raise RuntimeError("PDF 转换失败，DOCX 草案仍可下载")


async def _create_conversation(headers: dict[str, str], user_id: str) -> str:
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        for attempt in range(3):
            response = await client.post(
                CREATE_URL, headers=headers, json={"user_id": user_id}
            )
            if response.status_code in RETRYABLE_STATUS and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            response.raise_for_status()
            conversation_id = pick_conversation_id(response.json())
            if not conversation_id:
                raise RuntimeError("GPTBots 未返回 conversation_id")
            return conversation_id
    raise RuntimeError("无法创建 GPTBots 对话")


def _latest_assistant_text(payload: dict[str, Any]) -> tuple[str, str]:
    messages = payload.get("conversation_content", [])
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        parts: list[str] = []
        for content in message.get("content", []):
            if not isinstance(content, dict):
                continue
            for branch in content.get("branch_content", []):
                if isinstance(branch, dict) and branch.get("type") == "text":
                    text = branch.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
        if parts:
            marker = str(
                message.get("message_id")
                or message.get("create_time")
                or hash("\n".join(parts))
            )
            return marker, "\n".join(parts)
    return "", ""


async def _conversation_detail(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    conversation_id: str,
) -> tuple[str, str]:
    try:
        response = await client.get(
            MESSAGES_URL,
            headers=headers,
            params={"conversation_id": conversation_id, "page": 1, "page_size": 100},
        )
        if response.status_code >= 400:
            return "", ""
        return _latest_assistant_text(response.json())
    except (httpx.HTTPError, ValueError):
        return "", ""


async def _send_message(
    headers: dict[str, str],
    conversation_id: str,
    payload: dict[str, Any],
    timeout: float = 300.0,
) -> str:
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        baseline, _ = await _conversation_detail(client, headers, conversation_id)
        for attempt in range(3):
            try:
                response = await client.post(MESSAGE_URL, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                marker, recovered = await _conversation_detail(
                    client, headers, conversation_id
                )
                if recovered and marker != baseline:
                    return recovered
                raise RuntimeError("Agent 请求状态未知，请重试当前材料") from exc
            if response.status_code in RETRYABLE_STATUS:
                marker, recovered = await _conversation_detail(
                    client, headers, conversation_id
                )
                if recovered and marker != baseline:
                    return recovered
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
            response.raise_for_status()
            answer = extract_gptbots_reply(response.json()).strip()
            if not answer:
                _, answer = await _conversation_detail(
                    client, headers, conversation_id
                )
            if not answer:
                raise RuntimeError("Agent 未返回有效内容")
            return answer
    raise RuntimeError("Agent 请求失败")


async def _ocr_pdf(content: bytes, filename: str) -> list[str]:
    from api.pdf_chat import (
        _MAX_POLLS,
        _POLL_INTERVAL,
        _parse_ocr_jsonl,
        _submit_paddle_ocr_job,
    )
    from model.config import PADDLE_OCR_JOB_URL, paddle_ocr_headers

    page_count = _validate_pdf(content)
    async with httpx.AsyncClient(trust_env=False) as client:
        job_id = await _submit_paddle_ocr_job(client, content, filename)
        result_url = ""
        confirmed_pages = page_count
        for _ in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            response = await client.get(
                f"{PADDLE_OCR_JOB_URL}/{job_id}",
                headers=paddle_ocr_headers(),
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            if data.get("state") == "failed":
                raise RuntimeError(
                    f"OCR 任务失败：{data.get('errorMsg', '未知错误')}"
                )
            if data.get("state") == "done":
                result_url = data.get("resultUrl", {}).get("jsonUrl", "")
                confirmed_pages = (
                    data.get("extractProgress", {}).get("totalPages") or page_count
                )
                break
        if not result_url:
            raise RuntimeError("OCR 任务超时")
    last_error: Exception | None = None
    text = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
                response = await client.get(result_url)
                response.raise_for_status()
                text = response.text
                break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(2)
    if not text:
        raise RuntimeError(f"下载 OCR 结果失败：{last_error}")
    pages = _parse_ocr_jsonl(text, confirmed_pages)
    if any(not page for page in pages):
        native = extract_native_text(content)
        pages = [
            page or (native[index] if index < len(native) else "")
            for index, page in enumerate(pages)
        ]
    if any(not page.strip() for page in pages):
        raise RuntimeError("至少一页无法识别，已阻止生成不完整草案")
    return pages


def _agent_a_prompt(filename: str, unit_label: str, content: str) -> str:
    return (
        "You are processing one evidence unit for service-agreement drafting. "
        "Extract only explicit facts. Preserve names, addresses, dates, amounts, "
        "currencies, payment bases, commitments, exclusions and uncertainty. "
        "Do not infer or give legal advice. Return concise structured content.\n\n"
        f"Source: {filename}\nLocator: {unit_label}\n\n{content}"
    )


async def _summarize_material(material: MaterialRecord) -> list[dict[str, Any]]:
    if material.file_type == "xlsx":
        markdown, _ = extract_xlsx_markdown(material.content, material.filename)
        units = [("workbook", markdown)]
    else:
        pdf = (
            image_to_pdf(material.content)
            if material.file_type == "image"
            else material.content
        )
        pages = await _ocr_pdf(
            pdf,
            ocr_submission_filename(material.filename, material.file_type),
        )
        units = [(f"page:{index}", text) for index, text in enumerate(pages, 1)]
    conversation_id = await _create_conversation(
        auth_headers(), f"case6b-{material.material_id}"
    )
    material.conversation_id = conversation_id
    facts: list[dict[str, Any]] = []
    for unit_label, content in units:
        payload = {
            "conversation_id": conversation_id,
            "response_mode": "blocking",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _agent_a_prompt(
                                material.filename, unit_label, content
                            ),
                        }
                    ],
                }
            ],
            "conversation_config": {
                "short_term_memory": False,
                "long_term_memory": False,
            },
        }
        reply = await _send_message(
            auth_headers(), conversation_id, payload, timeout=180.0
        )
        facts.append(
            {
                "locator": unit_label,
                "summary": reply,
            }
        )
    return facts


def _validate_agent_fields(
    manifest: dict[str, Any],
    parsed: dict[str, Any],
    *,
    filled: bool,
) -> list[dict[str, Any]]:
    expected = [field["field_id"] for field in manifest["fields"]]
    values = parsed.get("fields")
    if not isinstance(values, list):
        raise ValueError("Agent 返回缺少 fields 数组")
    ids = [str(item.get("field_id", "")) for item in values if isinstance(item, dict)]
    if ids != expected or len(set(ids)) != len(expected):
        raise ValueError("Agent 返回的字段 ID 存在遗漏、重复或顺序错误")
    if filled:
        manifest_by_id = {field["field_id"]: field for field in manifest["fields"]}
        for item in values:
            if item.get("status") not in ALLOWED_AGENT_STATUSES:
                raise ValueError("Agent 返回了不支持的字段状态")
            definition = manifest_by_id[item["field_id"]]
            if definition.get("field_kind") in SIGNATURE_KINDS:
                item["status"] = "LEAVE_BLANK"
                item["value"] = ""
                item["evidence"] = []
            if item["status"] == "FILLED" and not item.get("evidence"):
                raise ValueError("Agent 已填字段缺少证据来源")
            if item["status"] == "FILLED" and not str(item.get("value", "")).strip():
                raise ValueError("Agent 已填字段缺少字段值")
            if item["status"] in {"NEEDS_CONFIRMATION", "REMOVE", "LEAVE_BLANK"}:
                item["value"] = ""
                if item["status"] != "NEEDS_CONFIRMATION":
                    item["evidence"] = []
    return values


def _merge_manifest(
    local_manifest: dict[str, Any],
    parsed: dict[str, Any],
) -> dict[str, Any]:
    fields = _validate_agent_fields(local_manifest, parsed, filled=False)
    local_by_id = {field["field_id"]: field for field in local_manifest["fields"]}
    merged = []
    for field in fields:
        local = local_by_id[field["field_id"]]
        merged.append({**local, **field, "locator": local["locator"]})
    return {
        "template_language": parsed.get("template_language") or "en",
        "fields": merged,
    }


async def _run_agent_l(session: Case6BSession) -> None:
    if not AGENT_L_API_KEY:
        raise RuntimeError("Case 6B 尚未配置 AGENT_L_API_KEY")
    last_error: Exception | None = None
    for _ in range(2):
        conversation_id = await _create_conversation(
            agent_l_auth_headers(), f"case6b-draft-{uuid.uuid4().hex}"
        )
        try:
            template_reply = await _send_message(
                agent_l_auth_headers(),
                conversation_id,
                build_agent_l_template_payload(
                    conversation_id, session.template, session.field_manifest
                ),
            )
            parsed_manifest = parse_agent_json(template_reply)
            merged_manifest = _merge_manifest(
                session.field_manifest, parsed_manifest
            )
            fill_reply = await _send_message(
                agent_l_auth_headers(),
                conversation_id,
                build_agent_l_fill_payload(
                    conversation_id,
                    {
                        "template_language": merged_manifest["template_language"],
                        "fields": merged_manifest["fields"],
                    },
                    session.full_summary or {"sources": []},
                ),
            )
            parsed_fill = parse_agent_json(fill_reply)
            session.field_manifest = merged_manifest
            session.template.language = merged_manifest["template_language"]
            session.fields = _validate_agent_fields(
                merged_manifest, parsed_fill, filled=True
            )
            session.conflicts = (
                parsed_fill.get("conflicts")
                if isinstance(parsed_fill.get("conflicts"), list)
                else []
            )
            session.agent_l_conversation_id = conversation_id
            session.review_version = 1
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Agent L 双阶段调用失败，将重建会话", exc_info=True)
    raise RuntimeError(f"Agent L 未能生成有效字段结果：{last_error}")


class Case6BDraftingHandler(CaseHandler):
    def __init__(self, retry_material_ids: set[str] | None = None):
        self.retry_material_ids = retry_material_ids

    async def analyze(
        self,
        session_id: str,
        session_store: dict[str, Any],
        session_results_store: dict[str, Any],
    ) -> AsyncGenerator[SseEvent, None]:
        values = session_store.get(session_id)
        if not values or not isinstance(values[0], Case6BSession):
            raise ValueError("Case 6B 会话不存在或已过期")
        session = values[0]
        if session.lock.locked():
            raise ValueError("当前草案正在处理中，请稍候")
        async with session.lock:
            total = len(session.materials)
            for index, material in enumerate(session.materials, 1):
                if material.status == "complete":
                    continue
                if (
                    self.retry_material_ids is not None
                    and material.material_id not in self.retry_material_ids
                ):
                    continue
                material.status = "extracting"
                material.error = None
                yield SseEvent(
                    "progress",
                    {
                        "stage": "material_extract",
                        "material_id": material.material_id,
                        "filename": material.filename,
                        "index": index,
                        "total": total,
                        "status": material.status,
                        "message": f"正在提取《{material.filename}》",
                    },
                )
                try:
                    material.status = "summarizing"
                    material.facts = await _summarize_material(material)
                    material.status = "complete"
                    yield SseEvent(
                        "progress",
                        {
                            "stage": "material_summary",
                            "material_id": material.material_id,
                            "filename": material.filename,
                            "index": index,
                            "total": total,
                            "status": "complete",
                            "message": f"《{material.filename}》处理完成",
                        },
                    )
                except Exception as exc:
                    material.status = "failed"
                    material.error = str(exc)
                    yield SseEvent(
                        "error",
                        {
                            "stage": "material_summary",
                            "material_id": material.material_id,
                            "filename": material.filename,
                            "message": str(exc),
                        },
                    )
            failed = [
                material for material in session.materials if material.status == "failed"
            ]
            if failed:
                yield SseEvent(
                    "error",
                    {
                        "stage": "blocked",
                        "message": f"{len(failed)} 份材料处理失败，请重试失败材料",
                    },
                )
                return
            session.full_summary = {
                "summary_version": "case6b-v1",
                "sources": [
                    {
                        "filename": material.filename,
                        "source_type": material.file_type,
                        "facts": material.facts,
                    }
                    for material in session.materials
                ],
                "conflicts": [],
            }
            yield SseEvent(
                "progress",
                {
                    "stage": "template_parse",
                    "message": "正在解析模板字段并匹配证据",
                },
            )
            await _run_agent_l(session)
            session.updated_at = datetime.utcnow()
            session_results_store[session_id] = {
                "case_type": "case6b",
                "created_at": session.created_at,
                "results": {
                    RESULT_KEY: {
                        "conversation_id": session.agent_l_conversation_id,
                        "review_version": session.review_version,
                    }
                },
            }
            yield SseEvent(
                "result",
                {
                    "result_key": RESULT_KEY,
                    "review_required": True,
                    "review_version": session.review_version,
                    "unresolved_count": review_payload(session)["unresolved_count"],
                    "message": "字段匹配完成，请审阅后生成协议草案",
                },
            )

    async def generate_report(
        self,
        session_id: str,
        result_key: str,
        output_format: str,
        session_results_store: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        from api.pdf_chat import session_store

        values = session_store.get(session_id)
        if (
            result_key != RESULT_KEY
            or not values
            or not isinstance(values[0], Case6BSession)
        ):
            raise ValueError("协议草案不存在或已过期")
        session = values[0]
        stem = Path(session.template.filename).stem
        if output_format == "docx" and session.generated_docx:
            return (
                session.generated_docx,
                f"{stem}_服务协议草案.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        if output_format == "pdf" and session.generated_pdf:
            return session.generated_pdf, f"{stem}_服务协议草案.pdf", "application/pdf"
        if output_format == "pdf" and session.generated_docx:
            raise ValueError(session.pdf_error or "PDF 尚未生成，DOCX 仍可下载")
        raise ValueError("请先完成字段审阅并生成草案")

    async def followup_chat(
        self,
        session_id: str,
        text: str,
        conversation_id: str | None,
        session_results_store: dict[str, Any],
    ) -> dict[str, Any]:
        raise ValueError("Case 6B 不支持结果追问")

    def get_downloadable_keys(
        self,
        session_id: str,
        session_results_store: dict[str, Any],
    ) -> list[str]:
        from api.pdf_chat import session_store

        values = session_store.get(session_id)
        if (
            values
            and isinstance(values[0], Case6BSession)
            and values[0].generated_docx
        ):
            return [RESULT_KEY]
        return []
