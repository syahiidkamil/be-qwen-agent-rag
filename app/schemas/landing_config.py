from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LandingConfigOut(BaseModel):
    config: dict
    updated_at: datetime | None = None


class LandingConfigIn(BaseModel):
    config: dict
