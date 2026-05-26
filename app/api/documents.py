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
from app.schemas.document import DocumentOut, DocumentUpdateIn
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


@router.get("/{doc_id}/view-url")
async def get_document_view_url(
    doc_id: uuid.UUID,
    _: Annotated[AuthUser, Depends(_require_any_role)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Return a short-lived signed URL the browser can open in a new tab.

    Read-access only — same gate as the list endpoint (any signed-in
    role can preview a document). The URL embeds its own auth so the
    new tab doesn't need our bearer token.
    """
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )
    try:
        url = storage.signed_url(doc.storage_path, expires_in=300)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "STORAGE_SIGN_FAILED", "message": str(exc)}},
        ) from exc
    return {"data": {"url": url, "filename": doc.filename}}


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


_MAX_TAGS = 16
_MAX_TAG_LEN = 32


def _invalid_tags(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": "INVALID_TAGS", "message": message}},
    )


def _validate_tags(raw: list[str]) -> list[str]:
    """Clean + validate a tag list. Returns lowercased, trimmed, deduped tags.

    Rules: each tag 1–32 chars after strip; no path separators or null
    bytes; case-folded; dedupe preserving order; at most 16 tags per doc.
    An empty list is valid (clears all tags on PATCH).
    """
    if not isinstance(raw, list):
        raise _invalid_tags("tags must be a list of strings")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise _invalid_tags("each tag must be a string")
        normalized = item.strip().lower()
        if not normalized:
            continue
        if len(normalized) > _MAX_TAG_LEN:
            raise _invalid_tags(f"each tag must be {_MAX_TAG_LEN} characters or fewer")
        for ch in _FORBIDDEN_FILENAME_CHARS:
            if ch in normalized:
                raise _invalid_tags("tags cannot contain path separators or null bytes")
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)

    if len(cleaned) > _MAX_TAGS:
        raise _invalid_tags(f"at most {_MAX_TAGS} tags per document")
    return cleaned


@router.patch("/{doc_id}")
async def update_document(
    doc_id: uuid.UUID,
    body: DocumentUpdateIn,
    _: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Patch a document's filename and/or tags. Both fields are optional;
    only the provided ones are persisted. The Storage object, chunks,
    and ingestion state are not touched. Source chips on future chat
    answers pick up the new filename because the chat service reads
    documents.filename at retrieval time.
    """
    if body.filename is None and body.tags is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "EMPTY_PATCH", "message": "At least one of  or  must be provided."}},
        )

    cleaned_filename = _validate_filename(body.filename) if body.filename is not None else None
    cleaned_tags = _validate_tags(body.tags) if body.tags is not None else None

    doc = await session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}},
        )

    if cleaned_filename is not None:
        doc.filename = cleaned_filename
    if cleaned_tags is not None:
        doc.tags = cleaned_tags
    await session.commit()
    await session.refresh(doc)
    return {"data": DocumentOut.model_validate(doc).model_dump(mode="json")}
