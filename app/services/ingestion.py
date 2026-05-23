"""Orchestrate parse → embed → insert chunks for a single document."""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import _sessionmaker
from app.models.document import Chunk, Document, IngestStatus
from app.services import storage
from app.services.embedding import embed_batch
from app.services.parsing import parse_to_chunks

log = logging.getLogger(__name__)


async def ingest_document(document_id: uuid.UUID) -> None:
    """Run the full ingestion pipeline for a single document.

    Designed to be invoked as a FastAPI BackgroundTask. Errors are caught and
    persisted to the document row so the UI can surface them.
    """
    async with _sessionmaker()() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            log.warning("ingest_document: %s not found", document_id)
            return
        await _set_status(session, doc, IngestStatus.ingesting, error=None)

    try:
        # Download → temp file → parse → embed → insert
        async with _sessionmaker()() as session:
            doc = await session.get(Document, document_id)
            if doc is None:
                return
            data = storage.download_bytes(doc.storage_path)
            suffix = Path(doc.filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

        chunks_text = parse_to_chunks(tmp_path)
        if not chunks_text:
            raise RuntimeError("DocParser produced zero chunks")

        embeddings = await embed_batch(chunks_text)
        if len(embeddings) != len(chunks_text):
            raise RuntimeError(
                f"Embedding count mismatch: {len(embeddings)} vs {len(chunks_text)} chunks"
            )

        async with _sessionmaker()() as session:
            session.add_all(
                Chunk(
                    document_id=document_id,
                    chunk_index=i,
                    content=text,
                    embedding=emb,
                )
                for i, (text, emb) in enumerate(zip(chunks_text, embeddings))
            )
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    status=IngestStatus.ingested,
                    chunk_count=len(chunks_text),
                    error_message=None,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — we want any failure persisted
        log.exception("Ingestion failed for %s", document_id)
        async with _sessionmaker()() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    status=IngestStatus.failed,
                    error_message=str(exc)[:1000],
                )
            )
            await session.commit()


async def _set_status(
    session: AsyncSession,
    doc: Document,
    status: IngestStatus,
    error: str | None,
) -> None:
    doc.status = status
    doc.error_message = error
    await session.commit()
