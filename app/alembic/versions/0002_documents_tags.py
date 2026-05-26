"""add tags JSONB column to documents

Revision ID: 0002_documents_tags
Revises: 0001_init
Create Date: 2026-05-25

Adds a JSONB array of free-form tag strings to each document. The list
is server-defaulted to `[]` so existing rows backfill automatically.
JSONB is chosen over `text[]` to mirror landing_config.config and
chat_messages.sources (the existing array-of-strings precedent in this
schema is JSON, not Postgres native arrays).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "0002_documents_tags"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "tags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "tags")
