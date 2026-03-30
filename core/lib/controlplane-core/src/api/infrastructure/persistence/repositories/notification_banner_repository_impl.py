from __future__ import annotations

import uuid
from typing import Any

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.exceptions import NotFoundException
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.entities.notification_settings import NotificationBannerSetting


def _serialize_row(row: NotificationBannerSetting) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "is_enabled": row.is_enabled,
        "message": row.message,
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class NotificationBannerRepository(BaseRepository[NotificationBannerSetting], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(NotificationBannerSetting)

    async def get_banner_setting(self) -> NotificationBannerSetting | None:
        rows = await self.search({}, limit=1)
        return rows[0] if rows else None

    async def create_banner_setting(
        self,
        is_enabled: bool,
        message: str | None,
        tenant_id: uuid.UUID | None,
    ) -> NotificationBannerSetting:
        created_setting = await self.create(
            {
                "is_enabled": is_enabled,
                "message": message,
                "tenant_id": tenant_id,
            }
        )
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                changes={
                    "info": f"Created notification banner setting for tenant {tenant_id}"
                },
                entity="NotificationBannerSetting",
                tenant_id=str(tenant_id) if tenant_id else None,
                user_id=None,
            )
        )
        return created_setting

    async def update_banner_setting(
        self, row_id: str, is_enabled: bool, message: str | None
    ) -> NotificationBannerSetting | None:
        existing = await self.get(row_id)
        if not existing:
            await self.add_audit_log(
                AuditLogDto(
                    action="error",
                    changes={
                        "info": f"Attempted to update non-existing notification banner setting with id {row_id}"
                    },
                    entity="NotificationBannerSetting",
                    tenant_id=None,
                    user_id=None,
                )
            )
            raise NotFoundException("NotificationBannerSetting", row_id)
        old_doc = _serialize_row(existing)
        updated_setting = await self.update(
            row_id, {"is_enabled": is_enabled, "message": message}
        )
        if updated_setting:
            await self.add_audit_log(
                AuditLogDto(
                    action="update",
                    changes={
                        "new": _serialize_row(updated_setting),
                        "old": old_doc,
                    },
                    entity="NotificationBannerSetting",
                    tenant_id=str(existing.tenant_id) if existing.tenant_id else None,
                    user_id=None,
                )
            )
        return updated_setting
