"""Pydantic response models for the documents API."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.document import IngestStatus


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str | None
    size_bytes: int | None
    status: IngestStatus
    chunk_count: int
    error_message: str | None
    tags: list[str]
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DocumentUpdateIn(BaseModel):
    """Body for PATCH /api/documents/{id}.

    Both fields are optional — PATCH semantics. Any omitted field is left
    untouched on the row. Validation (length, trim, path separators, tag
    count) is done at the route layer so each rule maps to a specific
    error code in the structured 400 response.
    """

    filename: str | None = None
    tags: list[str] | None = None
