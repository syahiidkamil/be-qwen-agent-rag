"""Anonymous chat endpoint with SSE streaming.

We do NOT use the request-scoped `Depends(get_db)` here because the
StreamingResponse outlives the request handler, and SQLAlchemy raises
IllegalStateChangeError when the session is closed (by the dependency
teardown) while the stream is still iterating. Instead, each DB
interaction opens its own short-lived session via the sessionmaker.
"""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update

from app.core.auth import AuthUser, get_current_user_optional
from app.core.db import _sessionmaker
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.landing_config import LandingConfig
from app.schemas.chat import ChatRequest
from app.services.chat import stream_answer

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Auto-title length cap. Titles are derived from the first user message and
# truncated to this many characters (ellipsis included). Kept in sync with
# the chat_sessions.title column width and the FE sidebar's expectations.
_TITLE_MAX = 40


def _make_title(text: str) -> str:
    """Derive a session title from the first user message.

    Collapses whitespace and truncates to _TITLE_MAX chars with a trailing
    ellipsis. Empty/whitespace-only input falls back to a generic label so a
    session always has a usable title in the sidebar.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "New chat"
    if len(cleaned) <= _TITLE_MAX:
        return cleaned
    return cleaned[: _TITLE_MAX - 1].rstrip() + "…"


async def _current_chat_mode() -> str:
    """Read chat_mode from the singleton landing_config row.

    Defensive default of ``public`` covers: row missing, key missing,
    bad value from direct DB tampering. The FE will see the same default
    via the LandingConfigOut normaliser. Lives in this module rather
    than the schemas package because nothing else reads chat_mode in
    isolation right now.
    """
    sessionmaker_ = _sessionmaker()
    async with sessionmaker_() as sess:
        result = await sess.execute(select(LandingConfig).where(LandingConfig.id == 1))
        row = result.scalar_one_or_none()
    if row is None:
        return "public"
    value = (row.config or {}).get("chat_mode")
    return value if value in ("public", "internal") else "public"


@router.post("")
async def chat(
    body: ChatRequest,
    user: Annotated[AuthUser | None, Depends(get_current_user_optional)] = None,
):
    # Internal-mode gating. Authoritative — the FE's "Sign in to chat"
    # card is UX, this 401 is what actually blocks unauthenticated POSTs.
    mode = await _current_chat_mode()
    if mode == "internal" and user is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {
                "code": "INTERNAL_MODE_REQUIRES_AUTH",
                "message": "Sign in to chat.",
            }},
        )

    if not body.messages:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "EMPTY_MESSAGES", "message": "messages must be non-empty"}},
        )
    last_user = next((m for m in reversed(body.messages) if m.role == "user"), None)
    if last_user is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "NO_USER_TURN", "message": "Last user message required"}},
        )

    sessionmaker_ = _sessionmaker()

    # Resolve or create the chat session in its own short-lived DB session.
    # A brand-new session is stamped with the caller's user id (NULL for
    # anonymous public-widget visitors) and an auto-title from the first
    # user message — both back the per-user session sidebar.
    chat_session_id: uuid.UUID
    if body.session_id is None:
        async with sessionmaker_() as sess:
            new_session = ChatSession(
                user_id=uuid.UUID(user.sub) if user and user.sub else None,
                title=_make_title(last_user.content),
            )
            sess.add(new_session)
            await sess.commit()
            await sess.refresh(new_session)
            chat_session_id = new_session.id
    else:
        chat_session_id = body.session_id

    # Persist the user turn before streaming, and bump the session's
    # last-activity stamp so the sidebar orders by most-recently-used.
    async with sessionmaker_() as sess:
        sess.add(
            ChatMessage(
                session_id=chat_session_id,
                role=MessageRole.user,
                content=last_user.content,
            )
        )
        await sess.execute(
            update(ChatSession)
            .where(ChatSession.id == chat_session_id)
            .values(last_message_at=func.now())
        )
        await sess.commit()

    history = [{"role": m.role, "content": m.content} for m in body.messages[:-1]]
    user_query = last_user.content

    async def event_stream():
        # Send the session id up front so the client can persist it.
        yield f"data: {json.dumps({'type': 'session', 'session_id': str(chat_session_id)})}\n\n"

        last_sources: list = []
        full_text = ""

        # Retrieval + LLM streaming happen inside their own session.
        try:
            async with sessionmaker_() as sess:
                async for event in stream_answer(
                    sess, user_query, history, session_id=str(chat_session_id)
                ):
                    if event["type"] == "sources":
                        last_sources = event["sources"]
                    elif event["type"] == "done":
                        full_text = event.get("full_text", "") or full_text
                    yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface to the client
            err = {"type": "error", "code": type(exc).__name__, "message": str(exc)[:500]}
            yield f"data: {json.dumps(err)}\n\n"

        # Persist the assistant turn in its own session.
        async with sessionmaker_() as sess:
            sess.add(
                ChatMessage(
                    session_id=chat_session_id,
                    role=MessageRole.assistant,
                    content=full_text,
                    sources=last_sources,
                )
            )
            await sess.commit()

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
