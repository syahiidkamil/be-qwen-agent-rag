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
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DocumentRenameIn(BaseModel):
    """Body for PATCH /api/documents/{id} — rename only.

    Validation lives at the route layer because the rules (length, trim,
    no path separators) are easier to express imperatively than as
    pydantic constraints. The field accepts any non-empty string here so
    we can return a structured 400 with a specific code at the route.
    """

    filename: str
