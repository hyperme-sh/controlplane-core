from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.domain.security import feature_access_management as access


@pytest.mark.asyncio
@pytest.mark.parametrize(("features", "allowed"), [
    ([{"name": "chat", "enabled": True}], True),
    ([SimpleNamespace(name=access.FeatureEnum.CHAT, enabled=True)], True),
    ([{"name": "chat", "enabled": False}], False),
    ([{"name": "report", "enabled": True}], False),
    ([], False),
])
async def test_tenant_feature_access(monkeypatch, features, allowed):
    service = AsyncMock()
    service.get_tenant_by_id.return_value = SimpleNamespace(features=features)
    monkeypatch.setattr(access, "get_tenant_service", lambda: service)
    check = access.check_feature_access(access.FeatureEnum.CHAT)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-a"))
    user = SimpleNamespace(tenant_id="tenant-a", role=SimpleNamespace(permissions=[]))
    if allowed:
        assert await check(request, user) is True
    else:
        with pytest.raises(access.FeatureNotEnabledException):
            await check(request, user)
    service.get_tenant_by_id.assert_awaited_once_with(tenant_id="tenant-a")
    request.state.tenant_id = "tenant-b"
    with pytest.raises(access.FeatureNotEnabledException):
        await check(request, user)
    assert service.get_tenant_by_id.await_count == 1
