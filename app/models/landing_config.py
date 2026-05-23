import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LandingConfig(Base):
    """Single-row table holding the current landing page config blob."""
    __tablename__ = "landing_config"

    # Singleton id; enforced to 1 by DB check constraint in the migration.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
