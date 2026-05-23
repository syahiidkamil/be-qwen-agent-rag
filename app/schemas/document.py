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
