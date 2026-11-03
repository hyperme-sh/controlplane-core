"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.logging as _implementation
from controlplane_api.logging import configure_logging
__all__ = ['configure_logging']
sys.modules[__name__] = _implementation
