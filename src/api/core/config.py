"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.config as _implementation
from controlplane_api.config import Settings, settings
__all__ = ['Settings', 'settings']
sys.modules[__name__] = _implementation
