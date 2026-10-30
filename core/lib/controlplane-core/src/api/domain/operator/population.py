"""Operator and owner claims for a tenant-session JWT.

Two sources, kept separate:

* The explicitly provisioned pan-test operator role carries the
  four operator claims. Email alone grants nothing. That seed is not an org owner, so it does not
  receive ``org:own`` or ``org:manage``.
* Production OIDC mapping is config. ``OPERATOR_OIDC_DOMAIN`` is this
  instance's operator and owner population (email domain or hosted
  domain ``hd``). ``fyberlabs.com`` is the Fyber example, not a
  hardcoded domain. Inside that domain, ``OPERATOR_OIDC_GROUP_MAP`` and
  ``OPERATOR_OIDC_USER_MAP`` assign claims. A group can map to
  ``operator:support`` and a different group to ``operator:program``
  and ``operator:resolve``. Owner is ``org:own``, distinct from support.
  Users outside the configured domain get nothing from this map.
"""

from __future__ import annotations

from typing import Any, Iterable

from controlplane_api.config import settings
from controlplane_api.permissions import Permission
from api.domain.operator.claims import (
    CONSOLE,
    ORG_MANAGE,
    ORG_OWN,
    PROGRAM,
    RESOLVE,
    SUPPORT,
)

# Existing pan-test portal admin. Not KC_BOOTSTRAP_ADMIN and not org owner.
SEEDED_TEST_ADMIN_EMAIL = "admin@test.hyperme.sh"
SEEDED_TEST_ADMIN_USERNAME = "admin"
SEEDED_TEST_ADMIN_OPERATOR_CLAIMS = (
    CONSOLE,
    SUPPORT,
    PROGRAM,
    RESOLVE,
)

# Map targets. Support and program stay different claims. Owner is org:own.
MAPPABLE_CLAIMS = frozenset(
    {CONSOLE, SUPPORT, PROGRAM, RESOLVE, ORG_MANAGE, ORG_OWN}
)


def _known(name: str) -> str | None:
    token = name.strip()
    if token in MAPPABLE_CLAIMS and token in {item.value for item in Permission}:
        return token
    return None


def parse_claim_map(raw: str | None) -> dict[str, tuple[str, ...]]:
    """Parse ``key=claim|claim;key=claim`` into a lowercase key map.

    Unknown claim ids are dropped. Empty input is an empty map.
    """

    found: dict[str, list[str]] = {}
    for entry in str(raw or "").split(";"):
        piece = entry.strip()
        if not piece or "=" not in piece:
            continue
        key, _, claims_raw = piece.partition("=")
        key_n = key.strip().lower()
        if not key_n:
            continue
        bucket = found.setdefault(key_n, [])
        for claim in claims_raw.split("|"):
            known = _known(claim)
            if known and known not in bucket:
                bucket.append(known)
    return {key: tuple(claims) for key, claims in found.items() if claims}


def group_labels(raw: str | None) -> set[str]:
    """Keycloak path or bare name, lowercased, including the last segment."""

    text = str(raw or "").strip().lower()
    if not text:
        return set()
    labels = {text}
    trimmed = text.strip("/")
    if trimmed:
        labels.add(trimmed)
        labels.add(trimmed.split("/")[-1])
    return labels


def configured_population_domain() -> str:
    return str(settings.operator_oidc_domain or "").strip().lower().lstrip("@")


def in_operator_population(
    email: str | None,
    hosted_domains: Iterable[str] | None = None,
    *,
    domain: str | None = None,
) -> bool:
    """True when the email domain or a hosted domain equals the config."""

    configured = configured_population_domain() if domain is None else domain.strip().lower().lstrip("@")
    if not configured:
        return False
    address = str(email or "").strip().lower()
    if "@" in address and address.rsplit("@", 1)[-1] == configured:
        return True
    for item in hosted_domains or ():
        if str(item).strip().lower() == configured:
            return True
    return False


def hosted_domains_from_claims(claims: dict[str, Any] | None) -> list[str]:
    """Google ``hd``, an explicit hosted-domain claim, and copied ``idp_org``."""

    if not claims:
        return []
    found: list[str] = []
    for key in ("hd", "hosted_domain", "idp_org"):
        raw = claims.get(key)
        if isinstance(raw, str) and raw.strip() and raw.strip() not in found:
            found.append(raw.strip())
    return found


def mapped_oidc_claims(
    email: str | None,
    groups: Iterable[str] | None = None,
    hosted_domains: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Claims from the user and group maps, only inside the configured domain."""

    if not in_operator_population(email, hosted_domains):
        return ()
    labels: set[str] = set()
    for group in groups or ():
        labels.update(group_labels(group))
    found: list[str] = []
    for key, claims in parse_claim_map(settings.operator_oidc_group_map).items():
        if not (group_labels(key) & labels):
            continue
        for claim in claims:
            if claim not in found:
                found.append(claim)
    address = str(email or "").strip().lower()
    if address:
        for claim in parse_claim_map(settings.operator_oidc_user_map).get(address, ()):
            if claim not in found:
                found.append(claim)
    return tuple(found)


def tenant_session_permissions(
    role_permissions: Iterable[str] | None,
    *,
    email: str | None,
    groups: Iterable[str] | None = None,
    hosted_domains: Iterable[str] | None = None,
) -> list[str]:
    """Role permissions and the OIDC map; email alone never seeds an operator."""

    out: list[str] = []
    for perm in role_permissions or ():
        name = str(perm).strip()
        if name and name not in out:
            out.append(name)
    for claim in mapped_oidc_claims(email, groups, hosted_domains):
        if claim not in out:
            out.append(claim)
    return out
