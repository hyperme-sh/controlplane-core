from uuid import UUID
from typing import Optional

from api.domain.dtos.notification_settings_dto import NotificationBannerSettingDto
from api.infrastructure.persistence.repositories.notification_banner_repository_impl import (
    NotificationBannerRepository,
)


class NotificationBannerService:
    def __init__(self, repository: NotificationBannerRepository):
        self.repository: NotificationBannerRepository = repository

    async def get_banner_setting(self) -> NotificationBannerSettingDto:
        result = await self.repository.get_banner_setting()
        if result:
            return NotificationBannerSettingDto(
                id=str(result.id),
                is_enabled=result.is_enabled,
                message=result.message,
                tenant_id=str(result.tenant_id) if result.tenant_id else None,
                created_at=result.created_at.isoformat() if result.created_at else None,
                updated_at=result.updated_at.isoformat() if result.updated_at else None,
            )
        return NotificationBannerSettingDto(is_enabled=False, message=None)

    async def create_banner_setting(self, is_enabled: bool, message: Optional[str] = None, tenant_id: Optional[UUID] = None):
        await self.repository.create_banner_setting(is_enabled, message, tenant_id)

    async def update_banner_setting(self, id: str, is_enabled: bool, message: Optional[str] = None):
        await self.repository.update_banner_setting(id, is_enabled, message)
        
