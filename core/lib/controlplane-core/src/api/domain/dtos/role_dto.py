from typing import List, Optional

from pydantic import BaseModel, Field

from api.domain.entities.role import Role
from api.domain.enum.permission import Permission
from api.domain.serialization import serialize_role


class RoleDto(BaseModel):
    id: str
    name: str
    description: Optional[str] | None
    permissions: List[Permission] = []
    created_at: str
    updated_at: str
    tenant_id: Optional[str] | None = None


class RoleListDto(BaseModel):
    roles: List[RoleDto]
    skip: int
    limit: int
    total: int
    has_previous: bool
    has_next: bool


class CreateRoleDto(BaseModel):
    name: str
    description: Optional[str] | None = None
    tenant_id: str | None = None


class UpdateRoleDto(CreateRoleDto):
    permissions: List[Permission] = Field(default_factory=list)


class CreateRoleResponseDto(BaseModel):
    id: str


def role_from_row(row: Role) -> RoleDto:
    d = serialize_role(row)
    perms: list[Permission] = []
    for p in d.get("permissions") or []:
        try:
            perms.append(Permission(p))
        except ValueError:
            continue
    return RoleDto(
        id=d["id"],
        name=d["name"],
        description=d["description"],
        permissions=perms,
        created_at=d["created_at"] or "",
        updated_at=d["updated_at"] or "",
        tenant_id=d.get("tenant_id"),
    )
