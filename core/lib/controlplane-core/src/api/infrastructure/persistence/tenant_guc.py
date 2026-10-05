"""Compatibility alias for controlplane_tenancy.tenant_guc."""
import sys
from controlplane_tenancy import tenant_guc as _implementation
from controlplane_tenancy.tenant_guc import set_current_tenant_id_guc

__all__ = ['set_current_tenant_id_guc']

sys.modules[__name__] = _implementation
