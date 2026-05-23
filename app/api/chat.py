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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.db import _sessionmaker
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.schemas.chat import ChatRequest
from app.services.chat import stream_answer

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("")
async def chat(body: ChatRequest):
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
    chat_session_id: uuid.UUID
    if body.session_id is None:
        async with sessionmaker_() as sess:
            new_session = ChatSession()
            sess.add(new_session)
            await sess.commit()
            await sess.refresh(new_session)
            chat_session_id = new_session.id
    else:
        chat_session_id = body.session_id

    # Persist the user turn before streaming.
    async with sessionmaker_() as sess:
        sess.add(
            ChatMessage(
                session_id=chat_session_id,
                role=MessageRole.user,
                content=last_user.content,
            )
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
                async for event in stream_answer(sess, user_query, history):
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
