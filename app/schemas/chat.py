from __future__ import annotations

import uuid
from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    session_id: uuid.UUID | None = None
