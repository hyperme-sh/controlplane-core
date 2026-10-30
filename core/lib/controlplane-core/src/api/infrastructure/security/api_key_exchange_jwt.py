"""Compatibility alias for controlplane_identity.security.api_key_exchange_jwt."""
import sys
from controlplane_api.config import settings as _configured_settings
from controlplane_identity.security import api_key_exchange_jwt as _implementation
from controlplane_identity.security.api_key_exchange_jwt import ISSUER, ALG, TTL_SECONDS, mint_api_key_session_jwt, try_decode_api_key_session_jwt

__all__ = ['ISSUER', 'ALG', 'TTL_SECONDS', 'mint_api_key_session_jwt', 'try_decode_api_key_session_jwt']

sys.modules[__name__] = _implementation
