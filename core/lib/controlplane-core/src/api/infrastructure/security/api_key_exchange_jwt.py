"""HS256 JWT minted by the gateway after API key validation (not Keycloak)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from jwt import InvalidTokenError

from api.core.config import settings

ISSUER = "https://panopticon.internal/api-key"
ALG = "HS256"
TTL_SECONDS = 300


def mint_api_key_session_jwt(
    *,
    key_record_id: uuid.UUID,
    tenant_id: uuid.UUID,
    scopes: list[str],
    purpose: str | None = None,
) -> str:
    """Short-lived JWT so downstream validate_access_token accepts API-key callers."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": f"apikey:{key_record_id}",
        "iss": ISSUER,
        "azp": "controlplane-api-key",
        "iat": now,
        "exp": now + TTL_SECONDS,
        "panopticon_auth": "api_key",
        "https://panopticon.dev/tenant_id": str(tenant_id),
        "scope": " ".join(scopes),
    }
    if purpose:
        payload["purpose"] = purpose
    return jwt.encode(
        payload,
        settings.api_key_exchange_jwt_secret,
        algorithm=ALG,
    )


def try_decode_api_key_session_jwt(token: str) -> dict[str, Any] | None:
    """Return claims if token is a valid API-key exchange JWT; else None."""
    try:
        payload = jwt.decode(
            token,
            settings.api_key_exchange_jwt_secret,
            algorithms=[ALG],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except InvalidTokenError:
        return None
    if payload.get("panopticon_auth") != "api_key":
        return None
    return payload
