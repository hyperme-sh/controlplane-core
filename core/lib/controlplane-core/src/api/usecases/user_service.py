from __future__ import annotations

import uuid
from typing import Any

from pydantic import EmailStr, TypeAdapter

from api.common.enums.gender import Gender
from controlplane_api.event_types import USER_CREATED_V1
from controlplane_api.exceptions import InvalidOperationException, UnauthorizedException
from controlplane_api.config import settings
from api.common.security import hash_it
from controlplane_api.utils import get_logger
from api.core.exceptions import EmailAlreadyExistsException, UserNotFoundException
from api.domain.dtos.dashboard_dto import TimeSeriesDto
from api.domain.dtos.user_dto import CreateUserDto, UpdateUserDto, UserDto, UserListDto
from api.domain.entities.user import User
from api.domain.enum.role import RoleType
from api.domain.entities.user_password_reset import UserPasswordReset
from api.infrastructure.persistence.repositories.user_password_reset_repository_impl import (
    UserPasswordResetRepository,
)
from api.domain.serialization import parse_uuid
from api.infrastructure.messaging.tenant_event_publisher import (
    get_tenant_event_publisher,
)
from api.infrastructure.persistence.repositories.role_repository_impl import RoleRepository
from api.infrastructure.persistence.repositories.user_repository_impl import UserRepository

logger = get_logger(__name__)

_email_adapter = TypeAdapter(EmailStr)

_MS_CLAIM_EMAIL = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
_MS_CLAIM_UPN = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn"


def _str_claim(claims: dict[str, object], key: str) -> str | None:
    v = claims.get(key)
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def _email_candidates_from_raw(raw: str) -> list[str]:
    out = [raw]
    if "#EXT#" in raw:
        head = raw.split("#EXT#", 1)[0].strip()
        if head and head not in out:
            out.append(head)
    return out


def _first_valid_email_from_oidc_claims(claims: dict[str, object]) -> str | None:
    keys: list[str] = []
    seen: set[str] = set()
    for k in (
        "email",
        "preferred_username",
        "upn",
        "unique_name",
        _MS_CLAIM_EMAIL,
        _MS_CLAIM_UPN,
    ):
        if k not in seen:
            seen.add(k)
            keys.append(k)
    for k in settings.oidc_extra_email_claim_keys():
        if k not in seen:
            seen.add(k)
            keys.append(k)
    for key in keys:
        raw = _str_claim(claims, key)
        if not raw:
            continue
        for cand in _email_candidates_from_raw(raw):
            try:
                return str(_email_adapter.validate_python(cand)).lower()
            except Exception:
                continue
    return None


def _oidc_subject_str(claims: dict[str, object]) -> str:
    sub_raw = claims.get("sub")
    if sub_raw is None or sub_raw == "":
        raise UnauthorizedException("Invalid token: missing sub")
    sub = str(sub_raw).strip()
    if not sub:
        raise UnauthorizedException("Invalid token: missing sub")
    return sub


def _groups_from_oidc_claims(claims: dict[str, object]) -> list[str]:
    raw = claims.get("groups")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [s]
    return []


def _oidc_claims_want_admin(email: str, claims: dict[str, object]) -> bool:
    em = email.strip().lower()
    if em in settings.oidc_bootstrap_admin_email_set():
        return True
    gids = settings.oidc_entra_admin_group_id_set()
    if not gids:
        return False
    for g in _groups_from_oidc_claims(claims):
        if g.strip().lower() in gids:
            return True
    return False


