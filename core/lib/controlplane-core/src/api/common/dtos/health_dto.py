"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.dtos.health as _implementation
from controlplane_api.dtos.health import HealthCheckResponseDto
__all__ = ['HealthCheckResponseDto']
sys.modules[__name__] = _implementation
