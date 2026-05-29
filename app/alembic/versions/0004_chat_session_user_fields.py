"""add user_id, title, last_message_at to chat_sessions

Revision ID: 0004_chat_session_user_fields
Revises: 0003_system_config
Create Date: 2026-05-30

Backs the per-user chat-session sidebar on the AI Help page. Sessions
were anonymous (only anonymous_id + started_at); we now scope them to the
signed-in user, give each a human-readable title (auto-derived from the
first message, then user-editable), and track last activity for recency
ordering.

All three columns are additive and safe on existing rows:
  - user_id stays NULL for pre-existing/anonymous sessions (they simply
    won't surface in any user's sidebar).
  - title stays NULL for legacy rows; the API/FE fall back to a default.
  - last_message_at is NOT NULL with a now() default; we backfill existing
    rows from started_at so ordering is sensible from day one.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = "0004_chat_session_user_fields"
down_revision = "0003_system_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("title", sa.String(120), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Seed last_message_at from started_at for rows that predate this column.
    op.execute("UPDATE chat_sessions SET last_message_at = started_at")
    op.create_index(
        "chat_sessions_user_id_idx", "chat_sessions", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("chat_sessions_user_id_idx", table_name="chat_sessions")
    op.drop_column("chat_sessions", "last_message_at")
    op.drop_column("chat_sessions", "title")
    op.drop_column("chat_sessions", "user_id")
