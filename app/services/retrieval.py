"""Hybrid retrieval: pgvector cosine similarity + Postgres FTS,
combined via Reciprocal Rank Fusion (RRF)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding import embed_batch

RRF_K = 60  # smoothing constant; classic default


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float


async def retrieve(
    session: AsyncSession,
    query: str,
    top_k: int = 8,
    candidate_k: int = 25,
) -> list[RetrievedChunk]:
    """Return the top_k most relevant chunks for `query`."""
    if not query.strip():
        return []

    # Vector arm: embed query once and rank by cosine distance.
    q_emb = (await embed_batch([query]))[0]
    q_emb_literal = "[" + ",".join(f"{x:.8f}" for x in q_emb) + "]"

    vector_sql = text(
        f"""
        SELECT id, document_id, content
        FROM chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> '{q_emb_literal}'::vector
        LIMIT :k
        """
    )
    fts_sql = text(
        """
        SELECT id, document_id, content
        FROM chunks
        WHERE tsv @@ plainto_tsquery('english', :q)
        ORDER BY ts_rank(tsv, plainto_tsquery('english', :q)) DESC
        LIMIT :k
        """
    )

    # AsyncSession is single-flight: run queries sequentially, not via gather.
    vector_rows = await session.execute(vector_sql, {"k": candidate_k})
    fts_rows = await session.execute(fts_sql, {"q": query, "k": candidate_k})

    # Reciprocal Rank Fusion: score = sum(1 / (k + rank)) across rankings
    scores: dict[uuid.UUID, float] = {}
    payload: dict[uuid.UUID, RetrievedChunk] = {}

    def _accumulate(rows):
        for rank, row in enumerate(rows.mappings().all(), start=1):
            chunk_id = row["id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
            if chunk_id not in payload:
                payload[chunk_id] = RetrievedChunk(
                    id=chunk_id,
                    document_id=row["document_id"],
                    content=row["content"],
                    score=0.0,  # set below
                )

    _accumulate(vector_rows)
    _accumulate(fts_rows)

    for chunk_id, total in scores.items():
        payload[chunk_id].score = total

    ranked = sorted(payload.values(), key=lambda c: c.score, reverse=True)
    return ranked[:top_k]
