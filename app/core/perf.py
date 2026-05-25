"""Ad-hoc latency probes for the chat pipeline.

Bundled with the latency-investigation PR; intended to be ripped out once
fixes land. Every probe emits a single line with the same prefix so a
later cleanup is one `grep -v "perf chat"` away:

    perf chat session=<sid> step=<label> ms=<float>

Two helpers:

- ``probe(label, session_id)`` — async context manager for "time this block"
- ``mark(label, t0, session_id)`` — manual stamp for cases the context
  manager can't express (e.g. time-to-first-token inside a streaming loop)
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

log = logging.getLogger("app.perf")


def _emit(label: str, ms: float, session_id: str | None) -> None:
    log.info("perf chat session=%s step=%s ms=%.1f", session_id or "-", label, ms)


@asynccontextmanager
async def probe(label: str, session_id: str | None = None) -> AsyncIterator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _emit(label, (time.perf_counter() - t0) * 1000, session_id)


def mark(label: str, t0: float, session_id: str | None = None) -> float:
    """Emit one probe line for `label` measured from `t0`. Returns elapsed ms."""
    ms = (time.perf_counter() - t0) * 1000
    _emit(label, ms, session_id)
    return ms
