"""HS256 JWT minted after a verified tenant switch (not Keycloak, not API-key).

Same shared secret as the API-key exchange token; different issuer and
``panopticon_auth`` so mint/consume cannot be confused with purpose grants.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from jwt import InvalidTokenError

from api.core.config import settings

ISSUER = "https://panopticon.internal/tenant-session"
ALG = "HS256"
TTL_SECONDS = 900
PANOPTICON_AUTH = "tenant_session"


def mint_tenant_session_jwt(
    *,
    claims: dict[str, Any],
    tenant_id: uuid.UUID,
    extra_roles: list[str] | None = None,
    extra_permissions: list[str] | None = None,
) -> str:
    """Short-lived JWT so ``get_current_tenant`` sees tenant context."""
    now = int(time.time())
    roles: list[str] = []
    ra = claims.get("realm_access")
    if isinstance(ra, dict):
        raw_roles = ra.get("roles")
        if isinstance(raw_roles, list):
            roles.extend(str(r) for r in raw_roles if str(r).strip())
    for role in extra_roles or []:
        name = str(role).strip()
        if name and name not in roles:
            roles.append(name)

    groups = claims.get("groups")
    if groups is None:
        groups = claims.get("group")
    if isinstance(groups, str) and groups.strip():
        groups_out: list[str] | str = groups.strip()
    elif isinstance(groups, list):
        groups_out = [str(g).strip() for g in groups if str(g).strip()]
    else:
        groups_out = []

    payload: dict[str, Any] = {
        "sub": str(claims.get("sub") or ""),
        "iss": ISSUER,
        "azp": str(claims.get("azp") or "controlplane-frontend"),
        "iat": now,
        "exp": now + TTL_SECONDS,
        "panopticon_auth": PANOPTICON_AUTH,
        "tenant_id": str(tenant_id),
        "https://panopticon.dev/tenant_id": str(tenant_id),
        "realm_access": {"roles": roles},
        "groups": groups_out,
        "scope": str(claims.get("scope") or "openid"),
    }
    permissions: list[str] = []
    raw_permissions = claims.get("permissions")
    if isinstance(raw_permissions, list):
        permissions.extend(str(item).strip() for item in raw_permissions if str(item).strip())
    for perm in extra_permissions or []:
        name = str(perm).strip()
        if name and name not in permissions:
            permissions.append(name)
    payload["permissions"] = permissions
    host_org_id = claims.get("host_org_id")
    if host_org_id is not None and str(host_org_id).strip():
        payload["host_org_id"] = str(host_org_id)

    email = claims.get("email") or claims.get("preferred_username") or claims.get("username")
    if email:
        payload["email"] = str(email)
    if claims.get("preferred_username"):
        payload["preferred_username"] = str(claims["preferred_username"])
    if claims.get("amr") is not None:
        payload["amr"] = claims["amr"]
    if claims.get("hypermesh_smoke") is True:
        payload["hypermesh_smoke"] = True
    secret = settings.usable_api_key_exchange_secret()
    if not secret:
        raise RuntimeError("API_KEY_EXCHANGE_JWT_SECRET is unset or is a published default")
    return jwt.encode(
        payload,
        secret,
        algorithm=ALG,
    )


def try_decode_tenant_session_jwt(token: str) -> dict[str, Any] | None:
    """Return claims if token is a valid tenant-session JWT; else None."""
    secret = settings.usable_api_key_exchange_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALG],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except InvalidTokenError:
        return None
    if payload.get("panopticon_auth") != PANOPTICON_AUTH:
        return None
    return payload
