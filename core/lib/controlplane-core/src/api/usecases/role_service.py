import uuid

from sqlalchemy import select
from api.common.utils import get_logger
from api.core.exceptions import RoleAlreadyExistsException, RoleNotFoundException
from api.domain.dtos.role_dto import CreateRoleDto, RoleListDto, UpdateRoleDto
from api.domain.entities.role import Role
from api.infrastructure.persistence.db_session import get_db_session
from api.infrastructure.persistence.repositories.role_repository_impl import RoleRepository

logger = get_logger(__name__)


class RoleService:
    def __init__(self, role_repository: RoleRepository):
        self.role_repository = role_repository
        logger.info("Initialized.")

    async def list_roles(self, skip: int = 0, limit: int = 10) -> RoleListDto:
        return await self.role_repository.list(skip=skip, limit=limit)

    async def find_by_name(self, name: str) -> Role:
        existing = await self.role_repository.single_or_none(name=name)
        if existing is None:
            raise RoleNotFoundException(role_id=name)
        return existing

    async def search_role_by_name(self, name: str) -> list[Role]:
        session = get_db_session()
        col = Role.name
        res = await session.execute(select(Role).where(col.ilike(f"%{name}%")))
        return list(res.scalars().all())

    async def get_role_by_id(self, role_id: str) -> Role:
        existing = await self.role_repository.get(role_id)
        if existing is None:
            raise RoleNotFoundException(role_id=role_id)
        return existing

    async def create_role(self, role_data: CreateRoleDto) -> uuid.UUID:
        existing = await self.role_repository.single_or_none(name=role_data.name)
        if existing is not None:
            raise RoleAlreadyExistsException(name=role_data.name)
        rid = await self.role_repository.create(role_data)
        if rid is None:
            raise RoleNotFoundException(role_id="create")
        return rid

    async def update_role(self, role_id: str, role_data: UpdateRoleDto) -> Role | None:
        existing = await self.role_repository.get(role_id)
        if existing is None:
            raise RoleNotFoundException(role_id=role_id)
        return await self.role_repository.update(role_id, role_data)

    async def delete_role(self, role_id: str) -> None:
        if await self.role_repository.delete(id=role_id) is False:
            raise RoleNotFoundException(role_id=role_id)

    async def total_count(self) -> int:
        return await self.role_repository.count()
