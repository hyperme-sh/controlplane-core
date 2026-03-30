from __future__ import annotations

import uuid
from typing import Any

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.utils import get_logger
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.entities.user_preference import UserPreference

logger = get_logger(__name__)


class UserPreferenceRepository(BaseRepository[UserPreference], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(UserPreference)

    async def get_preferences(self, user_id: str) -> UserPreference | None:
        return await self.single_or_none(user_id=uuid.UUID(user_id))

    async def update_preferences(self, user_id: str, preferences: dict[str, Any]) -> None:
        uid = uuid.UUID(user_id)
        existing = await self.single_or_none(user_id=uid)
        if existing is None:
            await self.create({"user_id": uid, "preferences": preferences})
            logger.debug(f"Created new preferences for user {user_id}: {preferences}")
            ex2 = await self.get_preferences(user_id=user_id)
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    entity="UserPreference",
                    user_id=user_id,
                    changes={"new": preferences},
                    tenant_id=str(ex2.tenant_id) if ex2 and ex2.tenant_id else None,
                )
            )
            return
        prf = dict(existing.preferences or {})
        prf.update(preferences)
        await self.update(id=str(existing.id), data={"preferences": prf})
        logger.debug(f"Updated preferences for user {user_id}")
        await self.add_audit_log(
            AuditLogDto(
                action="update",
                entity="UserPreference",
                user_id=user_id,
                changes={"new": preferences, "old": existing.preferences},
                tenant_id=str(existing.tenant_id) if existing.tenant_id else None,
            )
        )
