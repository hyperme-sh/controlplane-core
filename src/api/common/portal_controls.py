"""Compatibility alias for identity portal controls."""
import sys
from controlplane_identity import portal_controls as _implementation
from controlplane_identity.portal_controls import GROUP_HOST, GROUP_RENTER, GROUP_BILLING, GROUP_IR, GROUP_IR_EXPORT, CANONICAL_GROUPS, ORG_ADMIN_ROLES, ROLE_AS_GROUP, normalize_group_name, collect_groups, is_org_admin, PortalControls, evaluate_portal_controls
__all__ = ['GROUP_HOST', 'GROUP_RENTER', 'GROUP_BILLING', 'GROUP_IR', 'GROUP_IR_EXPORT', 'CANONICAL_GROUPS', 'ORG_ADMIN_ROLES', 'ROLE_AS_GROUP', 'normalize_group_name', 'collect_groups', 'is_org_admin', 'PortalControls', 'evaluate_portal_controls']
sys.modules[__name__] = _implementation
