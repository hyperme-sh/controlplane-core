"""Tenant object authentication contracts with real signed gateway tokens."""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException, Request
from api.infrastructure.security import storage_session as auth
from api.infrastructure.security import api_key_exchange_jwt as exchange


@pytest.fixture
def tokens(monkeypatch):
    secret = "unit-only-gateway-secret-" + "a" * 40
    monkeypatch.setattr(exchange, "settings", SimpleNamespace(usable_api_key_exchange_secret=lambda: secret))
    tenant = uuid4()
    now = int(time.time())
    claims = {"iss": exchange.ISSUER, "sub": "apikey:" + str(uuid4()), "azp": "controlplane-api-key",
        "spanopticon_auth": "api_key", "iat": now, "exp": now + 300,
        "https://spanopticon.dev/tenant_id": str(tenant), "scope": "storage:objects:read storage:objects:write"}
    def mint(changes=None, signing_secret=secret):
        return jwt.encode({**claims, **(changes or {})}, signing_secret, algorithm="HS256")
    return tenant, now, mint


def test_gateway_signature_scope_and_tenant_are_all_required(tokens):
    tenant, now, mint = tokens
    assert auth.storage_session(mint(), "read", now) == tenant
    assert auth.storage_session(mint(), "write", now) == tenant
    with pytest.raises(HTTPException) as exc:
        auth.storage_session(mint(), "delete", now)
    assert exc.value.status_code == 403
    for change in [{"iss": "https://other.example"}, {"azp": "browser"}, {"sub": str(uuid4())},
        {"spanopticon_auth": "oidc"}, {"https://spanopticon.dev/tenant_id": None},
        {"https://spanopticon.dev/tenant_id": "00000000-0000-0000-0000-000000000000"},
        {"exp": now + 301}, {"iat": now + 10}, {"iat": True}, {"exp": now - 1}, {"scope": "*"}]:
        with pytest.raises(HTTPException):
            auth.storage_session(mint(change), "read", now)
    with pytest.raises(HTTPException):
        auth.storage_session(mint(signing_secret="b" * 64), "read", now)
    with pytest.raises(HTTPException):
        auth.storage_session("x" * 20000, "read", now)


@pytest.mark.asyncio
async def test_backend_scope_comes_only_from_token_and_requires_private_bucket(tokens, monkeypatch):
    from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint
    tenant, _, mint = tokens
    selected = SimpleNamespace(tenant_id=tenant, provider="s3", is_enabled=True,
        aws_bucket_name="private-bucket", aws_access_key="access", aws_secret_key="secret", region="us-east-1")
    repo = SimpleNamespace(get_active_for_tenant=AsyncMock(return_value=selected))
    monkeypatch.setattr(endpoint, "get_storage_settings_repository", lambda: repo)
    flags = dict.fromkeys(["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"], True)
    client = SimpleNamespace(get_public_access_block=Mock(return_value={"PublicAccessBlockConfiguration": flags}), close=Mock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(endpoint.boto3, "client", factory)
    request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"tenant_id=attacker",
        "headers": [(b"authorization", ("Bearer " + mint()).encode()), (b"x-tenant-id", b"attacker")]})
    source = endpoint.object_store(request)
    store = await anext(source)
    assert store.tenant == tenant
    repo.get_active_for_tenant.assert_awaited_once_with(tenant)
    assert factory.call_args.kwargs["aws_access_key_id"] == "access"
    await source.aclose()
    client.close.assert_called_once()
    flags["BlockPublicPolicy"] = False
    with pytest.raises(HTTPException) as exc:
        await anext(endpoint.object_store(request))
    assert exc.value.status_code == 503
    assert client.close.call_count == 2
    for change in [{"provider": "azure_blob"}, {"tenant_id": uuid4()}, {"is_enabled": False}, {"aws_secret_key": None}]:
        repo.get_active_for_tenant.return_value = SimpleNamespace(**{**selected.__dict__, **change})
        before = factory.call_count
        with pytest.raises(HTTPException):
            await anext(endpoint.object_store(request))
        assert factory.call_count == before
    repo.get_active_for_tenant.return_value = None
    with pytest.raises(HTTPException):
        await anext(endpoint.object_store(request))


@pytest.mark.asyncio
async def test_missing_or_duplicate_auth_and_private_policy_failure_deny(tokens, monkeypatch):
    from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint
    tenant, _, mint = tokens
    repo = SimpleNamespace(get_active_for_tenant=AsyncMock())
    monkeypatch.setattr(endpoint, "get_storage_settings_repository", lambda: repo)
    for headers in [[], [(b"authorization", b"Basic nope")], [(b"authorization", ("Bearer " + mint()).encode())] * 2]:
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": headers})
        with pytest.raises(HTTPException) as exc:
            await anext(endpoint.object_store(request))
        assert exc.value.status_code == 401
    repo.get_active_for_tenant.assert_not_awaited()
    selected = SimpleNamespace(tenant_id=tenant, provider="s3", is_enabled=True,
        aws_bucket_name="private-bucket", aws_access_key="access", aws_secret_key="secret", region="us-east-1")
    repo.get_active_for_tenant.return_value = selected
    client = SimpleNamespace(get_public_access_block=Mock(side_effect=RuntimeError("private-details")), close=Mock())
    monkeypatch.setattr(endpoint.boto3, "client", Mock(return_value=client))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"authorization", ("Bearer " + mint()).encode())]})
    with pytest.raises(HTTPException) as exc:
        await anext(endpoint.object_store(request))
    assert exc.value.status_code == 503 and "private-details" not in exc.value.detail
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_azure_backend_uses_the_signed_tenant_and_closes_after_request(tokens, monkeypatch):
    from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint
    from contextlib import asynccontextmanager
    tenant, _, mint = tokens
    selected = SimpleNamespace(tenant_id=tenant, provider="azure_blob", is_enabled=True)
    repo = SimpleNamespace(get_active_for_tenant=AsyncMock(return_value=selected))
    monkeypatch.setattr(endpoint, "get_storage_settings_repository", lambda: repo)
    closed = False
    backend = object()
    @asynccontextmanager
    async def azure(config):
        nonlocal closed
        assert config is selected
        try:
            yield backend
        finally:
            closed = True
    monkeypatch.setattr(endpoint, "azure_store", azure)
    request = Request({"type": "http", "method": "PUT", "path": "/", "headers": [(b"authorization", ("Bearer " + mint()).encode())]})
    source = endpoint.object_store(request)
    store = await anext(source)
    assert store.tenant == tenant and store.backend is backend and not closed
    await source.aclose()
    assert closed
    repo.get_active_for_tenant.assert_awaited_once_with(tenant)
