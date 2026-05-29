"""add system_config singleton table

Revision ID: 0003_system_config
Revises: 0002_documents_tags
Create Date: 2026-05-29

Operational tuning knobs for the live system — currently just the
retrieval top-K used by chat. Kept in its own table (not folded into
landing_config) because the two are different concerns: landing_config
travels with brand presets that admins import/export, while
system_config is per-deployment infra tuning that must NOT swap when
the marketing preset changes.

Mirrors landing_config's singleton pattern: id check-constrained to 1,
JSONB blob, last-touched audit columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers
revision = "0003_system_config"
down_revision = "0002_documents_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True)),
        sa.CheckConstraint("id = 1", name="system_config_singleton"),
    )


def downgrade() -> None:
    op.drop_table("system_config")
