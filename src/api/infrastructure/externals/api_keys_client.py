"""Compatibility alias for controlplane_identity.api_keys_client."""
import sys
from controlplane_api.config import settings as _configured_settings
from controlplane_identity import api_keys_client as _implementation
from controlplane_identity.api_keys_client import validate_api_key_remote

__all__ = ['validate_api_key_remote']

sys.modules[__name__] = _implementation
