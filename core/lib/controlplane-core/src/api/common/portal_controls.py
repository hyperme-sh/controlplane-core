"""Org / group portal controls on the existing membership tree.

Public nouns are org and group. Groups are the existing Panopticon /
Keycloak groups (host, renter, billing, ir, ir_export). Org admin is a
role on that org, not a new object. Do not add workspace,
team-as-a-fourth-object, or a Hypermesh-only membership tree.

Used by the portal and by api-keys mint/revoke. Completions do not import
this module and do not hop to api-keys-service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

GROUP_HOST = "host"
GROUP_RENTER = "renter"
GROUP_BILLING = "billing"
GROUP_IR = "ir"
GROUP_IR_EXPORT = "ir_export"

CANONICAL_GROUPS = frozenset(
    {GROUP_HOST, GROUP_RENTER, GROUP_BILLING, GROUP_IR, GROUP_IR_EXPORT}
)

ORG_ADMIN_ROLES = frozenset({"admin"})

# Keycloak often exposes groups as realm roles. billing_manager is the
# existing seeded role that maps to the billing group.
ROLE_AS_GROUP = {
    "host": GROUP_HOST,
    "renter": GROUP_RENTER,
    "billing": GROUP_BILLING,
    "billing_manager": GROUP_BILLING,
    "ir": GROUP_IR,
    "ir_door": GROUP_IR,
    "ir_export": GROUP_IR_EXPORT,
    "ir_receipts": GROUP_IR_EXPORT,
}

def normalize_group_name(raw: str | None) -> str | None:
    """Turn a Keycloak path or label into a canonical group name, or None."""
    if raw is None:
        return None
    n = str(raw).strip().lower()
    if not n:
        return None
    if "/" in n:
        n = n.strip("/").split("/")[-1]
    if n.endswith(" group"):
        n = n[: -len(" group")].strip()
    if n in CANONICAL_GROUPS:
        return n
    if n in ROLE_AS_GROUP:
        return ROLE_AS_GROUP[n]
    return None


def collect_groups(
    roles: Iterable[str] | None = None,
    group_names: Iterable[str] | None = None,
) -> frozenset[str]:
    found: set[str] = set()
    for name in group_names or ():
        canon = normalize_group_name(name)
        if canon:
            found.add(canon)
    for role in roles or ():
        mapped = ROLE_AS_GROUP.get(str(role).strip().lower())
        if mapped:
            found.add(mapped)
    return frozenset(found)


def is_org_admin(roles: Iterable[str] | None = None) -> bool:
    for role in roles or ():
        if str(role).strip().lower() in ORG_ADMIN_ROLES:
            return True
    return False


@dataclass(frozen=True, slots=True)
class PortalControls:
    """Who may use each slice-2 portal control and slice-4 IR grants."""

    org_admin: bool
    groups: tuple[str, ...]
    can_invite_members: bool
    can_assign_group: bool
    can_mint_keys: bool
    can_checkout: bool
    can_see_host_status: bool
    can_see_leases: bool
    can_use_ir_door: bool
    can_export_ir_receipts: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "org_admin": self.org_admin,
            "groups": list(self.groups),
            "can_invite_members": self.can_invite_members,
            "can_assign_group": self.can_assign_group,
            "can_mint_keys": self.can_mint_keys,
            "can_checkout": self.can_checkout,
            "can_see_host_status": self.can_see_host_status,
            "can_see_leases": self.can_see_leases,
            "can_use_ir_door": self.can_use_ir_door,
            "can_export_ir_receipts": self.can_export_ir_receipts,
        }


def evaluate_portal_controls(
    roles: Iterable[str] | None = None,
    group_names: Iterable[str] | None = None,
) -> PortalControls:
    admin = is_org_admin(roles)
    groups = tuple(sorted(collect_groups(roles, group_names)))
    host = admin or GROUP_HOST in groups
    renter = admin or GROUP_RENTER in groups
    billing = admin or GROUP_BILLING in groups or renter
    ir_door = admin or GROUP_IR in groups
    ir_export = admin or GROUP_IR_EXPORT in groups
    return PortalControls(
        org_admin=admin,
        groups=groups,
        can_invite_members=admin,
        can_assign_group=admin,
        can_mint_keys=admin or billing,
        can_checkout=renter,
        can_see_host_status=host,
        can_see_leases=renter,
        can_use_ir_door=ir_door,
        can_export_ir_receipts=ir_export,
    )
