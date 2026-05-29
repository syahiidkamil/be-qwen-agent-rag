from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# Defaults + bounds for the retrieval top-K knob. The cap matches the
# clamp in the corpus-search endpoint (documents.py) so the two surfaces
# agree on a sane ceiling.
DEFAULT_RETRIEVAL_TOP_K = 8
MIN_RETRIEVAL_TOP_K = 1
MAX_RETRIEVAL_TOP_K = 20


def resolve_retrieval_top_k(config: dict | None) -> int:
    """Read retrieval_top_k from a stored config blob, clamping to bounds.

    Used by chat.py at request time so an out-of-range value sitting in
    the DB (set before bounds were tightened, or via direct SQL) can't
    explode the retrieval call.
    """
    if not config:
        return DEFAULT_RETRIEVAL_TOP_K
    raw = config.get("retrieval_top_k", DEFAULT_RETRIEVAL_TOP_K)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETRIEVAL_TOP_K
    return max(MIN_RETRIEVAL_TOP_K, min(value, MAX_RETRIEVAL_TOP_K))


class SystemConfigOut(BaseModel):
    config: dict
    updated_at: datetime | None = None


class SystemConfigIn(BaseModel):
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_retrieval_top_k(self) -> "SystemConfigIn":
        raw = self.config.get("retrieval_top_k")
        if raw is None:
            return self
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError("retrieval_top_k must be an integer")
        if not MIN_RETRIEVAL_TOP_K <= value <= MAX_RETRIEVAL_TOP_K:
            raise ValueError(
                f"retrieval_top_k must be between {MIN_RETRIEVAL_TOP_K} and {MAX_RETRIEVAL_TOP_K}"
            )
        self.config["retrieval_top_k"] = value
        return self
