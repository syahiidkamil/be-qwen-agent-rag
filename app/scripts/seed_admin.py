"""Bootstrap the demo users for the roles-refactor phase.

Creates three Supabase Auth users — one per role — with passwords sourced
from environment variables, with a documented dev fallback. The script is
idempotent: re-running updates each existing user to match the desired
state (password + user_metadata.role).

Usage
-----
With the backend venv active and `.env` populated (SUPABASE_URL +
SUPABASE_SECRET_KEY required):

    python -m app.scripts.seed_admin

Env vars (recommended in any non-dev environment):

    SEED_SUPER_PASSWORD   — password for super.boss@airanext.id (super_admin)
    SEED_ADMIN_PASSWORD   — password for admin@airanext.id      (admin)
    SEED_USER_PASSWORD    — password for cs.demo@airanext.id    (user)

When any of these is unset, that user falls back to ``DEV_FALLBACK_PASSWORD``
and the script logs a WARNING per fallback — production never silently uses
the dev password.

Exit codes: 0 on success; 1 on configuration error; 2 on Supabase failure.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.supabase import get_supabase

# Discoverable at the top of the file so dev knows where the fallback comes from.
DEV_FALLBACK_PASSWORD = "Qwenragadmin123!"

logger = logging.getLogger("app.seed")


@dataclass(frozen=True)
class SeedSpec:
    email: str
    role: str
    password_env: str


SEED_SPECS: tuple[SeedSpec, ...] = (
    SeedSpec(
        email="super.boss@airanext.id",
        role="super_admin",
        password_env="SEED_SUPER_PASSWORD",
    ),
    SeedSpec(
        email="admin@airanext.id",
        role="admin",
        password_env="SEED_ADMIN_PASSWORD",
    ),
    SeedSpec(
        email="cs.demo@airanext.id",
        role="user",
        password_env="SEED_USER_PASSWORD",
    ),
)


def _resolve_password(spec: SeedSpec) -> tuple[str, bool]:
    """Return (password, used_fallback). Warn on fallback so it's never silent."""
    value = os.environ.get(spec.password_env)
    if value:
        return value, False
    logger.warning(
        "env var %s is unset — using DEV_FALLBACK_PASSWORD for %s. "
        "Set this in .env (or your deployment secrets) before going to production.",
        spec.password_env,
        spec.email,
    )
    return DEV_FALLBACK_PASSWORD, True


def _find_user_by_email(sb, email: str):
    """supabase-py paginates list_users(); walk pages until we find a match."""
    page = 1
    per_page = 100
    while True:
        resp = sb.auth.admin.list_users(page=page, per_page=per_page)
        # In supabase-py 2.x, list_users returns an iterable of User objects.
        users = list(resp) if resp else []
        for user in users:
            if getattr(user, "email", None) == email:
                return user
        if len(users) < per_page:
            return None
        page += 1


def _seed_one(sb, spec: SeedSpec) -> tuple[str, str | None]:
    """Create or update one Supabase Auth user. Returns (status, user_id)."""
    password, _ = _resolve_password(spec)
    metadata = {"role": spec.role}

    existing = _find_user_by_email(sb, spec.email)
    if existing is not None:
        sb.auth.admin.update_user_by_id(
            existing.id,
            {
                "password": password,
                "email_confirm": True,
                "user_metadata": metadata,
            },
        )
        return "updated", existing.id

    result = sb.auth.admin.create_user(
        {
            "email": spec.email,
            "password": password,
            "email_confirm": True,  # skip the email verification flow
            "user_metadata": metadata,
        }
    )
    user_id = getattr(result.user, "id", None) if hasattr(result, "user") else None
    return "created", user_id


def _configure_logging() -> None:
    """Show INFO/WARNING on stdout so the operator sees fallback notices."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> int:
    _configure_logging()
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        logger.error("SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env")
        return 1

    sb = get_supabase()
    summary: list[tuple[str, str, str, str | None]] = []
    for spec in SEED_SPECS:
        try:
            status_str, user_id = _seed_one(sb, spec)
        except Exception as exc:  # noqa: BLE001 — surface Supabase failures verbatim
            logger.error("seeding %s failed: %s", spec.email, exc)
            return 2
        summary.append((status_str, spec.email, spec.role, user_id))

    print()
    print("Seed summary:")
    for status_str, email, role, user_id in summary:
        print(f"  {status_str:<8}  {email:<28}  role={role:<12}  id={user_id}")
    print()
    print("Sign in at the frontend with one of the seeded emails plus its password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
