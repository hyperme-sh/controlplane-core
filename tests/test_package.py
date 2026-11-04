import importlib.util
from importlib.metadata import requires
from unittest.mock import Mock
import pytest


def test_service_implementations_are_not_dependencies():
    for name in ('user_service', 'auth_service', 'hypermesh_service', 'aiuditor_service'):
        assert importlib.util.find_spec(name) is None
    import operator_ban_store
    assert callable(operator_ban_store.subject_banned_sync)


def test_shared_session_context_resets():
    from api.infrastructure.persistence import db_session as old
    from controlplane_tenancy import db_session as owner
    session = Mock()
    token = old.set_db_session(session)
    try:
        assert owner.get_db_session() is session
    finally:
        old.reset_db_session(token)
    with pytest.raises(RuntimeError):
        owner.get_db_session()


def test_nested_contract_pin_is_standard_metadata():
    dep, = [d for d in requires('controlplane-core') if d.startswith('operator-ban-store ')]
    assert dep == 'operator-ban-store @ git+https://github.com/hyperme-sh/user-service.git@895b8621dec17cf55a060f028ad65ad81e6bb81e#subdirectory=core/user-service/contracts'


def test_health_and_version_aliases_http(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.interfaces.api_controllers.health_endpoint import router as health
    from api.interfaces.api_controllers.version_endpoint import router as version
    monkeypatch.delenv('CPEDGE_HYPERMESH_SERVICE_URL',raising=False)
    monkeypatch.setenv('SERVICE_NAME','core-package-test')
    app=FastAPI()
    app.include_router(health)
    app.include_router(version)
    with TestClient(app) as client:
        assert client.get('/health/').json()=={'status':'Ok'}
        assert client.get('/version').json()['service']=='core-package-test'
