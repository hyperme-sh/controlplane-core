"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.permissions as _implementation
from controlplane_api.permissions import Permission
__all__ = ['Permission']
sys.modules[__name__] = _implementation
