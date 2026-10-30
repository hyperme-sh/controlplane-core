"""Operator claim checks.

`/operator` opens only when the tenant-session JWT carries `operator:console`.
Org admin, org owner, product groups, `host:manage_tenants`, and `full:access`
do not enter by themselves. A purpose=service key is not a console session.
"""

from __future__ import annotations

from typing import Any, Iterable

from controlplane_api.permissions import Permission

CONSOLE = Permission.OPERATOR_CONSOLE.value
SUPPORT = Permission.OPERATOR_SUPPORT.value
PROGRAM = Permission.OPERATOR_PROGRAM.value
RESOLVE = Permission.OPERATOR_RESOLVE.value
ORG_MANAGE = Permission.ORG_MANAGE.value
ORG_OWN = Permission.ORG_OWN.value

# Present on other doors. None of these is the console gate.
NOT_A_CONSOLE_GRANT = frozenset(
    {
        Permission.HOST_MANAGE_TENANTS.value,
        Permission.MANAGE_TENANT_SETTINGS.value,
        Permission.FULL_ACCESS.value,
        ORG_MANAGE,
        ORG_OWN,
        SUPPORT,
        PROGRAM,
        RESOLVE,
        "admin",
        "host",
        "renter",
        "billing",
        "billing_manager",
        "ir",
        "ir_export",
        "org_admin",
    }
)

PRODUCT_GROUPS = frozenset({"host", "renter", "billing", "ir", "ir_export"})


def permissions_from_claims(claims: dict[str, Any] | None) -> set[str]:
    """Read the permissions claim. Realm roles and groups are not claims."""

    if not claims:
        return set()
    raw = claims.get("permissions")
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def console_allowed(
    permissions: Iterable[str] | None,
    *,
    roles: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
) -> bool:
    """True only when operator:console is an explicit claim.

    Roles and groups are accepted so callers can pass the whole session.
    They never open the console on their own.
    """

    del roles, groups
    return CONSOLE in set(permissions or [])


def support_allowed(permissions: Iterable[str] | None) -> bool:
    """Directory, ban, lift, and operator mail. Not suite edits or resolve."""

    return SUPPORT in set(permissions or [])


def program_may_edit_suite(permissions: Iterable[str] | None) -> bool:
    """Cert-suite membership. Support staff do not receive this."""

    return PROGRAM in set(permissions or [])


def human_may_resolve(permissions: Iterable[str] | None) -> bool:
    """Host-cert override, grant resolve/revoke, auditor resolve."""

    return RESOLVE in set(permissions or [])


def resolve_actor_allowed(
    permissions: Iterable[str] | None,
    *,
    service_purpose: bool = False,
) -> bool:
    """Automation keeps purpose=service. Humans need operator:resolve."""

    if service_purpose:
        return True
    return human_may_resolve(permissions)
