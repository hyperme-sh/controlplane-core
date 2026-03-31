from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.exceptions import NotFoundException
from api.common.utils import get_logger
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.common.enums.gender import Gender
from api.domain.dtos.user_dto import CreateUserDto, UpdateUserDto, UserDto, UserListDto
from api.domain.entities.user import User
from api.domain.serialization import parse_uuid, serialize_user

logger = get_logger(__name__)


class UserRepository(BaseRepository[User], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(User)

    async def single_or_none_platform_email_ci(self, email: str) -> User | None:
        """Match platform user (tenant_id IS NULL) by case-insensitive email."""
        normalized = email.strip().lower()
        stmt = (
            select(User)
            .where(func.lower(User.email) == normalized)
            .where(User.tenant_id.is_(None))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list(self, skip: int = 0, limit: int = 10) -> UserListDto:
        cnt = await self.session.execute(select(func.count()).select_from(User))
        total = int(cnt.scalar_one())
        res = await self.session.execute(select(User).offset(skip).limit(limit))
        docs = list(res.scalars().all())
        users_out = []
        for doc in docs:
            d = serialize_user(doc)
            d["gender"] = Gender(d["gender"])
            users_out.append(UserDto(**d))
        result = UserListDto(
            users=users_out,
            skip=skip,
            limit=limit,
            total=total,
            has_previous=skip > 0,
            has_next=skip + limit < total,
        )
        return result

    async def create(self, data: CreateUserDto) -> uuid.UUID | None:
        kc_sub = data.keycloak_sub
        payload = {
            "email": data.email,
            "first_name": data.first_name,
            "last_name": data.last_name,
            "gender": data.gender.value if hasattr(data.gender, "value") else str(data.gender),
            "password": data.password,
            "keycloak_sub": kc_sub,
            "is_active": True if kc_sub else False,
            "activated_at": datetime.now(timezone.utc) if kc_sub else None,
            "role_id": parse_uuid(data.role_id),
            "tenant_id": parse_uuid(data.tenant_id),
        }
        result = await super().create(payload)
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                entity="User",
                user_id=str(result.id),
                changes={
                    "new": data.model_dump(
                        exclude={"password", "email"}, exclude_none=True, exclude_unset=True
                    )
                },
                tenant_id=str(data.tenant_id) if data.tenant_id else None,
            )
        )
        return result.id

    async def update(self, user_id: str, data: UpdateUserDto) -> Optional[User]:
        try:
            existing_user = await self.get(id=user_id)
            if not existing_user:
                raise NotFoundException("User not found")
            dump = data.model_dump(exclude_unset=True)
            if "role_id" in dump and dump["role_id"] is not None:
                dump["role_id"] = parse_uuid(dump["role_id"])
            if "tenant_id" in dump:
                dump["tenant_id"] = parse_uuid(dump["tenant_id"])
            updated_user = await super().update(id=user_id, data=dump)
            if updated_user:
                await self.add_audit_log(
                    AuditLogDto(
                        action="update",
                        entity="User",
                        user_id=str(user_id),
                        changes={"info": f"User with id {user_id} updated."},
                        tenant_id=str(existing_user.tenant_id) if existing_user.tenant_id else None,
                    )
                )
                return updated_user
        except NotFoundException:
            logger.error(f"No user found for the given user id: {user_id}")
            await self.add_audit_log(
                AuditLogDto(
                    action="update",
                    entity="User",
                    user_id=str(user_id),
                    changes={"error": f"No user found for the given user id: {user_id}"},
                    tenant_id=str(data.tenant_id) if data.tenant_id else None,
                )
            )
            return None
        return None

    async def delete(self, user_id: str) -> bool:
        existing_user = await self.get(id=user_id)
        try:
            if not existing_user:
                raise NotFoundException("User not found")
            result = await super().delete(id=user_id)
            if result:
                await self.add_audit_log(
                    AuditLogDto(
                        action="delete",
                        entity="User",
                        user_id=user_id,
                        changes={"info": f"User with id {user_id} deleted."},
                        tenant_id=str(existing_user.tenant_id) if existing_user.tenant_id else None,
                    )
                )
            return result
        except NotFoundException:
            logger.error(f"No user found for the given user id: {user_id}")
            await self.add_audit_log(
                AuditLogDto(
                    action="delete",
                    entity="User",
                    user_id=user_id,
                    changes={"error": f"No user found for the given user id: {user_id}"},
                    tenant_id=str(existing_user.tenant_id)
                    if existing_user and existing_user.tenant_id
                    else None,
                )
            )
            return False
