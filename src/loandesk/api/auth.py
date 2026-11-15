"""Admin access: a bearer token issued by any OIDC provider (Supabase Auth, Okta, Entra ID).

The token is checked against the provider's published signing keys (JWKS), audience and
issuer. With no JWKS URL configured, admin routes are open to requests from localhost only.
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import HTTPException, Request

from loandesk.config import get_settings


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def require_admin(request: Request) -> dict:
    settings = get_settings()
    if settings.admin_dev_open:
        return {"sub": "dev-open"}
    if not settings.admin_jwks_url:
        host = request.client.host if request.client else ""
        if host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return {"sub": "local-dev"}
        raise HTTPException(403, "Admin auth is not configured; only localhost may connect.")

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = header.split(" ", 1)[1]
    try:
        key = _jwks_client(settings.admin_jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                          audience=settings.admin_audience or None,
                          issuer=settings.admin_issuer or None,
                          options={"verify_aud": bool(settings.admin_audience)})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}") from e
