from __future__ import annotations

from sqlalchemy import select

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.domain.entities.sso_settings import SSOSettings


class SSOSettingsProviderRepository(BaseRepository[SSOSettings], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(SSOSettings)

    async def get_by_id(self, row_id: str) -> SSOSettings | None:
        return await self.get(row_id)

    async def update_sso_settings(
        self, row_id: str, data: dict
    ) -> SSOSettings | None:
        return await self.update(row_id, data)

    async def create_sso_settings(self, data: dict) -> SSOSettings:
        return await self.create(data)

    async def delete_sso_settings(self, row_id: str) -> bool:
        return await self.delete(row_id)

    async def list_sso_settings(self) -> list[SSOSettings]:
        res = await self.session.execute(select(SSOSettings))
        return list(res.scalars().all())
