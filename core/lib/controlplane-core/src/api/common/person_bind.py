"""Compatibility alias for controlplane_identity.person_bind."""
import sys
from controlplane_identity import person_bind as _implementation
from controlplane_identity.person_bind import uuid_from_sub, roles_from_claims, groups_from_claims, can_checkout_from_claims, can_checkout_from_me, RETARGET_METADATA_KEYS, client_metadata_without_retarget

__all__ = ['uuid_from_sub', 'roles_from_claims', 'groups_from_claims', 'can_checkout_from_claims', 'can_checkout_from_me', 'RETARGET_METADATA_KEYS', 'client_metadata_without_retarget']

sys.modules[__name__] = _implementation
