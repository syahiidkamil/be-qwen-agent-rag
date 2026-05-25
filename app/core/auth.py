"""FastAPI dependencies that verify Supabase tokens and enforce roles.

Supabase migrated to asymmetric JWT Signing Keys: new session tokens are
signed with the project's ECC (P-256) private key as ES256. We verify by
fetching the matching public key from the project's JWKS endpoint:

    {SUPABASE_URL}/auth/v1/.well-known/jwks.json

The Legacy HS256 Shared Secret may still appear in the JWKS for tokens
issued before the rotation; python-jose handles ``oct`` keys the same way.
We also retain a config-level fallback to ``SUPABASE_JWT_SECRET`` so a
fully legacy project (no signing-keys migration) keeps working.

Authorization is performed by ``require_role(*roles)``: it decodes the
token, reads ``user_metadata.role`` from the verified claims, and raises
403 when the caller's role is missing, unknown, or not in the allowed
set. ``get_current_admin`` is a thin alias for
``require_role("admin")`` (which transitively allows super_admin via the
privilege hierarchy below). ``get_current_user_optional`` is for routes
that accept both authenticated and anonymous callers (the chat endpoint
in internal-mode gating, for example).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.auth")

_bearer = HTTPBearer(auto_error=False)

_JWKS_TTL_SECONDS = 600
_jwks_cache: dict[str, dict[str, Any]] = {}
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()

# Roles recognized by the system. Anything outside this set is treated as
# unknown and rejected at the role check.
KNOWN_ROLES: tuple[str, ...] = ("super_admin", "admin", "user")


class AuthUser:
    def __init__(self, sub: str, email: str | None, raw: dict):
        self.sub = sub
        self.email = email
        self.raw = raw

    @property
    def role(self) -> str | None:
        """Extract role from `user_metadata.role`. Returns None if absent or non-string."""
        metadata = self.raw.get("user_metadata") or {}
        value = metadata.get("role")
        return value if isinstance(value, str) else None


def _unauthorized(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": code, "message": detail}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"code": code, "message": detail}},
    )


def _jwks_url(settings: Settings) -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


async def _fetch_jwks(settings: Settings) -> None:
    """Replace the in-memory JWKS cache. Caller must hold ``_jwks_lock``."""
    global _jwks_fetched_at
    url = _jwks_url(settings)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    payload = resp.json()
    keys = payload.get("keys") or []
    _jwks_cache.clear()
    for jwk in keys:
        kid = jwk.get("kid")
        if kid:
            _jwks_cache[kid] = jwk
    _jwks_fetched_at = time.monotonic()


async def _get_jwk(kid: str, settings: Settings) -> dict[str, Any] | None:
    now = time.monotonic()
    stale = (now - _jwks_fetched_at) > _JWKS_TTL_SECONDS
    if kid in _jwks_cache and not stale:
        return _jwks_cache[kid]
    async with _jwks_lock:
        # Re-check inside the lock; another coroutine may have just refreshed.
        if kid in _jwks_cache and (time.monotonic() - _jwks_fetched_at) <= _JWKS_TTL_SECONDS:
            return _jwks_cache[kid]
        try:
            await _fetch_jwks(settings)
        except (httpx.HTTPError, ValueError) as exc:
            raise _unauthorized("JWKS_FETCH_FAILED", f"Could not load JWKS: {exc}") from exc
    return _jwks_cache.get(kid)


def _decode_with_jwk(
    token: str, jwk: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    alg = jwk.get("alg") or _default_alg_for_kty(jwk.get("kty"))
    if not alg:
        raise _unauthorized("UNKNOWN_ALG", "JWK is missing `alg` and `kty`")
    return jwt.decode(
        token,
        jwk,
        algorithms=[alg],
        audience="authenticated",
        issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
    )


def _default_alg_for_kty(kty: str | None) -> str | None:
    # Supabase issues ES256 for ECC P-256 and HS256 for shared secrets.
    if kty == "EC":
        return "ES256"
    if kty == "oct":
        return "HS256"
    if kty == "RSA":
        return "RS256"
    return None


def _decode_with_hs256_fallback(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.supabase_jwt_secret:
        raise _unauthorized("UNKNOWN_KID", "Token kid not in JWKS and no HS256 fallback configured")
    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        algorithms=["HS256"],
        audience="authenticated",
        issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
    )


async def _decode_token(
    credentials: HTTPAuthorizationCredentials,
    settings: Settings,
) -> AuthUser:
    """Verify a Supabase bearer token and return the AuthUser.

    Raises 401 when the token is missing required claims, malformed, or
    fails signature verification. Does not enforce any role policy.
    """
    if not settings.supabase_url:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED",
                              "message": "SUPABASE_URL is not set"}},
        )

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise _unauthorized("MALFORMED_TOKEN", f"Could not parse token header: {exc}") from exc

    kid = header.get("kid")
    try:
        if kid:
            jwk = await _get_jwk(kid, settings)
            if jwk is not None:
                claims = _decode_with_jwk(token, jwk, settings)
            else:
                # kid present but not in JWKS — try HS256 fallback (covers tokens
                # signed with the legacy shared secret before signing-keys migration).
                claims = _decode_with_hs256_fallback(token, settings)
        else:
            claims = _decode_with_hs256_fallback(token, settings)
    except JWTError as exc:
        raise _unauthorized("INVALID_TOKEN", f"Invalid token: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("INVALID_TOKEN", "Token missing subject")
    return AuthUser(sub=sub, email=claims.get("email"), raw=claims)


def _expand_role_hierarchy(roles: tuple[str, ...]) -> frozenset[str]:
    """Expand the allowed set to include higher-privilege roles.

    super_admin > admin > user. A route that allows "admin" implicitly
    allows "super_admin" since super-admins have a superset of admin
    privileges. Likewise, "user" implies "admin" and "super_admin".
    """
    expanded = set(roles)
    if "user" in expanded:
        expanded.update(("admin", "super_admin"))
    if "admin" in expanded:
        expanded.add("super_admin")
    return frozenset(expanded)


def require_role(*allowed_roles: str):
    """Return a FastAPI dependency that enforces the caller's role.

    Behavior:
        - 401 if no bearer token is present, or if the token is malformed,
          expired, or has an invalid signature.
        - 403 if the token is valid but `user_metadata.role` is missing,
          unknown, or not in the allowed set (after hierarchy expansion).
        - Returns the AuthUser on success.

    The hierarchy is enforced via ``_expand_role_hierarchy``: ``require_role("admin")``
    accepts both `admin` and `super_admin`; ``require_role("user")`` accepts all three.
    """
    if not allowed_roles:
        raise ValueError("require_role requires at least one allowed role")
    for role in allowed_roles:
        if role not in KNOWN_ROLES:
            raise ValueError(f"Unknown role passed to require_role: {role!r}")

    permitted = _expand_role_hierarchy(allowed_roles)

    async def dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> AuthUser:
        if credentials is None:
            raise _unauthorized("MISSING_TOKEN", "Missing bearer token")
        user = await _decode_token(credentials, settings)
        role = user.role
        if role is None:
            raise _forbidden(
                "FORBIDDEN",
                "Account role missing — contact your administrator",
            )
        if role not in KNOWN_ROLES:
            logger.info("Unknown role on token: sub=%s role=%r", user.sub, role)
            raise _forbidden("FORBIDDEN", "Unrecognized account role")
        if role not in permitted:
            raise _forbidden(
                "FORBIDDEN",
                f"This action requires one of: {', '.join(sorted(permitted))}",
            )
        return user

    dependency.__name__ = f"require_role_{'_'.join(allowed_roles)}"
    return dependency


async def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser | None:
    """Return AuthUser when a valid token is present; None when no token at all.

    Used by endpoints that legitimately accept anonymous callers (e.g. the
    chat endpoint in public mode). A present-but-invalid token still raises
    401 — anonymity is distinct from a broken credential.
    """
    if credentials is None:
        return None
    return await _decode_token(credentials, settings)


# Backwards-compatible alias for existing routes that called `get_current_admin`
# before role enforcement existed. require_role("admin") accepts both admin and
# super_admin via the privilege hierarchy.
get_current_admin = require_role("admin")
get_current_admin.__name__ = "get_current_admin"
