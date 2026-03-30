from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.utils import get_logger
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.dtos.role_dto import CreateRoleDto, RoleDto, RoleListDto, UpdateRoleDto
from api.domain.entities.role import Role
from api.domain.enum.permission import Permission
from api.domain.serialization import parse_uuid, serialize_role

logger = get_logger(__name__)


def _role_to_dto(row: Role) -> RoleDto:
    d = serialize_role(row)
    perms = []
    for p in d.get("permissions") or []:
        try:
            perms.append(Permission(p) if isinstance(p, str) else p)
        except ValueError:
            continue
    return RoleDto(
        id=d["id"],
        name=d["name"],
        description=d["description"],
        permissions=perms,
        created_at=d["created_at"] or "",
        updated_at=d["updated_at"] or "",
        tenant_id=d["tenant_id"],
    )


class RoleRepository(BaseRepository[Role], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(Role)

    async def list(self, skip: int = 0, limit: int = 10) -> RoleListDto:
        cnt = await self.session.execute(select(func.count()).select_from(Role))
        total = int(cnt.scalar_one())
        res = await self.session.execute(select(Role).offset(skip).limit(limit))
        docs = list(res.scalars().all())
        return RoleListDto(
            roles=[_role_to_dto(doc) for doc in docs],
            skip=skip,
            limit=limit,
            total=total,
            has_previous=skip > 0,
            has_next=skip + limit < total,
        )

    async def create(self, data: CreateRoleDto) -> uuid.UUID | None:
        payload = {
            "name": data.name,
            "description": data.description,
            "tenant_id": parse_uuid(data.tenant_id),
            "permissions": [],
        }
        result = await super().create(payload)
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                changes={"Info": f"Create new role with name {data.name} and id {result.id}"},
                entity="Role",
                tenant_id=str(data.tenant_id) if data.tenant_id else None,
                user_id=None,
            )
        )
        return result.id

    async def update(self, role_id: str, data: UpdateRoleDto) -> Optional[Role]:
        existing_role = await super().get(role_id)
        dump = data.model_dump(exclude_unset=True)
        if "tenant_id" in dump:
            dump["tenant_id"] = parse_uuid(dump["tenant_id"])
        if "permissions" in dump and dump["permissions"] is not None:
            dump["permissions"] = [p.value if hasattr(p, "value") else str(p) for p in dump["permissions"]]
        updated_role = await super().update(id=role_id, data=dump)
        if updated_role and existing_role:
            existing_role_doc = serialize_role(existing_role)
            await self.add_audit_log(
                AuditLogDto(
                    action="update",
                    changes={
                        "new": data.model_dump(exclude_unset=True, exclude_none=True),
                        "old": existing_role_doc,
                    },
                    entity="Role",
                    tenant_id=str(updated_role.tenant_id) if updated_role.tenant_id else None,
                    user_id=None,
                )
            )
            return updated_role
        logger.warning(f"No role found for the given role id: {role_id}")
        await self.add_audit_log(
            AuditLogDto(
                action="update",
                changes={"Info": f"Attempted to update role with id {role_id}, but it was not found."},
                entity="Role",
                tenant_id=str(data.tenant_id) if data.tenant_id else None,
                user_id=None,
            )
        )
        return None

    async def delete(self, id: str) -> bool:
        existing_role = await super().get(id)
        if not existing_role:
            logger.warning(f"No role found for the given role id: {id}")
            await self.add_audit_log(
                AuditLogDto(
                    action="delete",
                    changes={"Info": f"Attempted to delete role with id {id}, but it was not found."},
                    entity="Role",
                    tenant_id=None,
                    user_id=None,
                )
            )
            return False
        await super().delete(id)
        await self.add_audit_log(
            AuditLogDto(
                action="delete",
                changes={"Info": f"Deleted role with id {id}"},
                entity="Role",
                tenant_id=str(existing_role.tenant_id) if existing_role.tenant_id else None,
                user_id=None,
            )
        )
        return True
