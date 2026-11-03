"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.version as _implementation
from controlplane_api.version import router, version_payload, version
__all__ = ['router', 'version_payload', 'version']
sys.modules[__name__] = _implementation
