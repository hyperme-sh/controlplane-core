from __future__ import annotations

import base64
import uuid
from datetime import timedelta
from typing import Any, Literal, Optional

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from controlplane_api.utils import get_logger, get_utc_now
from controlplane_api.dtos.audit import AuditLogDto
from api.domain.entities.user_passkey import Challenges, UserPasskey
from api.domain.serialization import parse_uuid

logger = get_logger(__name__)


class UserPasskeyRepository(BaseRepository[UserPasskey], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(UserPasskey)

    async def ensure_row(
        self, user_email: str, tenant_id: uuid.UUID | None
    ) -> UserPasskey:
        row = await self.single_or_none(user_email=user_email)
        if row is not None:
            return row
        return await super().create(
            {
                "user_email": user_email,
                "tenant_id": tenant_id,
                "credentials": [],
            }
        )

    async def set_credentials(self, user_email: str, credentials: list[Any]) -> None:
        row = await self.single_or_none(user_email=user_email)
        if row is None:
            raise ValueError("User passkey row not found")
        await super().update(str(row.id), {"credentials": credentials})


class UserPasskeyChallengesRepository(BaseRepository[Challenges], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(Challenges)

    async def save_challenge(
        self,
        email: str,
        challenge: bytes,
        type: Literal["registration", "authentication"],
        tenant_id: Optional[str] = None,
    ) -> Challenges:
        cv = base64.urlsafe_b64encode(challenge).decode("utf-8").rstrip("=")
        logger.debug("challenge bytes to a string %s", cv)
        tid = parse_uuid(tenant_id) if tenant_id else None
        expires_at = get_utc_now() + timedelta(minutes=10)
        res = await super().create(
            {
                "email": email,
                "challenge": cv,
                "type": type,
                "tenant_id": tid,
                "expires_at": expires_at,
            }
        )
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                entity="UserPasskeyChallenges",
                user_id=email,
                changes={"Info": "Created new challenge for user with type " + type},
                tenant_id=str(tid) if tid else None,
            )
        )
        return res

    async def get_challenge(
        self, email: str, type: Literal["registration", "authentication"]
    ) -> Optional[Challenges]:
        row = await self.single_or_none(email=email, type=type)
        if row is None:
            return None
        if row.expires_at < get_utc_now():
            await super().delete(str(row.id))
            return None
        return row

    async def delete_challenge(
        self, email: str, type: Literal["registration", "authentication"]
    ) -> bool:
        existing = await self.get_challenge(email=email, type=type)
        if existing is None:
            logger.warning(
                "Challenge not found for user %s with type %s", email, type
            )
            await self.add_audit_log(
                AuditLogDto(
                    action="delete",
                    entity="UserPasskeyChallenges",
                    user_id=email,
                    changes={"Info": "Attempted to delete non-existing challenge"},
                    tenant_id=None,
                )
            )
            return False
        await super().delete(str(existing.id))
        await self.add_audit_log(
            AuditLogDto(
                action="delete",
                entity="UserPasskeyChallenges",
                user_id=email,
                changes={"Info": "Deleted challenge for user with type " + type},
                tenant_id=str(existing.tenant_id) if existing.tenant_id else None,
            )
        )
        return True
