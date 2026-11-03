"""Compatibility alias for controlplane_tenancy.tenant_membership."""
import sys
from controlplane_tenancy import tenant_membership as _implementation
from controlplane_tenancy.tenant_membership import MARKETPLACE_TENANT_ID, get_tenant_membership_row, create_tenant_membership_row

__all__ = ['MARKETPLACE_TENANT_ID', 'get_tenant_membership_row', 'create_tenant_membership_row']

sys.modules[__name__] = _implementation
