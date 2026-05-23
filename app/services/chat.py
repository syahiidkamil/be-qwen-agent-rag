"""Build a Qwen-Agent Assistant grounded in retrieved chunks and stream tokens."""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.retrieval import RetrievedChunk, retrieve

GROUNDING_INSTRUCTION = """You are a knowledgebase assistant. Answer ONLY using
the CONTEXT passages below. If the answer is not in the context, say you
don't know — do not make up information from your general training.

Cite sources at the end of relevant sentences using the format [doc:<document_id>].
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
) -> AsyncIterator[dict]:
    """Yield SSE-event-shaped dicts: {'type': 'token'|'sources'|'done', ...}."""
    # Imported lazily — qwen-agent has heavy deps.
    from qwen_agent.agents import Assistant

    settings = get_settings()
    chunks = await retrieve(session, query, top_k=8)

    # Surface sources to the UI before streaming tokens.
    yield {
        "type": "sources",
        "sources": [
            {"chunk_id": str(c.id), "document_id": str(c.document_id), "score": c.score}
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
    bot = Assistant(llm=llm_cfg, system_message=system_message)

    # Build the message list (history + new user turn).
    messages = list(history) + [{"role": "user", "content": query}]

    last_text = ""
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
            yield {"type": "token", "delta": delta}

    yield {"type": "done", "full_text": last_text}
