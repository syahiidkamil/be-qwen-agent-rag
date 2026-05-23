"""Create (or upsert) the demo admin user in Supabase Auth.

The admin user is a row in Supabase's managed `auth.users` table, not in any
of our application tables — so we provision it via the Supabase admin API
(service_role key) rather than via Alembic.

Usage
-----
With the backend venv active and `.env` populated (SUPABASE_URL +
SUPABASE_SECRET_KEY required):

    python -m app.scripts.seed_admin
    python -m app.scripts.seed_admin --email me@example.com --password 'MyPw!'

Idempotent: if the user already exists, the password is reset to the value
passed in (lets you recover a forgotten dev password by re-running the
script).

DEV ONLY: the default password is hard-coded for demo convenience. Change
it before exposing the project to anyone outside your machine.
"""
from __future__ import annotations

import argparse
import os
import sys

from app.core.config import get_settings
from app.core.supabase import get_supabase

DEFAULT_EMAIL = "admin@airanext.id"
DEFAULT_PASSWORD = "Qwenragadmin123!"  # dev-only default; override via --password


def _find_user_by_email(sb, email: str):
    """supabase-py paginates list_users(); walk pages until we find a match."""
    page = 1
    per_page = 100
    while True:
        resp = sb.auth.admin.list_users(page=page, per_page=per_page)
        # The shape returned varies a bit between supabase-py versions.
        # In 2.x it's an iterable of User objects.
        users = list(resp) if resp else []
        for user in users:
            if getattr(user, "email", None) == email:
                return user
        if len(users) < per_page:
            return None
        page += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email",
        default=os.environ.get("SEED_ADMIN_EMAIL", DEFAULT_EMAIL),
        help=f"Admin email (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("SEED_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        help="Admin password (default: the one set in this script)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env",
            file=sys.stderr,
        )
        return 1

    sb = get_supabase()
    existing = _find_user_by_email(sb, args.email)

    user_metadata = {"role": "admin"}

    if existing is not None:
        sb.auth.admin.update_user_by_id(
            existing.id,
            {
                "password": args.password,
                "email_confirm": True,
                "user_metadata": user_metadata,
            },
        )
        print(f"updated  {args.email}  (id={existing.id})")
    else:
        result = sb.auth.admin.create_user(
            {
                "email": args.email,
                "password": args.password,
                "email_confirm": True,  # skip the email verification flow
                "user_metadata": user_metadata,
            }
        )
        user_id = getattr(result.user, "id", None) if hasattr(result, "user") else None
        print(f"created  {args.email}  (id={user_id})")

    print()
    print("Sign in at the frontend with:")
    print(f"  Email:    {args.email}")
    print(f"  Password: {args.password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
