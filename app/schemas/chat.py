from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    session_id: uuid.UUID | None = None


class ChatSessionOut(BaseModel):
    """A chat session row for the per-user session sidebar list."""

    id: uuid.UUID
    title: str | None
    started_at: datetime
    last_message_at: datetime

    class Config:
        from_attributes = True


class ChatMessageOut(BaseModel):
    """A persisted message, replayed when a past session is reopened."""

    id: uuid.UUID
    role: str
    content: str
    sources: list | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSessionDetailOut(ChatSessionOut):
    """A session plus its full message history (oldest first)."""

    messages: list[ChatMessageOut]


class ChatSessionUpdateIn(BaseModel):
    title: str
