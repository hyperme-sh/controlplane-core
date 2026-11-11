from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select, update

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from controlplane_api.utils import get_logger
from controlplane_api.dtos.audit import AuditLogDto
from api.domain.dtos.storage_settings_dto import (
    AvailableStorageProviderDto,
    StorageSettingsDto,
)
from api.domain.entities.storage_settings import StorageProvider, StorageSettings
from api.domain.storage_scope import storage_tenant
from controlplane_db.models import Tenant
from api.domain.serialization import serialize_storage_settings

logger = get_logger(__name__)


def _row_to_available_dto(row: StorageSettings) -> AvailableStorageProviderDto:
    d = serialize_storage_settings(row)
    return AvailableStorageProviderDto(
        id=d["id"],
        provider=d["provider"],  # type: ignore[arg-type]
        is_enabled=d["is_enabled"],
        region=d["region"] or "",
        aws_access_key=d.get("aws_access_key"),
        aws_secret_key=d.get("aws_secret_key"),
        aws_bucket_name=d.get("aws_bucket_name"),
        azure_connection_string=d.get("azure_connection_string"),
        azure_container_name=d.get("azure_container_name"),
        updated_by_user_id=d.get("updated_by_user_id"),
        created_at=d["created_at"] or "",
        updated_at=d["updated_at"] or "",
        tenant_id=d.get("tenant_id"),
    )


class StorageSettingsRepository(BaseRepository[StorageSettings], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(StorageSettings)

    async def disable_all_active_storages(self, tenant_id: uuid.UUID) -> None:
        tenant = storage_tenant(tenant_id)
        await self.session.execute(update(StorageSettings).where(
            StorageSettings.tenant_id == tenant,
            StorageSettings.is_enabled.is_(True),
        ).values(is_enabled=False))

    async def configure_storage(
        self, setting: StorageSettingsDto, tenant_id: Optional[uuid.UUID] = None
    ) -> uuid.UUID:
        tenant = storage_tenant(tenant_id)
        # Serialize provider selection across this tenant, including first use.
        # The request transaction retains this lock through the configuration write.
        locked = await self.session.execute(select(Tenant.id).where(Tenant.id == tenant).with_for_update())
        if locked.scalar_one_or_none() is None:
            raise ValueError("Unknown tenant storage scope")
        if setting.is_enabled:
            await self.disable_all_active_storages(tenant)
        payload = setting.model_dump()
        payload["tenant_id"] = tenant
        if payload.get("provider") == StorageProvider.AWS_S3.value:
            payload["azure_connection_string"] = None
            payload["azure_container_name"] = None
        elif payload.get("provider") == StorageProvider.AZURE_BLOB.value:
            payload["aws_access_key"] = None
            payload["aws_secret_key"] = None
            payload["aws_bucket_name"] = None

        existing: StorageSettings | None = await self.single_or_none(
            provider=payload["provider"], tenant_id=tenant
        )
        if existing is None:
            logger.info(
                "Creating new storage setting for provider: %s", payload["provider"]
            )
            result = await super().create(payload)
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    changes={
                        "Info": f"Created storage settings {payload['provider']} {result.id}"
                    },
                    entity="StorageSettings",
                    tenant_id=str(result.tenant_id) if result.tenant_id else None,
                    user_id=payload.get("updated_by_user_id"),
                )
            )
            return result.id

        logger.info("Updating existing setting for provider: %s", payload["provider"])
        changes = {}
        for field in ("is_enabled", "region"):
            if getattr(existing, field) != payload.get(field):
                changes[field] = {"from": getattr(existing, field), "to": payload.get(field)}
        # A cleared credential must become SQL NULL; BaseRepository.update skips None.
        for field, value in payload.items():
            setattr(existing, field, value)
        await self.session.flush()
        await self.session.refresh(existing)
        updated = existing
        await self.add_audit_log(
            AuditLogDto(
                action="update",
                changes={
                    "Info": f"Updated storage settings {payload['provider']} {updated.id}",
                    **changes,
                },
                entity="StorageSettings",
                tenant_id=str(payload["tenant_id"]) if payload.get("tenant_id") else None,
                user_id=payload.get("updated_by_user_id"),
            )
        )
        return updated.id

    async def get_storages(self, tenant_id: uuid.UUID | None = None) -> list[AvailableStorageProviderDto]:
        tenant = storage_tenant(tenant_id)
        res = await self.session.execute(select(StorageSettings).where(StorageSettings.tenant_id == tenant))
        rows = list(res.scalars().all())
        return [_row_to_available_dto(s) for s in rows]

    async def get_storage_by_provider(
        self, provider: StorageProvider, tenant_id: uuid.UUID | None = None
    ) -> StorageSettings | None:
        setting = await self.single_or_none(provider=provider.value, tenant_id=storage_tenant(tenant_id))
        if setting is None:
            logger.warning("No storage setting found for provider: %s", provider.value)
        return setting

    async def get_active_for_tenant(self, tenant_id: uuid.UUID) -> StorageSettings | None:
        return await self.single_or_none(tenant_id=storage_tenant(tenant_id), is_enabled=True)

    async def reset_storage(self, id: str, data: dict, tenant_id: uuid.UUID | None = None) -> None:
        tenant = storage_tenant(tenant_id)
        result = await self.single_or_none(id=uuid.UUID(id), tenant_id=tenant)
        if result is None:
            return
        allowed = {"is_enabled", "updated_by_user_id", "aws_access_key", "aws_secret_key", "aws_bucket_name", "azure_connection_string", "azure_container_name"}
        if set(data) - allowed or data.get("is_enabled") is not False:
            raise ValueError("Invalid storage reset")
        for field, value in data.items():
            setattr(result, field, value)
        await self.session.flush()
        await self.session.refresh(result)
        logger.info("Reset storage setting id=%s", id)
        await self.add_audit_log(
            AuditLogDto(
                action="update",
                changes={"Info": f"Reset storage settings {result.id}"},
                entity="StorageSettings",
                tenant_id=str(result.tenant_id) if result.tenant_id else None,
                user_id=data.get("updated_by_user_id"),
            )
        )
