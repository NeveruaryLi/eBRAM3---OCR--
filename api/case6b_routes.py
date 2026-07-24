"""Case 6B upload, review, retry and draft-finalization endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.pdf_chat import (
    SESSION_TTL_SECONDS,
    session_metadata,
    session_results_store,
    session_store,
)
from cases.case6b_service_agreement import (
    MAX_MATERIALS,
    MAX_OCR_UNITS,
    MAX_TOTAL_BYTES,
    RESULT_KEY,
    Case6BDraftingHandler,
    Case6BSession,
    apply_review_changes,
    build_template_preflight,
    convert_docx_to_pdf,
    extract_template_manifest,
    render_draft_docx,
    review_payload,
    validate_material,
    validate_template,
)

router = APIRouter(prefix="/case6b", tags=["case6b"])


class FieldUpdate(BaseModel):
    field_id: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=4000)


class ServiceRowUpdate(BaseModel):
    group_index: int = Field(ge=1, le=100)
    action: str = Field(pattern="^(included|optional|blank|remove|keep)$")
    name: str = Field(default="", max_length=1000)
    price: str = Field(default="", max_length=1000)


class ConflictResolution(BaseModel):
    conflict_id: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=4000)


class ReviewUpdate(BaseModel):
    version: int = Field(ge=1)
    field_updates: list[FieldUpdate] = Field(default_factory=list, max_length=200)
    service_rows: list[ServiceRowUpdate] = Field(default_factory=list, max_length=100)
    conflict_resolutions: list[ConflictResolution] = Field(
        default_factory=list, max_length=100
    )


class TemplateUpdate(BaseModel):
    version: int = Field(ge=1)
    confirmed: bool
    field_updates: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    repeat_blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    signature_sections: list[str] = Field(default_factory=list, max_length=50)
    allowed_rewrites: list[str] = Field(default_factory=list, max_length=50)


class FinalizeRequest(BaseModel):
    version: int = Field(ge=1)
    allow_unresolved: bool = False


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _session(session_id: str) -> Case6BSession:
    values = session_store.get(session_id)
    if not values or not isinstance(values[0], Case6BSession):
        raise _error(410, "SESSION_EXPIRED", "草案会话不存在或已过期，请重新上传")
    session = values[0]
    if session.updated_at < datetime.utcnow() - timedelta(seconds=SESSION_TTL_SECONDS):
        session_store.pop(session_id, None)
        session_results_store.pop(session_id, None)
        session_metadata.pop(session_id, None)
        raise _error(410, "SESSION_EXPIRED", "草案会话已过期，请重新上传")
    return session


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/session/upload")
async def upload_case6b(
    template_file: UploadFile = File(...),
    material_files: list[UploadFile] = File(...),
    form_entries: str | None = Form(default=None),
):
    if not 1 <= len(material_files) <= MAX_MATERIALS:
        raise _error(400, "INVALID_MATERIAL_COUNT", "事实材料数量必须为 1–20 份")
    template_name = Path(template_file.filename or "template.docx").name
    template_bytes = await template_file.read(10 * 1024 * 1024 + 1)
    try:
        template = validate_template(template_bytes, template_name)
        manifest = extract_template_manifest(template_bytes)
    except ValueError as exc:
        raise _error(400, "INVALID_TEMPLATE", str(exc)) from exc

    total_bytes = len(template_bytes)
    materials = []
    hashes: set[str] = set()
    for upload in material_files:
        filename = Path(upload.filename or "material").name
        content = await upload.read(25 * 1024 * 1024 + 1)
        total_bytes += len(content)
        digest = hashlib.sha256(content).hexdigest()
        if digest in hashes:
            raise _error(400, "DUPLICATE_MATERIAL", f"重复材料：《{filename}》")
        hashes.add(digest)
        try:
            materials.append(validate_material(content, filename))
        except (ValueError, RuntimeError) as exc:
            raise _error(400, "INVALID_MATERIAL", str(exc)) from exc
    if total_bytes > MAX_TOTAL_BYTES:
        raise _error(413, "UPLOAD_TOO_LARGE", "模板和材料总大小不能超过 100 MB")
    parsed_form_entries: dict[str, Any] = {}
    if form_entries:
        try:
            value = json.loads(form_entries)
        except json.JSONDecodeError as exc:
            raise _error(400, "INVALID_FORM_ENTRIES", "form_entries 必须是 JSON Object") from exc
        if not isinstance(value, dict):
            raise _error(400, "INVALID_FORM_ENTRIES", "form_entries 必须是 JSON Object")
        if len(json.dumps(value, ensure_ascii=False)) > 50_000:
            raise _error(413, "FORM_ENTRIES_TOO_LARGE", "结构化表单信息不能超过 50 KB")
        parsed_form_entries = value
    ocr_units = sum(
        material.units for material in materials if material.file_type in {"pdf", "image"}
    )
    if ocr_units > MAX_OCR_UNITS:
        raise _error(400, "TOO_MANY_OCR_UNITS", "PDF 页与图片合计最多支持 60 页")

    session_id = uuid4().hex
    session = Case6BSession(
        template=template,
        materials=materials,
        field_manifest=manifest,
        form_entries=parsed_form_entries,
    )
    session_store[session_id] = [session]
    session_results_store.pop(session_id, None)
    session_metadata[session_id] = {
        "case_type": "case6b",
        "filename": template.filename,
        "placeholder_count": len(manifest["fields"]),
        "created_at": session.created_at,
    }
    return {
        "session_id": session_id,
        "template": {
            "filename": template.filename,
            "language": template.language,
            **build_template_preflight(manifest),
        },
        "placeholder_count": len(manifest["fields"]),
        "materials": [
            {
                "material_id": material.material_id,
                "filename": material.filename,
                "file_type": material.file_type,
                "units": material.units,
                "status": material.status,
            }
            for material in materials
        ],
        "expires_in": SESSION_TTL_SECONDS,
    }


@router.get("/session/{session_id}/template")
async def get_case6b_template(session_id: str):
    session = _session(session_id)
    return build_template_preflight(session.field_manifest, session.template_version)


@router.patch("/session/{session_id}/template")
async def update_case6b_template(session_id: str, body: TemplateUpdate):
    session = _session(session_id)
    if session.lock.locked():
        raise _error(409, "SESSION_BUSY", "当前草案正在处理中，请稍候")
    if body.version != session.template_version:
        raise _error(409, "STALE_TEMPLATE", "模板预检内容已更新，请刷新后再提交")
    if session.fields:
        raise _error(409, "TEMPLATE_ALREADY_ANALYZED", "材料分析开始后不能修改模板映射")
    known_ids = {field["field_id"] for field in session.field_manifest["fields"]}
    for update in body.field_updates:
        field_id = str(update.get("field_id", ""))
        if field_id not in known_ids:
            raise _error(400, "INVALID_TEMPLATE_MAPPING", f"未知模板字段：{field_id}")
        target = next(
            field
            for field in session.field_manifest["fields"]
            if field["field_id"] == field_id
        )
        for key in ("semantic_key", "label", "required"):
            if key in update:
                target[key] = update[key]
    if body.repeat_blocks:
        session.field_manifest["repeat_blocks"] = body.repeat_blocks
    if body.signature_sections:
        if any(field_id not in known_ids for field_id in body.signature_sections):
            raise _error(400, "INVALID_TEMPLATE_MAPPING", "签署区包含未知字段")
        session.field_manifest["signature_sections"] = body.signature_sections
    session.field_manifest["allowed_rewrites"] = body.allowed_rewrites
    session.field_manifest["template_confirmed"] = body.confirmed
    session.template_version += 1
    session.updated_at = datetime.utcnow()
    return build_template_preflight(session.field_manifest, session.template_version)


@router.post("/session/{session_id}/retry")
async def retry_case6b(
    session_id: str,
    material_id: str | None = Query(default=None, min_length=1, max_length=64),
):
    session = _session(session_id)
    if session.lock.locked():
        raise _error(409, "SESSION_BUSY", "当前草案正在处理中，请稍候")
    retry_ids: set[str] | None = None
    if material_id is not None:
        material = next(
            (
                item
                for item in session.materials
                if item.material_id == material_id
            ),
            None,
        )
        if material is None:
            raise _error(404, "MATERIAL_NOT_FOUND", "指定材料不存在")
        if material.status != "failed":
            raise _error(400, "MATERIAL_NOT_FAILED", "只能单独重试处理失败的材料")
        retry_ids = {material_id}
    if (
        not any(material.status == "failed" for material in session.materials)
        and session.fields
    ):
        raise _error(400, "NO_FAILED_MATERIAL", "当前没有需要重试的失败材料")
    handler = Case6BDraftingHandler(retry_material_ids=retry_ids)

    async def stream() -> AsyncIterator[str]:
        yield _sse({"type": "analysis_start", "session_id": session_id})
        try:
            async for event in handler.analyze(
                session_id, session_store, session_results_store
            ):
                yield _sse({"type": event.type, **event.data})
        except Exception as exc:
            yield _sse({"type": "fatal_error", "message": str(exc)})
            return
        yield _sse({"type": "analysis_complete"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/session/{session_id}/review")
async def get_case6b_review(session_id: str):
    session = _session(session_id)
    if not session.fields:
        raise _error(409, "REVIEW_NOT_READY", "字段分析尚未完成")
    return review_payload(session)


@router.patch("/session/{session_id}/review")
async def update_case6b_review(session_id: str, body: ReviewUpdate):
    session = _session(session_id)
    if session.lock.locked():
        raise _error(409, "SESSION_BUSY", "当前草案正在处理中，请稍候")
    if not session.fields:
        raise _error(409, "REVIEW_NOT_READY", "字段分析尚未完成")
    async with session.lock:
        try:
            apply_review_changes(
                session,
                body.version,
                [update.model_dump() for update in body.field_updates],
                [row.model_dump() for row in body.service_rows],
                [
                    resolution.model_dump()
                    for resolution in body.conflict_resolutions
                ],
            )
        except ValueError as exc:
            code = "STALE_REVIEW" if "已更新" in str(exc) else "INVALID_REVIEW"
            raise _error(409 if code == "STALE_REVIEW" else 400, code, str(exc)) from exc
    return review_payload(session)


@router.post("/session/{session_id}/finalize")
async def finalize_case6b(session_id: str, body: FinalizeRequest):
    session = _session(session_id)
    if session.lock.locked():
        raise _error(409, "SESSION_BUSY", "当前草案正在处理中，请稍候")
    if body.version != session.review_version:
        raise _error(409, "STALE_REVIEW", "审阅内容已更新，请刷新后再生成")
    review = review_payload(session)
    if review["unresolved_conflict_count"]:
        raise _error(
            409,
            "UNRESOLVED_CONFLICTS",
            f"仍有 {review['unresolved_conflict_count']} 项材料冲突待解决",
        )
    if review["unresolved_count"] and not body.allow_unresolved:
        raise _error(
            409,
            "UNRESOLVED_FIELDS",
            f"仍有 {review['unresolved_count']} 个必填字段待确认",
        )
    async with session.lock:
        try:
            session.generated_docx = await asyncio.to_thread(
                render_draft_docx,
                session.template.content,
                session.field_manifest,
                session.fields,
                session.template.language,
            )
        except ValueError as exc:
            raise _error(400, "DRAFT_GENERATION_FAILED", str(exc)) from exc
        session.generated_pdf = None
        session.pdf_error = None
        try:
            session.generated_pdf = await asyncio.to_thread(
                convert_docx_to_pdf,
                session.generated_docx,
                session.template.filename,
            )
        except Exception as exc:
            session.pdf_error = str(exc)
        session.updated_at = datetime.utcnow()
        result = session_results_store.setdefault(
            session_id,
            {
                "case_type": "case6b",
                "created_at": session.created_at,
                "results": {},
            },
        )
        result["results"][RESULT_KEY] = {
            "conversation_id": session.agent_l_conversation_id,
            "review_version": session.review_version,
            "docx_ready": True,
            "pdf_ready": session.generated_pdf is not None,
        }
    return {
        "result_key": RESULT_KEY,
        "version": session.review_version,
        "docx_ready": True,
        "pdf_ready": session.generated_pdf is not None,
        "pdf_error": session.pdf_error,
    }
