from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# Defaults + bounds for the retrieval knobs.
#
# `retrieval_top_k` caps the chunk count (prompt budget). Its 20 ceiling
# matches the clamp in the corpus-search endpoint (documents.py).
#
# `retrieval_max_files` caps the number of distinct source documents
# represented in the answer — controls citation focus and stops a
# single doc from monopolising the context. Walk chunks in RRF rank
# order, keep chunks whose document is among the first N distinct
# documents seen.
DEFAULT_RETRIEVAL_TOP_K = 8
MIN_RETRIEVAL_TOP_K = 1
MAX_RETRIEVAL_TOP_K = 20

DEFAULT_RETRIEVAL_MAX_FILES = 3
MIN_RETRIEVAL_MAX_FILES = 1
MAX_RETRIEVAL_MAX_FILES = 10


def _clamp_int(raw, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


def resolve_retrieval_top_k(config: dict | None) -> int:
    """Read retrieval_top_k from a stored config blob, clamping to bounds."""
    if not config:
        return DEFAULT_RETRIEVAL_TOP_K
    return _clamp_int(
        config.get("retrieval_top_k", DEFAULT_RETRIEVAL_TOP_K),
        DEFAULT_RETRIEVAL_TOP_K,
        MIN_RETRIEVAL_TOP_K,
        MAX_RETRIEVAL_TOP_K,
    )


def resolve_retrieval_max_files(config: dict | None) -> int:
    """Read retrieval_max_files from a stored config blob, clamping to bounds."""
    if not config:
        return DEFAULT_RETRIEVAL_MAX_FILES
    return _clamp_int(
        config.get("retrieval_max_files", DEFAULT_RETRIEVAL_MAX_FILES),
        DEFAULT_RETRIEVAL_MAX_FILES,
        MIN_RETRIEVAL_MAX_FILES,
        MAX_RETRIEVAL_MAX_FILES,
    )


class SystemConfigOut(BaseModel):
    config: dict
    updated_at: datetime | None = None


class SystemConfigIn(BaseModel):
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "SystemConfigIn":
        self._validate_int_field(
            "retrieval_top_k", MIN_RETRIEVAL_TOP_K, MAX_RETRIEVAL_TOP_K
        )
        self._validate_int_field(
            "retrieval_max_files",
            MIN_RETRIEVAL_MAX_FILES,
            MAX_RETRIEVAL_MAX_FILES,
        )
        return self

    def _validate_int_field(self, key: str, lo: int, hi: int) -> None:
        raw = self.config.get(key)
        if raw is None:
            return
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer")
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        self.config[key] = value
