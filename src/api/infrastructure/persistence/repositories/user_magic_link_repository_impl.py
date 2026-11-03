from __future__ import annotations

import uuid

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from controlplane_api.exceptions import ApiBaseException
from controlplane_api.utils import get_logger
from controlplane_api.dtos.audit import AuditLogDto
from api.domain.entities.user_magic_link import UserMagicLink

logger = get_logger(__name__)


class UserMagicLinkRepository(BaseRepository[UserMagicLink], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(UserMagicLink)

    async def create_magic_link(self, user_id: str, token: str) -> UserMagicLink:
        uid = uuid.UUID(user_id)
        count = await self.count({"user_id": uid})
        logger.info(f"Magic link requests count for user {user_id}: {count}")
        if count > 3:
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    entity="UserMagicLink",
                    user_id=user_id,
                    changes={"Info": "Too many magic link requests"},
                    tenant_id=None,
                )
            )
            raise ApiBaseException("Too many magic link requests. Please try again later.")
        from controlplane_api.utils import get_utc_now
        from datetime import timedelta

        res = await self.create(
            {
                "user_id": uid,
                "token": token,
                "expires_at": get_utc_now() + timedelta(minutes=15),
            }
        )
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                entity="UserMagicLink",
                user_id=user_id,
                changes={"info": "Created new magic link for user to login"},
                tenant_id=None,
            )
        )
        return res
