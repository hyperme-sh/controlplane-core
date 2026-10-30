"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.operator.plugins as _implementation
from controlplane_api.operator.plugins import TENNESSEE_EMPTY_REASON, PLUGINS, register_plugin, plugin_registry, plugin_by_id, registry_has_payments, known_action
__all__ = ['TENNESSEE_EMPTY_REASON', 'PLUGINS', 'plugin_registry', 'plugin_by_id', 'registry_has_payments', 'known_action', 'register_plugin']
sys.modules[__name__] = _implementation
