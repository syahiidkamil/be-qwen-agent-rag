"""Pydantic request + response models for the Users CRUD endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Role = Literal["super_admin", "admin", "user"]
UserStatus = Literal["active", "deactivated"]


class UserCreateIn(BaseModel):
    """Body for POST /api/users.

    Password is required (min 8 chars) because Supabase Auth has no
    email-invitation flow in this project — the admin shares the password
    out-of-band via the post-create dialog.
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role


class UserOut(BaseModel):
    """Projection sent to the frontend. Hide internal Supabase fields."""

    id: str
    email: str
    role: Role | None  # users seeded outside the system may not have one
    status: UserStatus
    created_at: datetime | None
