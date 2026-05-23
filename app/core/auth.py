"""FastAPI dependency that verifies a Supabase access token.

The Supabase access token is a standard JWT signed with the project's
JWT secret (Settings -> API -> JWT Settings -> JWT Secret). We verify it
locally to keep `/admin/*` request handling cheap and offline-friendly.

For now, *any* authenticated user is treated as admin. Tighten this with
a role check (e.g., a `is_admin` flag in user metadata, or an `admins`
table) before opening Supabase signups to the public.
"""
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


class AuthUser:
    def __init__(self, sub: str, email: str | None, raw: dict):
        self.sub = sub
        self.email = email
        self.raw = raw


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "UNAUTHORIZED", "message": detail}},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser:
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED",
                              "message": "SUPABASE_JWT_SECRET is not set"}},
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("Token missing subject")
    return AuthUser(sub=sub, email=claims.get("email"), raw=claims)
