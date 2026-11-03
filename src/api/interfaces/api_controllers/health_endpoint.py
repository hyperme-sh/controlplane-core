"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.health as _implementation
from controlplane_api.health import logger, router, hypermesh_catalog_reachable, health_check
__all__ = ['logger', 'router', 'hypermesh_catalog_reachable', 'health_check']
sys.modules[__name__] = _implementation
