"""Admin endpoints for the knowledgebase document corpus."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, get_current_admin, require_role
from app.core.db import get_db
from app.models.document import Document, IngestStatus
from app.schemas.document import DocumentOut, DocumentRenameIn
from app.services import ingestion, storage

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Read access to the KB list is broader than write access: signed-in users
# need it for the /workspace surface, but mutation is still admin-only.
_require_any_role = require_role("user")


@router.get("")
async def list_documents(
    _: Annotated[AuthUser, Depends(_require_any_role)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await session.execute(
        select(Document).order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()
    return {"data": [DocumentOut.model_validate(d).model_dump(mode="json") for d in docs]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    user: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    background: BackgroundTasks,
    file: UploadFile = File(...),
):
    content = await file.read()
    doc_id = uuid.uuid4()
    path = storage.make_storage_path(doc_id, file.filename or "unnamed")

    try:
        storage.upload_bytes(path, content, content_type=file.content_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "STORAGE_UPLOAD_FAILED", "message": str(exc)}},
        ) from exc

    doc = Document(
        id=doc_id,
        user_id=uuid.UUID(user.sub) if user.sub else None,
        filename=file.filename or "unnamed",
        mime_type=file.content_type,
        storage_path=path,
        size_bytes=len(content),
        status=IngestStatus.uploaded,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    # Kick off ingestion in the background. For MVP we use BackgroundTasks;
    # swap to a proper queue (Arq, Celery, RQ) when traffic warrants it.
    background.add_task(ingestion.ingest_document, doc.id)

    return {"data": DocumentOut.model_validate(doc).model_dump(mode="json")}


@router.get("/{doc_id}")
async def get_document(
    doc_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )
    return {"data": DocumentOut.model_validate(doc).model_dump(mode="json")}


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )
    try:
        storage.remove(doc.storage_path)
    except Exception:
        # Don't block DB cleanup on storage hiccups; orphan can be GC'd later.
        pass
    await session.delete(doc)
    await session.commit()
    return None


@router.post("/{doc_id}/reingest")
async def reingest_document(
    doc_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    background: BackgroundTasks,
):
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )
    doc.status = IngestStatus.uploaded
    doc.error_message = None
    await session.commit()
    background.add_task(ingestion.ingest_document, doc_id)
    return {"data": DocumentOut.model_validate(doc).model_dump(mode="json")}


_FORBIDDEN_FILENAME_CHARS = ("/", "\\", "\x00")


def _validate_filename(raw: str) -> str:
    """Trim + validate a rename input. Returns the cleaned value or raises 400."""
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_FILENAME", "message": "filename must be a string"}},
        )
    cleaned = raw.strip()
    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_FILENAME", "message": "filename cannot be empty"}},
        )
    if len(cleaned) > 255:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_FILENAME", "message": "filename must be 255 characters or fewer"}},
        )
    for ch in _FORBIDDEN_FILENAME_CHARS:
        if ch in cleaned:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "INVALID_FILENAME", "message": "filename cannot contain path separators or null bytes"}},
            )
    return cleaned


@router.patch("/{doc_id}")
async def rename_document(
    doc_id: uuid.UUID,
    body: DocumentRenameIn,
    _: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Rename a document. Updates the filename column only; storage_path,
    chunks, and the underlying Supabase Storage object are untouched.
    Source chips on future chat answers pick up the new filename because
    the chat service reads documents.filename at retrieval time.
    """
    cleaned = _validate_filename(body.filename)

    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )
    doc.filename = cleaned
    await session.commit()
    await session.refresh(doc)
    return {"data": DocumentOut.model_validate(doc).model_dump(mode="json")}
