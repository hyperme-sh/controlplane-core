from faker import Faker

from api.common.enums.gender import Gender
from api.common.security import hash_it
from api.common.seeder_utils import get_seed_roles
from controlplane_api.utils import get_logger
from controlplane_api.config import settings
from api.core.container import get_role_service, get_user_service
from api.domain.dtos.role_dto import UpdateRoleDto
from api.domain.dtos.user_dto import CreateUserDto, UpdateUserDto
from controlplane_api.permissions import Permission
from api.domain.enum.role import RoleType
from api.infrastructure.persistence.repositories.role_repository_impl import \
    RoleRepository
from api.infrastructure.persistence.repositories.user_repository_impl import \
    UserRepository

fake = Faker()

logger = get_logger(__name__)

# Coordinates seed across gateway / auth-service / user-service / payment-service (each has its own container /tmp).
_SEED_LOCK_KEY1 = 7_408_263
_SEED_LOCK_KEY2 = 1_928_374


def _role_name(name: RoleType | str) -> str:
    return name.value if isinstance(name, RoleType) else str(name)


def _permission_values(permissions: list | None) -> list[str]:
    values: list[str] = []
    for perm in permissions or []:
        values.append(perm.value if isinstance(perm, Permission) else str(perm))
    return values


async def seed_platform_roles(role_repo: RoleRepository) -> None:
    """Prod-safe role rows only. No users, no ADMIN_PASSWORD hash, no fake guests.

    Platform rows are ``tenant_id IS NULL``. A tenant-scoped role with the
    same name is a different row and must not be selected or merged.
    """
    for role in get_seed_roles():
        name = _role_name(role.name)
        existing = await role_repo.find_platform_role(name)
        if existing is not None:
            logger.info("Role %s already exists. Skipping...", name)
            continue
        inserted = await role_repo.insert_platform_role_if_absent(name, role.description)
        existing = await role_repo.find_platform_role(name)
        if existing is None:
            raise RuntimeError(f"platform role {name} was not inserted")
        if not inserted:
            logger.info("Role %s already exists. Skipping...", name)
            continue
        permissions = list(role.permissions)
        if name == RoleType.ADMIN.value and Permission.HOST_MANAGE_TENANTS not in permissions:
            permissions.append(Permission.HOST_MANAGE_TENANTS)
        await role_repo.update(
            role_id=str(existing.id),
            data=UpdateRoleDto(
                name=name,
                description=role.description,
                permissions=permissions,
            ),
        )
        logger.info("Seeded role: %s", name)
    await _ensure_admin_host_manage_tenants(role_repo)


async def _ensure_admin_host_manage_tenants(role_repo: RoleRepository) -> None:
    admin = await role_repo.find_platform_role(RoleType.ADMIN.value)
    if admin is None:
        return
    values = _permission_values(admin.permissions)
    if Permission.HOST_MANAGE_TENANTS.value in values:
        return
    permissions = [Permission(v) for v in values]
    permissions.append(Permission.HOST_MANAGE_TENANTS)
    await role_repo.update(
        role_id=str(admin.id),
        data=UpdateRoleDto(
            name=_role_name(admin.name),
            description=admin.description,
            permissions=permissions,
        ),
    )
    logger.info("Attached %s on admin", Permission.HOST_MANAGE_TENANTS.value)


async def seed_development_users(
    user_repo: UserRepository, role_repo: RoleRepository
) -> None:
    existing_admin = await user_repo.single_or_none(email=settings.admin_email)
    if existing_admin is None:
        existing_role_admin = await role_repo.find_platform_role(RoleType.ADMIN.value)
        if existing_role_admin is None:
            logger.error("ADMIN role is missing; cannot seed admin user. Seed roles first.")
            return
        create_user = CreateUserDto(
            email=settings.admin_email,
            first_name="Admin",
            last_name="User",
            password=hash_it(settings.admin_password),
            gender=Gender.MALE,
        )
        user_id = await user_repo.create(data=create_user)
        logger.info("Created admin user with email: %s", settings.admin_email)
        await user_repo.update(
            user_id=str(user_id),
            data=UpdateUserDto(role_id=str(existing_role_admin.id)),
        )
        logger.info("Updated admin user with role: %s", existing_role_admin.name)

    existing_users = await user_repo.count()
    if existing_users < 10:
        logger.info("Seeding fake users...")
        existing_role_guest = await role_repo.find_platform_role(RoleType.GUEST.value)
        if existing_role_guest is None:
            logger.warning("GUEST role missing; skipping fake user seed.")
            return
        for _ in range(100):
            fake_user = CreateUserDto(
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=fake.email(),
                role_id=str(existing_role_guest.id),
                gender=fake.random_element(
                    elements=[
                        Gender.MALE.value,
                        Gender.FEMALE.value,
                        Gender.PREFER_NOT_TO_SAY.value,
                        Gender.OTHER.value,
                    ]
                ),
                password=hash_it("Test@123!"),
            )
            await user_repo.create(data=fake_user)
        logger.info("Seeded fake users.")


async def apply_seed(
    role_repo: RoleRepository,
    user_repo: UserRepository,
    *,
    fastapi_env: str,
) -> None:
    await seed_platform_roles(role_repo)
    if fastapi_env == "development":
        await seed_development_users(user_repo, role_repo)
        logger.info("Seeding completed.")
        return
    logger.info(
        "Development user seeder skipped outside development (fastapi_env=%s).",
        fastapi_env,
    )


async def seed_initial_data():
    from sqlalchemy import text

    from api.infrastructure.persistence.db_session import get_session_maker, reset_db_session, set_db_session

    maker = get_session_maker()
    async with maker() as session:
        await session.execute(
            text("SELECT pg_advisory_lock(:k1, :k2)").bindparams(
                k1=_SEED_LOCK_KEY1, k2=_SEED_LOCK_KEY2
            )
        )
        tok = None
        try:
            tok = set_db_session(session)
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', '', true)")
            )
            user_repo: UserRepository = get_user_service().user_repository
            role_repo: RoleRepository = get_role_service().role_repository
            await apply_seed(
                role_repo, user_repo, fastapi_env=settings.fastapi_env
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            if tok is not None:
                reset_db_session(tok)
            await session.execute(
                text("SELECT pg_advisory_unlock(:k1, :k2)").bindparams(
                    k1=_SEED_LOCK_KEY1, k2=_SEED_LOCK_KEY2
                )
            )
