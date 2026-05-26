"""Users CRUD endpoints — wrap Supabase Auth's admin API.

All four endpoints share the same auth gate (``require_role("admin")``)
which transitively permits ``super_admin`` via the privilege hierarchy in
``app.core.auth``. There is no Postgres ``users`` table — Supabase Auth is
the source of truth — so this router speaks to ``supabase.auth.admin``
exclusively. Deactivation is implemented by setting ``banned_until`` to a
far-future date (cheapest enforcement; the Supabase gotrue server checks
the timestamp on every token issue/refresh).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import AuthUser, require_role
from app.core.supabase import get_supabase
from app.schemas.user import Role, UserCreateIn, UserOut, UserStatus

log = logging.getLogger("app.api.users")

router = APIRouter(prefix="/api/users", tags=["users"])

# Both admin and super_admin can manage users; the require_role hierarchy
# expands "admin" to include super_admin.
_require_admin = require_role("admin")

# Deactivation = ban_until 100 years in the future. Reactivation clears it.
_FAR_FUTURE_BAN = timedelta(days=365 * 100)


# ---------- helpers ---------------------------------------------------------


def _project_user(raw: Any) -> UserOut:
    """Project a Supabase Admin User → our public UserOut."""
    # supabase-py returns User objects with attribute access; tolerate dicts
    # in case the SDK ever switches.
    def _attr(key: str, default: Any = None) -> Any:
        if isinstance(raw, dict):
            return raw.get(key, default)
        return getattr(raw, key, default)

    metadata = _attr("user_metadata") or {}
    role_value = metadata.get("role") if isinstance(metadata, dict) else None
    role: Role | None = role_value if role_value in ("super_admin", "admin", "user") else None

    banned_until_raw = _attr("banned_until")
    status_value: UserStatus = "active"
    if banned_until_raw:
        try:
            ts = banned_until_raw
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts > datetime.now(timezone.utc):
                    status_value = "deactivated"
        except (ValueError, TypeError):
            # Malformed timestamp — be conservative and treat as active.
            log.warning("Could not parse banned_until=%r on user %s", banned_until_raw, _attr("id"))

    created_at_raw = _attr("created_at")
    created_at: datetime | None = None
    if created_at_raw:
        try:
            created_at = (
                datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                if isinstance(created_at_raw, str)
                else created_at_raw
            )
        except (ValueError, TypeError):
            created_at = None

    return UserOut(
        id=str(_attr("id")),
        email=_attr("email") or "",
        role=role,
        status=status_value,
        created_at=created_at,
    )


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": code, "message": message}},
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": {"code": code, "message": message}},
    )


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "NOT_FOUND", "message": message}},
    )


def _bad_gateway(message: str) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={"error": {"code": "SUPABASE_UPSTREAM_ERROR", "message": message}},
    )


def _looks_like_email_conflict(exc: Exception) -> bool:
    """Heuristic for Supabase 'email already exists' errors.

    gotrue returns several shapes here (HTTPError with body, AuthError with
    `message`, dict-style); we check the stringified exception for the
    canonical phrasing.
    """
    msg = str(exc).lower()
    return (
        "already registered" in msg
        or "already exists" in msg
        or "duplicate" in msg
        or "email address" in msg and "exists" in msg
    )


# ---------- endpoints -------------------------------------------------------


@router.get("")
async def list_users(_: Annotated[AuthUser, Depends(_require_admin)]):
    """Return all users from Supabase Auth Admin API as the projected shape.

    No pagination — list is expected to stay under ~50 users per
    DECISIONS.md. We walk pages defensively in case the list grows.
    """
    sb = get_supabase()
    out: list[UserOut] = []
    page = 1
    per_page = 100
    try:
        while True:
            resp = sb.auth.admin.list_users(page=page, per_page=per_page)
            batch = list(resp) if resp else []
            for raw in batch:
                out.append(_project_user(raw))
            if len(batch) < per_page:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 — Supabase failures bubble to client
        log.exception("list_users failed")
        raise _bad_gateway(f"Supabase admin list_users failed: {exc}") from exc

    return {"data": [u.model_dump(mode="json") for u in out]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateIn,
    _: Annotated[AuthUser, Depends(_require_admin)],
):
    """Create a Supabase Auth user with the given role.

    Returns 409 on email collision (Supabase reports this through several
    error shapes; we sniff the message). 400 on validation failure (pydantic
    handles the obvious cases; the role literal makes the role check
    schema-level).
    """
    sb = get_supabase()
    try:
        result = sb.auth.admin.create_user(
            {
                "email": str(body.email),
                "password": body.password,
                "email_confirm": True,  # no email-verification flow in this project
                "user_metadata": {"role": body.role},
            }
        )
    except Exception as exc:  # noqa: BLE001
        if _looks_like_email_conflict(exc):
            raise _conflict("EMAIL_EXISTS", f"A user with that email already exists.") from exc
        log.exception("create_user failed for %s", body.email)
        raise _bad_gateway(f"Supabase admin create_user failed: {exc}") from exc

    raw_user = getattr(result, "user", None) if not isinstance(result, dict) else result.get("user")
    if raw_user is None:
        raise _bad_gateway("Supabase create_user returned no user object")
    return {"data": _project_user(raw_user).model_dump(mode="json")}


@router.patch("/{user_id}/deactivate")
async def deactivate_user(
    user_id: str,
    caller: Annotated[AuthUser, Depends(_require_admin)],
):
    """Set banned_until to far-future so the user can no longer sign in.

    Defense-in-depth: refuse self-deactivation server-side. The FE also
    hides the button on the caller's own row, but a hand-crafted request
    must still bounce.
    """
    if caller.sub == user_id:
        raise _bad_request(
            "SELF_DEACTIVATE_FORBIDDEN",
            "You cannot deactivate your own account.",
        )

    sb = get_supabase()
    # First confirm the user exists so we return a clean 404 instead of a
    # 502 with a Supabase error string.
    try:
        existing = sb.auth.admin.get_user_by_id(user_id)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "not found" in msg or "user not found" in msg:
            raise _not_found(f"User {user_id} not found") from exc
        log.exception("deactivate_user: lookup failed for %s", user_id)
        raise _bad_gateway(f"Supabase get_user_by_id failed: {exc}") from exc

    raw_user = (
        getattr(existing, "user", None) if not isinstance(existing, dict) else existing.get("user")
    )
    if raw_user is None:
        raise _not_found(f"User {user_id} not found")

    banned_until = datetime.now(timezone.utc) + _FAR_FUTURE_BAN
    try:
        result = sb.auth.admin.update_user_by_id(
            user_id,
            {"ban_duration": "876000h"},  # 100y in hours; supabase-py accepts duration strings
        )
    except Exception as exc:  # noqa: BLE001
        # Some supabase-py versions want banned_until directly instead.
        try:
            result = sb.auth.admin.update_user_by_id(
                user_id,
                {"banned_until": banned_until.isoformat()},
            )
        except Exception as exc2:  # noqa: BLE001
            log.exception("deactivate_user failed for %s", user_id)
            raise _bad_gateway(f"Supabase update_user_by_id failed: {exc2}") from exc2

    raw_updated = getattr(result, "user", None) if not isinstance(result, dict) else result.get("user")
    if raw_updated is None:
        raw_updated = raw_user  # fall back to the lookup payload
    return {"data": _project_user(raw_updated).model_dump(mode="json")}


@router.patch("/{user_id}/reactivate")
async def reactivate_user(
    user_id: str,
    _: Annotated[AuthUser, Depends(_require_admin)],
):
    """Clear banned_until so the user can sign in again. Idempotent."""
    sb = get_supabase()
    try:
        existing = sb.auth.admin.get_user_by_id(user_id)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "not found" in msg:
            raise _not_found(f"User {user_id} not found") from exc
        log.exception("reactivate_user: lookup failed for %s", user_id)
        raise _bad_gateway(f"Supabase get_user_by_id failed: {exc}") from exc

    raw_user = (
        getattr(existing, "user", None) if not isinstance(existing, dict) else existing.get("user")
    )
    if raw_user is None:
        raise _not_found(f"User {user_id} not found")

    try:
        # The Supabase docs document "none" as the magic ban_duration to clear it.
        result = sb.auth.admin.update_user_by_id(user_id, {"ban_duration": "none"})
    except Exception as exc:  # noqa: BLE001
        # Some supabase-py builds use banned_until=null instead.
        try:
            result = sb.auth.admin.update_user_by_id(user_id, {"banned_until": None})
        except Exception as exc2:  # noqa: BLE001
            log.exception("reactivate_user failed for %s", user_id)
            raise _bad_gateway(f"Supabase update_user_by_id failed: {exc2}") from exc2

    raw_updated = getattr(result, "user", None) if not isinstance(result, dict) else result.get("user")
    if raw_updated is None:
        raw_updated = raw_user
    return {"data": _project_user(raw_updated).model_dump(mode="json")}
