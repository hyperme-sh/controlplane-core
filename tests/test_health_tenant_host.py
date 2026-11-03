from starlette.requests import Request
import pytest
from api.interfaces.middlewares import database_middleware as middleware

@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/health/"])
@pytest.mark.parametrize("host", ["172.18.0.13", "tenant.example.com"])
def test_health_probe_does_not_resolve_tenant(path, host, monkeypatch):
    monkeypatch.setattr(middleware, "get_host_main_domain_name", lambda: "example.com")
    request = Request({"type": "http", "path": path, "headers": [(b"host", host.encode())], "query_string": b""})
    assert middleware.get_tenant_host(request) is None

@pytest.mark.parametrize("path", ["/api/v1/hosts", "/api/v1/health/private"])
def test_normal_routes_keep_tenant_resolution(path, monkeypatch):
    monkeypatch.setattr(middleware, "get_host_main_domain_name", lambda: "example.com")
    request = Request({"type": "http", "path": path, "headers": [(b"host", b"tenant.example.com")], "query_string": b""})
    assert middleware.get_tenant_host(request) == "tenant.example.com"
