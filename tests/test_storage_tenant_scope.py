"""Isolated storage tenant-boundary contracts; no provider or database calls."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from api.domain.storage_scope import storage_tenant
from api.domain.dtos.storage_settings_dto import StorageSettingsDto
from api.domain.entities.storage_settings import StorageProvider, StorageSettings
from api.infrastructure.persistence.repositories.storage_settings_repository_impl import StorageSettingsRepository
from api.usecases.storage_settings_service import StorageSettingsService


@pytest.fixture
def repository(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(StorageSettingsRepository, "session", property(lambda self: session))
    repo = StorageSettingsRepository()
    repo.add_audit_log = AsyncMock()
    return repo, session


def statement(call):
    compiled = call.args[0].compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


@pytest.mark.parametrize("value", [None, "", "not-a-tenant", UUID(int=0)])
def test_missing_tenant_never_selects_platform_storage(value):
    with pytest.raises(ValueError, match="tenant storage scope"):
        storage_tenant(value)


@pytest.mark.asyncio
async def test_listing_and_disabling_filter_exact_tenant(repository):
    repo, session = repository
    tenant = uuid4()
    session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[]))))
    assert await repo.get_storages(tenant) == []
    sql, params = statement(session.execute.await_args)
    assert "storage_settings.tenant_id =" in sql and tenant in params.values()
    await repo.disable_all_active_storages(tenant)
    sql, params = statement(session.execute.await_args)
    assert sql.startswith("UPDATE storage_settings")
    assert "storage_settings.tenant_id =" in sql and tenant in params.values()
    assert "storage_settings.is_enabled IS true" in sql and "LIMIT" not in sql
    before = session.execute.await_count
    with pytest.raises(ValueError):
        await repo.get_storages()
    assert session.execute.await_count == before


@pytest.mark.asyncio
async def test_provider_selection_and_active_lookup_have_no_global_fallback(repository):
    repo, _ = repository
    tenant = uuid4()
    repo.single_or_none = AsyncMock(return_value=None)
    assert await repo.get_active_for_tenant(tenant) is None
    repo.single_or_none.assert_awaited_once_with(tenant_id=tenant, is_enabled=True)
    await repo.get_storage_by_provider(StorageProvider.AWS_S3, tenant)
    repo.single_or_none.assert_awaited_with(provider="s3", tenant_id=tenant)
    with pytest.raises(ValueError):
        await repo.get_storage_by_provider(StorageProvider.AWS_S3)
    assert repo.single_or_none.await_count == 2


@pytest.mark.asyncio
async def test_configuration_locks_tenant_and_scopes_existing_provider(repository):
    repo, session = repository
    tenant = uuid4()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=tenant))
    row = StorageSettings(id=uuid4(), tenant_id=tenant, provider="s3", is_enabled=True, region="old", aws_secret_key="old-secret")
    repo.single_or_none = AsyncMock(return_value=row)
    setting = StorageSettingsDto(provider="s3", is_enabled=True, region="new", aws_secret_key=None)
    assert await repo.configure_storage(setting, tenant) == row.id
    sql, params = statement(session.execute.await_args_list[0])
    assert "FOR UPDATE" in sql and tenant in params.values()
    repo.single_or_none.assert_awaited_once_with(provider="s3", tenant_id=tenant)
    assert row.tenant_id == tenant and row.aws_secret_key is None and row.region == "new"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_disable_selected_provider(repository):
    repo, session = repository
    tenant = uuid4()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=tenant))
    row = StorageSettings(id=uuid4(), tenant_id=tenant, provider="s3", is_enabled=False, region="here")
    repo.single_or_none = AsyncMock(return_value=row)
    repo.disable_all_active_storages = AsyncMock()
    await repo.configure_storage(StorageSettingsDto(provider="s3", is_enabled=False, region="here"), tenant)
    repo.disable_all_active_storages.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_clears_secrets_and_cannot_move_or_enable_a_provider(repository):
    repo, session = repository
    tenant = uuid4()
    row = StorageSettings(id=uuid4(), tenant_id=tenant, provider="s3", is_enabled=True, region="here", aws_secret_key="private")
    repo.single_or_none = AsyncMock(return_value=row)
    await repo.reset_storage(str(row.id), {"is_enabled": False, "aws_secret_key": None}, tenant)
    repo.single_or_none.assert_awaited_once_with(id=row.id, tenant_id=tenant)
    assert row.aws_secret_key is None and row.is_enabled is False
    for payload in [{"is_enabled": True}, {"is_enabled": False, "tenant_id": uuid4()}]:
        with pytest.raises(ValueError, match="Invalid storage reset"):
            await repo.reset_storage(str(row.id), payload, tenant)
    assert session.flush.await_count == 1


@pytest.mark.asyncio
async def test_foreign_reset_is_not_found_before_any_write():
    from controlplane_api.exceptions import NotFoundException
    tenant, foreign = uuid4(), uuid4()
    repo = SimpleNamespace(single_or_none=AsyncMock(return_value=None), reset_storage=AsyncMock())
    service = StorageSettingsService(repo)
    with pytest.raises(NotFoundException):
        await service.reset_storage(str(foreign), "operator", tenant)
    repo.single_or_none.assert_awaited_once_with(id=foreign, tenant_id=tenant)
    repo.reset_storage.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_uses_authenticated_tenant_and_never_logs_credentials(caplog):
    from api.interfaces.api_controllers import storage_endpoint as endpoint
    tenant = uuid4()
    user = SimpleNamespace(id="operator", tenant_id=str(tenant))
    service = SimpleNamespace(configure_storage=AsyncMock(), get_storages=AsyncMock(return_value=[]), reset_storage=AsyncMock())
    configuration = StorageSettingsDto(provider="s3", is_enabled=True, region="here", aws_secret_key="never-log-this-secret")
    with caplog.at_level("DEBUG"):
        assert await endpoint.configure_storage(configuration, user, service) == 201
    service.configure_storage.assert_awaited_once_with(setting=configuration, tenant_id=tenant)
    assert configuration.updated_by_user_id == "operator"
    assert "never-log-this-secret" not in caplog.text
    assert await endpoint.get_storage_settings(user, service) == []
    service.get_storages.assert_awaited_once_with(tenant)
    assert (await endpoint.reset_storage(str(uuid4()), user, service)).status_code == 204
    user.tenant_id = None
    with pytest.raises(HTTPException) as exc:
        await endpoint.get_storage_settings(user, service)
    assert exc.value.status_code == 403
    assert service.get_storages.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["s3", "azure_blob"])
async def test_new_provider_cannot_inherit_foreign_credentials(repository, monkeypatch, provider):
    from api.common.base_repository import BaseRepository
    repo, session = repository
    tenant = uuid4()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=tenant))
    repo.single_or_none = AsyncMock(return_value=None)
    row = StorageSettings(id=uuid4(), tenant_id=tenant, provider=provider)
    create = AsyncMock(return_value=row)
    monkeypatch.setattr(BaseRepository, "create", create)
    setting = StorageSettingsDto(provider=provider, is_enabled=True, region="here",
        aws_secret_key="s3-secret", azure_connection_string="azure-secret")
    assert await repo.configure_storage(setting, tenant) == row.id
    payload = create.await_args.args[0]
    assert payload["tenant_id"] == tenant
    assert payload["azure_connection_string" if provider == "s3" else "aws_secret_key"] is None
    assert repo.add_audit_log.await_count == 1
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))
    with pytest.raises(ValueError, match="Unknown tenant"):
        await repo.configure_storage(setting, uuid4())
    assert create.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,keys", [("s3", ["aws_access_key", "aws_secret_key", "aws_bucket_name"]),
    ("azure_blob", ["azure_connection_string", "azure_container_name"])])
async def test_service_reset_clears_provider_credentials_in_authenticated_scope(provider, keys):
    tenant, row_id = uuid4(), uuid4()
    row = SimpleNamespace(id=row_id, tenant_id=tenant, provider=provider)
    repo = SimpleNamespace(single_or_none=AsyncMock(return_value=row), reset_storage=AsyncMock())
    service = StorageSettingsService(repo)
    await service.reset_storage(str(row_id), "operator", str(tenant))
    args = repo.reset_storage.await_args.args
    assert args[0] == str(row_id) and args[2] == tenant
    assert args[1] == {"is_enabled": False, "updated_by_user_id": "operator", **dict.fromkeys(keys)}
    row.tenant_id = uuid4()
    from controlplane_api.exceptions import NotFoundException
    with pytest.raises(NotFoundException):
        await service.reset_storage(str(row_id), "operator", tenant)
    assert repo.reset_storage.await_count == 1


@pytest.mark.asyncio
async def test_listing_masks_credentials_and_reset_absence_does_not_write(repository):
    from datetime import datetime, timezone
    repo, session = repository
    tenant = uuid4()
    row = StorageSettings(id=uuid4(), tenant_id=tenant, provider="s3", is_enabled=True, region="here",
        aws_access_key="access", aws_secret_key="secret", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[row]))))
    result = (await repo.get_storages(tenant))[0].model_dump()
    assert result["aws_access_key"] == "****" and result["aws_secret_key"] == "****"
    repo.single_or_none = AsyncMock(return_value=None)
    await repo.reset_storage(str(uuid4()), {"is_enabled": False}, tenant)
    session.flush.assert_not_awaited()
