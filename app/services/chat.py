"""Build a Qwen-Agent Assistant grounded in retrieved chunks and stream tokens."""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from qwen_agent.agents import Assistant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.perf import mark, probe
from app.models.document import Document
from app.services import storage
from app.services.retrieval import RetrievedChunk, retrieve

GROUNDING_INSTRUCTION = """You are a knowledgebase assistant. Answer ONLY using
the CONTEXT passages below. If the CONTEXT does not contain the answer, reply
with exactly: "I don't have information about that in my knowledge base." — do
NOT make up information from your general training.

Do NOT include any citation tags in your reply (no [doc:...], no [1], no
footnote markers). The sources are displayed separately by the UI.

Keep answers concise unless the user asks for detail.
"""


def _format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(no relevant passages found)"
    parts = []
    for c in chunks:
        parts.append(f"[doc:{c.document_id}]\n{c.content}")
    return "\n\n---\n\n".join(parts)


async def stream_answer(
    session: AsyncSession,
    query: str,
    history: list[dict],
    session_id: str | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE-event-shaped dicts: {'type': 'token'|'sources'|'done', ...}."""
    settings = get_settings()
    chunks = await retrieve(session, query, top_k=8, session_id=session_id)

    # Pull the filename + storage path for every cited document so the UI can
    # render clickable source chips. Public bucket URLs are built directly;
    # swap for signed URLs here if the bucket later becomes private.
    doc_meta: dict[uuid.UUID, dict[str, str]] = {}
    doc_ids = list({c.document_id for c in chunks})
    if doc_ids:
        async with probe("doc_metadata", session_id):
            rows = await session.execute(
                select(Document.id, Document.filename, Document.storage_path).where(
                    Document.id.in_(doc_ids)
                )
            )
        for row in rows.mappings().all():
            doc_meta[row["id"]] = {
                "filename": row["filename"],
                "url": storage.public_url(row["storage_path"]),
            }

    # Surface sources to the UI before streaming tokens.
    yield {
        "type": "sources",
        "sources": [
            {
                "chunk_id": str(c.id),
                "document_id": str(c.document_id),
                "filename": doc_meta.get(c.document_id, {}).get("filename"),
                "url": doc_meta.get(c.document_id, {}).get("url"),
                "score": c.score,
            }
            for c in chunks
        ],
    }

    system_message = (
        GROUNDING_INSTRUCTION
        + "\n\n=== CONTEXT ===\n"
        + _format_context(chunks)
        + "\n=== END CONTEXT ===\n"
    )

    # Use the OpenAI-compatible endpoint (model_type "oai") so we hit the
    # International DashScope base URL — the native "qwen_dashscope" mode
    # defaults to the China endpoint and rejects International API keys.
    llm_cfg = {
        "model": settings.qwen_chat_model,
        "model_type": "oai",
        "model_server": settings.dashscope_base_url,
        "api_key": settings.dashscope_api_key,
        "generate_cfg": {
            "max_input_tokens": settings.qwen_max_input_tokens,
            "top_p": 0.8,
        },
    }
    async with probe("assistant_build", session_id):
        bot = Assistant(llm=llm_cfg, system_message=system_message)

    # Build the message list (history + new user turn).
    messages = list(history) + [{"role": "user", "content": query}]

    last_text = ""
    stream_t0 = time.perf_counter()
    ttft_marked = False
    for responses in bot.run(messages=messages):
        # qwen-agent streams a list of Message dicts. The assistant message
        # accumulates content; we yield the delta only.
        if not responses:
            continue
        msg = responses[-1]
        if msg.get("role") != "assistant":
            continue
        text_now = msg.get("content") or ""
        if isinstance(text_now, list):
            # ContentItem list — flatten
            text_now = "".join(item.get("text", "") for item in text_now if isinstance(item, dict))
        if text_now and text_now != last_text:
            delta = text_now[len(last_text):]
            last_text = text_now
            if not ttft_marked:
                mark("ttft", stream_t0, session_id)
                ttft_marked = True
            yield {"type": "token", "delta": delta}

    mark("stream_total", stream_t0, session_id)
    yield {"type": "done", "full_text": last_text}
