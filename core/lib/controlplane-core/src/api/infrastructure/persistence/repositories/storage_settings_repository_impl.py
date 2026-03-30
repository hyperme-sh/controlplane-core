from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.utils import get_logger
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.dtos.storage_settings_dto import (
    AvailableStorageProviderDto,
    StorageSettingsDto,
)
from api.domain.entities.storage_settings import StorageProvider, StorageSettings
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

    async def disable_all_active_storages(self) -> None:
        total = await super().count({"is_enabled": True})
        if total > 0:
            logger.warning("Disabling %s active storage settings", total)
            for s in await super().search({"is_enabled": True}):
                await super().update(str(s.id), {"is_enabled": False})
                logger.info(
                    "Disabled storage setting id=%s provider=%s", s.id, s.provider
                )

    async def configure_storage(
        self, setting: StorageSettingsDto, tenant_id: Optional[uuid.UUID] = None
    ) -> uuid.UUID:
        await self.disable_all_active_storages()
        payload = setting.model_dump()
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        if payload.get("provider") == StorageProvider.AWS_S3.value:
            payload["azure_connection_string"] = None
            payload["azure_container_name"] = None
        elif payload.get("provider") == StorageProvider.AZURE_BLOB.value:
            payload["aws_access_key"] = None
            payload["aws_secret_key"] = None
            payload["aws_bucket_name"] = None

        existing: StorageSettings | None = await self.single_or_none(
            provider=payload["provider"]
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
        updated = await super().update(str(existing.id), payload)
        assert updated is not None
        is_enabled_change = existing.is_enabled != payload.get("is_enabled")
        region_change = existing.region != payload.get("region")
        changes: dict = {}
        if is_enabled_change:
            changes["is_enabled"] = {
                "from": existing.is_enabled,
                "to": payload.get("is_enabled"),
            }
        if region_change:
            changes["region"] = {
                "from": existing.region,
                "to": payload.get("region"),
            }
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

    async def get_storages(self) -> list[AvailableStorageProviderDto]:
        res = await self.session.execute(select(StorageSettings))
        rows = list(res.scalars().all())
        return [_row_to_available_dto(s) for s in rows]

    async def get_storage_by_provider(
        self, provider: StorageProvider
    ) -> StorageSettings | None:
        setting = await self.single_or_none(provider=provider.value)
        if setting is None:
            logger.warning("No storage setting found for provider: %s", provider.value)
        return setting

    async def reset_storage(self, id: str, data: dict) -> None:
        result = await super().update(id, data)
        if result is None:
            return
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
