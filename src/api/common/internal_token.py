"""Compatibility alias for controlplane_identity.internal_token."""
import sys
from controlplane_identity import internal_token as _implementation
from controlplane_identity.internal_token import INTERNAL_TOKEN_HEADER, provided_internal_token, internal_token_matches, request_has_internal_token

__all__ = ['INTERNAL_TOKEN_HEADER', 'provided_internal_token', 'internal_token_matches', 'request_has_internal_token']

sys.modules[__name__] = _implementation
