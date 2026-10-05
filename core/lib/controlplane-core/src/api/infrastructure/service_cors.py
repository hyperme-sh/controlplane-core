"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.service_cors as _implementation
from controlplane_api.service_cors import DEFAULT_DEV_CORS_ORIGINS, parse_cors_origins, add_optional_service_cors
__all__ = ['DEFAULT_DEV_CORS_ORIGINS', 'parse_cors_origins', 'add_optional_service_cors']
sys.modules[__name__] = _implementation
