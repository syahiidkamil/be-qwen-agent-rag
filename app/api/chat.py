"""Anonymous chat endpoint with SSE streaming."""
from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.schemas.chat import ChatRequest
from app.services.chat import stream_answer

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("")
async def chat(
    body: ChatRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    if not body.messages:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "EMPTY_MESSAGES", "message": "messages must be non-empty"}},
        )
    last_user = next(
        (m for m in reversed(body.messages) if m.role == "user"), None
    )
    if last_user is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "NO_USER_TURN", "message": "Last user message required"}},
        )

    # Persist or load the chat session.
    chat_session_id = body.session_id
    if chat_session_id is None:
        new_session = ChatSession()
        session.add(new_session)
        await session.commit()
        await session.refresh(new_session)
        chat_session_id = new_session.id

    # Persist the user message before streaming.
    session.add(
        ChatMessage(
            session_id=chat_session_id,
            role=MessageRole.user,
            content=last_user.content,
        )
    )
    await session.commit()

    history = [{"role": m.role, "content": m.content} for m in body.messages[:-1]]

    async def event_stream():
        # Emit the session id up front so the client can re-use it.
        yield f"data: {json.dumps({'type': 'session', 'session_id': str(chat_session_id)})}\n\n"

        last_sources = []
        full_text = ""
        async for event in stream_answer(session, last_user.content, history):
            if event["type"] == "sources":
                last_sources = event["sources"]
            elif event["type"] == "token":
                pass
            elif event["type"] == "done":
                full_text = event.get("full_text", "")
            yield f"data: {json.dumps(event)}\n\n"

        # Persist the assistant response.
        session.add(
            ChatMessage(
                session_id=chat_session_id,
                role=MessageRole.assistant,
                content=full_text,
                sources=last_sources,
            )
        )
        await session.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering on nginx
        },
    )
