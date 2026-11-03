import importlib

ALIASES = {'api.core.config': 'controlplane_api.config', 'api.common.logging': 'controlplane_api.logging', 'api.common.exceptions': 'controlplane_api.exceptions', 'api.common.utils': 'controlplane_api.utils', 'api.common.event_types': 'controlplane_api.event_types', 'api.infrastructure.metrics': 'controlplane_api.metrics', 'api.infrastructure.service_cors': 'controlplane_api.service_cors', 'api.interfaces.api_controllers.health_endpoint': 'controlplane_api.health', 'api.interfaces.api_controllers.version_endpoint': 'controlplane_api.version', 'api.domain.enum.permission': 'controlplane_api.permissions', 'api.domain.operator.plugins': 'controlplane_api.operator.plugins', 'api.common.dtos.health_dto': 'controlplane_api.dtos.health', 'api.domain.dtos.audit_logs_dto': 'controlplane_api.dtos.audit'}

def test_legacy_scaffolding_aliases_share_all_implementations(monkeypatch):
    for old, new in ALIASES.items():
        assert importlib.import_module(old) is importlib.import_module(new)
    from api.core.config import settings as old
    from controlplane_api.config import settings as new
    monkeypatch.setattr(old, "app_title", "Same process settings")
    assert new.app_title == "Same process settings"
    from api.common.exceptions import ForbiddenException as legacy_error
    from controlplane_api.exceptions import ForbiddenException
    assert legacy_error is ForbiddenException
