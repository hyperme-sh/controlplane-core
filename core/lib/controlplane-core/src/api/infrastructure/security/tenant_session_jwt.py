"""Compatibility alias for controlplane_identity.security.tenant_session_jwt."""
import sys
from api.core.config import settings as _configured_settings
from controlplane_identity.security import tenant_session_jwt as _implementation
from controlplane_identity.security.tenant_session_jwt import ISSUER, ALG, TTL_SECONDS, PANOPTICON_AUTH, mint_tenant_session_jwt, try_decode_tenant_session_jwt, HOST_SETUP_ISSUER, HOST_SETUP_AUDIENCE, mint_host_setup_session, try_decode_host_setup_session

__all__ = ['ISSUER', 'ALG', 'TTL_SECONDS', 'PANOPTICON_AUTH', 'mint_tenant_session_jwt', 'try_decode_tenant_session_jwt', 'HOST_SETUP_ISSUER', 'HOST_SETUP_AUDIENCE', 'mint_host_setup_session', 'try_decode_host_setup_session']

sys.modules[__name__] = _implementation
