from __future__ import annotations

import secrets
import uuid

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from controlplane_api.utils import get_utc_now, get_logger
from controlplane_api.dtos.audit import AuditLogDto
from api.domain.entities.user_password_reset import UserPasswordReset
from api.domain.serialization import parse_uuid

logger = get_logger(__name__)


class UserPasswordResetRepository(BaseRepository[UserPasswordReset], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(UserPasswordReset)

    async def set_password_reset(
        self, user_id: str, first_name: str, tenant_id: str | None = None
    ) -> UserPasswordReset:
        uid = uuid.UUID(user_id)
        existing = await self.single_or_none(user_id=uid)
        token_secret = secrets.token_urlsafe(32)
        tid = parse_uuid(tenant_id) if tenant_id else None
        if existing:
            existing.token_secret = token_secret
            existing.reset_secret_updated_at = get_utc_now()
            await self.session.flush()
            await self.session.refresh(existing)
            await self.add_audit_log(
                AuditLogDto(
                    action="update",
                    entity="UserPasswordReset",
                    user_id=user_id,
                    changes={"Info": "Updated password with reset token"},
                    tenant_id=tenant_id,
                )
            )
            return existing
        res = await self.create(
            {
                "user_id": uid,
                "token_secret": token_secret,
                "reset_secret_updated_at": get_utc_now(),
                "tenant_id": tid,
                "first_name": first_name,
            }
        )
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                entity="UserPasswordReset",
                user_id=user_id,
                changes={"Info": "Created password reset with token"},
                tenant_id=tenant_id,
            )
        )
        return res

    async def delete_by_user_id(self, user_id: str) -> bool:
        existing = await self.single_or_none(user_id=uuid.UUID(user_id))
        if existing is None:
            await self.add_audit_log(
                AuditLogDto(
                    action="delete",
                    entity="UserPasswordReset",
                    user_id=user_id,
                    changes={"Info": "Attempted to delete non-existing password reset"},
                    tenant_id=None,
                )
            )
            return False
        await self.session.delete(existing)
        await self.session.flush()
        await self.add_audit_log(
            AuditLogDto(
                action="delete",
                entity="UserPasswordReset",
                user_id=user_id,
                changes={"Info": "Deleted password reset"},
                tenant_id=str(existing.tenant_id) if existing.tenant_id else None,
            )
        )
        return True
