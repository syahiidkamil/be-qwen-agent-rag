"""GET (public) + PUT (admin) for the singleton landing-page config blob."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, get_current_admin
from app.core.db import get_db
from app.models.landing_config import LandingConfig
from app.schemas.landing_config import LandingConfigIn, LandingConfigOut

router = APIRouter(prefix="/api/landing-config", tags=["landing-config"])


@router.get("")
async def get_landing_config(session: Annotated[AsyncSession, Depends(get_db)]):
    result = await session.execute(select(LandingConfig).where(LandingConfig.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        return {"data": LandingConfigOut(config={}, updated_at=None).model_dump(mode="json")}
    return {
        "data": LandingConfigOut(config=row.config, updated_at=row.updated_at).model_dump(
            mode="json"
        )
    }


@router.put("")
async def save_landing_config(
    body: LandingConfigIn,
    user: Annotated[AuthUser, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await session.execute(select(LandingConfig).where(LandingConfig.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = LandingConfig(
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
        "data": LandingConfigOut(config=row.config, updated_at=row.updated_at).model_dump(
            mode="json"
        )
    }
