from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, model_validator

ChatMode = Literal["public", "internal"]
_VALID_CHAT_MODES: tuple[str, ...] = get_args(ChatMode)


class LandingConfigOut(BaseModel):
    """Public read shape. Defensive: if the row pre-dates chat_mode (or
    contains a junk value), normalise to 'public' so the FE never sees an
    invalid mode."""

    config: dict
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _normalise_chat_mode(self) -> "LandingConfigOut":
        if self.config.get("chat_mode") not in _VALID_CHAT_MODES:
            self.config["chat_mode"] = "public"
        return self


class LandingConfigIn(BaseModel):
    """Write shape. Validates chat_mode strictly when present — direct DB
    tampering can produce junk values, but the API surface won't accept them."""

    config: dict

    @model_validator(mode="after")
    def _validate_chat_mode(self) -> "LandingConfigIn":
        cm = self.config.get("chat_mode")
        if cm is not None and cm not in _VALID_CHAT_MODES:
            raise ValueError(
                f"chat_mode must be one of {_VALID_CHAT_MODES}, got {cm!r}"
            )
        return self
