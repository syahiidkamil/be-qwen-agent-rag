"""GET + PUT for the singleton system_config row.

Both verbs are admin-gated (require_role("admin") transitively allows
super_admin). Unlike landing_config, this one isn't public-readable —
infra knobs shouldn't leak to anonymous landing visitors.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_role
from app.core.db import get_db
from app.models.system_config import SystemConfig
from app.schemas.system_config import SystemConfigIn, SystemConfigOut

router = APIRouter(prefix="/api/system-config", tags=["system-config"])

_require_admin = require_role("admin")


async def _load_or_default(session: AsyncSession) -> SystemConfig | None:
    result = await session.execute(select(SystemConfig).where(SystemConfig.id == 1))
    return result.scalar_one_or_none()


@router.get("")
async def get_system_config(
    _: Annotated[AuthUser, Depends(_require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _load_or_default(session)
    if row is None:
        return {"data": SystemConfigOut(config={}, updated_at=None).model_dump(mode="json")}
    return {
        "data": SystemConfigOut(config=row.config, updated_at=row.updated_at).model_dump(
            mode="json"
        )
    }


@router.put("")
async def save_system_config(
    body: SystemConfigIn,
    user: Annotated[AuthUser, Depends(_require_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _load_or_default(session)
    if row is None:
        row = SystemConfig(
            id=1,
            config=body.config,
            updated_by=uuid.UUID(user.sub) if user.sub else None,
        )
        session.add(row)
    else:
        row.config = body.config
        row.updated_by = uuid.UUID(user.sub) if user.sub else None
    await session.commit()
    await session.refresh(row)
    return {
        "data": SystemConfigOut(config=row.config, updated_at=row.updated_at).model_dump(
            mode="json"
        )
    }
