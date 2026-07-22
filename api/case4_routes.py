"""Case 4 upload endpoint."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.pdf_chat import session_metadata, session_results_store, session_store
from cases.case4_pdf_translation import MAX_PDF_BYTES, validate_case4_pdf

router = APIRouter(prefix="/case4", tags=["case4"])


@router.post("/session/upload")
async def upload_translation_pdf(
    pdf_file: UploadFile = File(...),
    direction: str = Form(...),
):
    filename = pdf_file.filename or "document.pdf"
    pdf_bytes = await pdf_file.read(MAX_PDF_BYTES + 1)
    try:
        document = validate_case4_pdf(pdf_bytes, filename, direction)
    except ValueError as exc:
        status_code = 413 if "25 MB" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    session_id = uuid4().hex
    session_store[session_id] = [document]
    session_results_store.pop(session_id, None)
    session_metadata[session_id] = {
        "case_type": "case4",
        "filename": document.filename,
        "page_count": document.page_count,
        "direction": document.direction,
    }
    return {
        "session_id": session_id,
        "filename": document.filename,
        "page_count": document.page_count,
        "direction": document.direction,
    }
