"""FastAPI dependency that verifies a Supabase access token.

Supabase migrated to asymmetric JWT Signing Keys: new session tokens are
signed with the project's ECC (P-256) private key as ES256. We verify by
fetching the matching public key from the project's JWKS endpoint:

    {SUPABASE_URL}/auth/v1/.well-known/jwks.json

The Legacy HS256 Shared Secret may still appear in the JWKS for tokens
issued before the rotation; python-jose handles `oct` keys the same way.
We also retain a config-level fallback to ``SUPABASE_JWT_SECRET`` so a
fully legacy project (no signing-keys migration) keeps working.

For now, *any* authenticated user is treated as admin. Tighten this with
a role check (e.g., a `is_admin` flag in user metadata, or an `admins`
table) before opening Supabase signups to the public.
"""
from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)

_JWKS_TTL_SECONDS = 600
_jwks_cache: dict[str, dict[str, Any]] = {}
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()


class AuthUser:
    def __init__(self, sub: str, email: str | None, raw: dict):
        self.sub = sub
        self.email = email
        self.raw = raw


def _unauthorized(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": code, "message": detail}},
        headers={"WWW-Authenticate": "Bearer"},
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


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser:
    if credentials is None:
        raise _unauthorized("MISSING_TOKEN", "Missing bearer token")
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