class UserService:
    def __init__(
        self,
        user_repository: UserRepository,
        user_password_reset_repository: UserPasswordResetRepository,
        role_repository: RoleRepository,
    ):
        self.user_repository = user_repository
        self.user_password_reset_repository = user_password_reset_repository
        self.role_repository = role_repository
        logger.info("Initialized.")

    async def list_users(self, skip: int = 0, limit: int = 10) -> UserListDto:
        return await self.user_repository.list(skip=skip, limit=limit)

    async def find_by_email(self, email: EmailStr) -> User:
        existing = await self.user_repository.single_or_none(email=email)
        if existing is None:
            raise UserNotFoundException(email)
        return existing

    async def get_user_by_id(self, user_id: str) -> User:
        existing = await self.user_repository.get(user_id)
        if existing is None:
            raise UserNotFoundException(user_id)
        return existing

    async def create_user(self, user_data: CreateUserDto) -> uuid.UUID:
        tid = parse_uuid(user_data.tenant_id) if user_data.tenant_id else None
        if tid is None:
            existing = await self.user_repository.single_or_none_platform_email_ci(
                str(user_data.email)
            )
        else:
            existing = await self.user_repository.single_or_none(
                email=user_data.email, tenant_id=tid
            )
        if existing is not None:
            raise EmailAlreadyExistsException(user_data.email)
        to_create = user_data.model_copy()
        if to_create.password is not None:
            to_create = to_create.model_copy(update={"password": hash_it(to_create.password)})
        user_id = await self.user_repository.create(to_create)
        await get_tenant_event_publisher().publish(
            tenant_id=tid,
            event_type=USER_CREATED_V1,
            payload={
                "user_id": str(user_id),
                "email": str(to_create.email),
                "tenant_id": to_create.tenant_id,
            },
        )
        return user_id

    async def _maybe_promote_oidc_admin(
        self, user_id: str, email: str, claims: dict[str, object]
    ) -> None:
        if not _oidc_claims_want_admin(email, claims):
            return
        admin = await self.role_repository.single_or_none(name=RoleType.ADMIN.value)
        if admin is None:
            return
        row = await self.user_repository.get(user_id)
        if row is None or str(row.role_id) == str(admin.id):
            return
        await self.user_repository.update(
            user_id, UpdateUserDto(role_id=str(admin.id))
        )

    async def ensure_user_from_keycloak_claims(
        self, claims: dict[str, object]
    ) -> User:
        sub = _oidc_subject_str(claims)
        email = _first_valid_email_from_oidc_claims(claims)
        if email is None:
            logger.warning(
                "OIDC JIT: no valid email in access token (claim keys: %s)",
                sorted(claims.keys()),
            )
            raise UnauthorizedException(
                "Access token has no valid email claim. Request scope 'email' and map IdP "
                "email into the access token, or set OIDC_EXTRA_EMAIL_CLAIMS for a custom "
                "claim name (see docs/oidc-console.md)."
            )
        if claims.get("email_verified") is False and not settings.oidc_relax_email_verified:
            raise UnauthorizedException("Email not verified at identity provider")

        row = await self.user_repository.single_or_none(keycloak_sub=sub)
        if row:
            await self._maybe_promote_oidc_admin(str(row.id), email, claims)
            updated = await self.user_repository.get(str(row.id))
            return updated or row

        row = await self.user_repository.single_or_none_platform_email_ci(email)
        if row is None:
            row = await self.user_repository.single_or_none(email=email)
        if row:
            link_updates = UpdateUserDto(keycloak_sub=sub)
            if _oidc_claims_want_admin(email, claims):
                admin = await self.role_repository.single_or_none(name=RoleType.ADMIN.value)
                if admin is not None:
                    link_updates = UpdateUserDto(
                        keycloak_sub=sub, role_id=str(admin.id)
                    )
            await self.user_repository.update(str(row.id), link_updates)
            updated = await self.user_repository.get(str(row.id))
            if updated is None:
                raise InvalidOperationException("Failed to link Keycloak identity")
            return updated

        guest = await self.role_repository.single_or_none(name=RoleType.GUEST.value)
        if guest is None:
            raise InvalidOperationException("GUEST role not found; seed the database")
        admin = await self.role_repository.single_or_none(name=RoleType.ADMIN.value)
        role = admin if admin and _oidc_claims_want_admin(email, claims) else guest

        first = str(claims.get("given_name") or claims.get("name") or email.split("@")[0])[
            :255
        ]
        last = str(claims.get("family_name") or "")[:255]
        dto = CreateUserDto(
            email=email,
            first_name=first,
            last_name=last,
            gender=Gender.PREFER_NOT_TO_SAY,
            role_id=str(role.id),
            password=None,
            keycloak_sub=sub,
        )
        uid = await self.user_repository.create(dto)
        tid = parse_uuid(dto.tenant_id) if dto.tenant_id else None
        await get_tenant_event_publisher().publish(
            tenant_id=tid,
            event_type=USER_CREATED_V1,
            payload={
                "user_id": str(uid),
                "email": str(dto.email),
                "tenant_id": dto.tenant_id,
            },
        )
        created = await self.user_repository.get(str(uid))
        if created is None:
            raise InvalidOperationException("Failed to create user from Keycloak")
        return created

    async def update_user(self, user_id: str, user_data: UpdateUserDto) -> User | None:
        existing = await self.user_repository.get(user_id)
        if existing is None:
            raise UserNotFoundException(user_id)
        return await self.user_repository.update(user_id, user_data)

    async def delete_user(self, user_id: str) -> None:
        if not await self.user_repository.delete(user_id):
            raise UserNotFoundException(user_id)

    async def update_user_password(self, user_id: str, new_password: str) -> User | None:
        existing = await self.user_repository.get(user_id)
        if existing is None:
            raise UserNotFoundException(user_id)
        hashed_password = hash_it(new_password)
        return await self.user_repository.update(user_id, {"password": hashed_password})

    async def total_count(self, params: Any | None = None) -> int:
        return await self.user_repository.count(params=params)

    async def request_password_reset(self, email: EmailStr) -> UserPasswordReset:
        try:
            user = await self.find_by_email(email=email)
            return await self.user_password_reset_repository.set_password_reset(
                user_id=str(user.id),
                first_name=user.first_name,
                tenant_id=str(user.tenant_id) if user.tenant_id else None,
            )
        except Exception as e:
            logger.error(f"Error setting password reset for email {email}: {e}")
            raise InvalidOperationException(message="Failed to set password reset.")

    async def retrieve_password_reset_data_by_user_id(self, user_id: str) -> UserPasswordReset | None:
        return await self.user_password_reset_repository.single_or_none(user_id=uuid.UUID(user_id))

    async def clear_password_reset_data_by_user_id(self, user_id: str) -> bool:
        logger.info(f"Clearing password reset data for user_id: {user_id}")
        return await self.user_password_reset_repository.delete_by_user_id(user_id)

    async def check_email_exists(self, email: EmailStr) -> bool:
        existing = await self.user_repository.single_or_none(email=email)
        return existing is not None

    async def aggregate(self, pipeline: list[dict]) -> list[TimeSeriesDto]:
        logger.warning("User aggregate not implemented for SQL backend.")
        return []
