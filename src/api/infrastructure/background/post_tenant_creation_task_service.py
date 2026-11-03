from api.common.seeder_utils import get_seed_roles
from controlplane_api.utils import get_logger
from api.core.exceptions import EmailAlreadyExistsException, RoleNotFoundException, UserNotFoundException
from api.domain.dtos.role_dto import CreateRoleDto, UpdateRoleDto
from api.domain.dtos.user_dto import CreateUserDto, UpdateUserDto
from api.domain.enum.role import RoleType
from api.domain.interfaces.background_task import IBackgroundTask
from api.domain.serialization import serialize_role
from api.usecases.role_service import RoleService
from api.usecases.user_service import UserService

logger = get_logger(__name__)


class PostTenantCreationTaskService(IBackgroundTask):
    def __init__(self, user_service: UserService, role_service: RoleService):
        self.user_service = user_service
        self.role_service = role_service
        logger.info("Initialized.")

    async def _seed_roles(self, tenant_id: str | None) -> None:
        roles = get_seed_roles()
        for role in roles:
            logger.info(f"Seeding role {role.name} and permissions: {role.permissions}")
            rid = await self.role_service.create_role(
                CreateRoleDto(name=role.name, description=role.description, tenant_id=tenant_id)
            )
            await self.role_service.update_role(
                str(rid),
                UpdateRoleDto(
                    name=role.name,
                    description=role.description,
                    permissions=role.permissions.copy(),
                    tenant_id=tenant_id,
                ),
            )
        logger.info("Seeded default roles successfully.")

    async def _init_and_run(self, admin_user: CreateUserDto) -> None:
        try:
            await self._seed_roles(tenant_id=admin_user.tenant_id)
            new_id = await self.user_service.create_user(admin_user)
            user = await self.user_service.get_user_by_id(user_id=str(new_id))
            logger.info(f"Created admin user with ID: {user.id} and email: {user.email}")
            role = await self.role_service.find_by_name(name=RoleType.ADMIN)
            logger.info(f"Assigning 'Admin' role to user {user.email}")
            await self.user_service.update_user(
                str(user.id), UpdateUserDto(role_id=str(role.id), tenant_id=admin_user.tenant_id)
            )
            logger.info(f"Assigned 'Admin' role to user {user.email} successfully.")
        except EmailAlreadyExistsException as eae:
            logger.error(f"Email already exists: {eae} - Admin user creation skipped.")
        except UserNotFoundException as uex:
            logger.error(f"User not found: {uex} - Some issue in admin user creation.")
        except RoleNotFoundException as rex:
            logger.error(f"Role not found: {rex}")
        except Exception as ex:
            logger.error(f"An unexpected error occurred: {ex}")
        finally:
            logger.info("Post-tenant-creation tasks completed.")

    async def enqueue(self, admin_user: CreateUserDto) -> None:
        logger.info("Enqueuing post-tenant-creation tasks.")
        await self._init_and_run(admin_user=admin_user)
