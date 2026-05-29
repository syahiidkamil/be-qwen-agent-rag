"""Per-user chat-session management (list / open / rename / delete).

Backs the session sidebar on the AI Help page. Unlike the streaming chat
endpoint in ``chat.py`` (which avoids the request-scoped session because its
StreamingResponse outlives the handler), these are ordinary request/response
endpoints and use the standard ``Depends(get_db)`` session.

Every route is scoped to the caller: a session is only visible/mutable by the
user whose id created it. We return 404 (not 403) for someone else's session
so the endpoint never confirms a session id exists for another user.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_role
from app.core.db import get_db
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import (
    ChatMessageOut,
    ChatSessionDetailOut,
    ChatSessionOut,
    ChatSessionUpdateIn,
)

router = APIRouter(prefix="/api/chat/sessions", tags=["chat-sessions"])

# Any signed-in role (user/admin/super_admin) manages their own sessions.
_require_user = require_role("user")

# Sidebar cap. A single user accumulating more than this many conversations is
# well past what a sidebar can usefully show; older rows still exist in the DB.
_LIST_LIMIT = 100

_TITLE_MAX = 120


def _caller_id(user: AuthUser) -> uuid.UUID:
    try:
        return uuid.UUID(user.sub)
    except (ValueError, TypeError) as exc:  # malformed token subject
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_SUBJECT", "message": "Invalid token subject"}},
        ) from exc


async def _owned_session(
    session: AsyncSession, session_id: uuid.UUID, user: AuthUser
) -> ChatSession:
    """Load a session and assert the caller owns it, else 404."""
    row = await session.get(ChatSession, session_id)
    if row is None or row.user_id != _caller_id(user):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Chat session not found"}},
        )
    return row


def _validate_title(raw: str) -> str:
    if not isinstance(raw, str):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TITLE", "message": "title must be a string"}},
        )
    cleaned = raw.strip()
    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TITLE", "message": "title cannot be empty"}},
        )
    if len(cleaned) > _TITLE_MAX:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TITLE", "message": f"title must be {_TITLE_MAX} characters or fewer"}},
        )
    return cleaned


@router.get("")
async def list_sessions(
    user: Annotated[AuthUser, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """List the caller's chat sessions, most-recently-active first."""
    result = await session.execute(
        select(ChatSession)
        .where(ChatSession.user_id == _caller_id(user))
        .order_by(ChatSession.last_message_at.desc())
        .limit(_LIST_LIMIT)
    )
    rows = result.scalars().all()
    return {"data": [ChatSessionOut.model_validate(r).model_dump(mode="json") for r in rows]}


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Fetch one session plus its full message history (oldest first).

    Backs reopening a past conversation in the chat panel.
    """
    row = await _owned_session(session, session_id, user)
    msgs_result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = msgs_result.scalars().all()
    detail = ChatSessionDetailOut(
        id=row.id,
        title=row.title,
        started_at=row.started_at,
        last_message_at=row.last_message_at,
        messages=[ChatMessageOut.model_validate(m) for m in messages],
    )
    return {"data": detail.model_dump(mode="json")}


@router.patch("/{session_id}")
async def rename_session(
    session_id: uuid.UUID,
    body: ChatSessionUpdateIn,
    user: Annotated[AuthUser, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Rename a session. Title is the only mutable field."""
    title = _validate_title(body.title)
    row = await _owned_session(session, session_id, user)
    row.title = title
    await session.commit()
    await session.refresh(row)
    return {"data": ChatSessionOut.model_validate(row).model_dump(mode="json")}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(_require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a session. Its messages cascade (FK ondelete=CASCADE)."""
    row = await _owned_session(session, session_id, user)
    await session.delete(row)
    await session.commit()
    return None
