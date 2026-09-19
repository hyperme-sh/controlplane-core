"""P0-D person bind: renter / billing / event user comes from the token.

No trusted body UUID. Human tenant-session uses ``sub`` (UUID) or a users
row keyed by ``keycloak_sub``. Service keys use a recorded service→user map
(``api_keys.created_by_sub`` or an in-process binding for tests).
"""

from __future__ import annotations

import uuid
from typing import Any


def uuid_from_sub(sub: str | None) -> uuid.UUID | None:
    raw = str(sub or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def roles_from_claims(claims: dict[str, Any] | None) -> list[str]:
    if not claims:
        return []
    roles: list[str] = []
    ra = claims.get("realm_access")
    if isinstance(ra, dict):
        raw = ra.get("roles")
        if isinstance(raw, list):
            roles.extend(str(r) for r in raw if str(r).strip())
    extra = claims.get("roles")
    if isinstance(extra, list):
        roles.extend(str(r) for r in extra if str(r).strip())
    return roles


def groups_from_claims(claims: dict[str, Any] | None) -> list[str]:
    if not claims:
        return []
    groups = claims.get("groups")
    if groups is None:
        groups = claims.get("group")
    if isinstance(groups, str) and groups.strip():
        return [groups.strip()]
    if isinstance(groups, list):
        return [str(g).strip() for g in groups if str(g).strip()]
    return []


def can_checkout_from_claims(claims: dict[str, Any] | None) -> bool:
    from api.common.portal_controls import evaluate_portal_controls

    return evaluate_portal_controls(
        roles_from_claims(claims),
        groups_from_claims(claims),
    ).can_checkout


def can_checkout_from_me(roles: list[str] | None, groups: list[str] | None) -> bool:
    from api.common.portal_controls import evaluate_portal_controls

    return evaluate_portal_controls(roles, groups).can_checkout


RETARGET_METADATA_KEYS = frozenset({"lease_id", "listing_id"})


def client_metadata_without_retarget(
    metadata: dict[str, str] | None,
) -> dict[str, str] | None:
    """Drop client lease_id / listing_id so they cannot retarget settlement."""

    if not metadata:
        return None
    cleaned = {
        key: value
        for key, value in metadata.items()
        if key.lower() not in RETARGET_METADATA_KEYS
    }
    return cleaned or None
