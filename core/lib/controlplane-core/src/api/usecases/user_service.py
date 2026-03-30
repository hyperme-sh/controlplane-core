from __future__ import annotations

import uuid
from typing import Any

from pydantic import EmailStr

from api.common.exceptions import InvalidOperationException
from api.common.security import hash_it
from api.common.utils import get_logger
from api.core.exceptions import EmailAlreadyExistsException, UserNotFoundException
from api.domain.dtos.dashboard_dto import TimeSeriesDto
from api.domain.dtos.user_dto import CreateUserDto, UpdateUserDto, UserDto, UserListDto
from api.domain.entities.user import User
from api.domain.entities.user_password_reset import UserPasswordReset
from api.infrastructure.persistence.repositories.user_password_reset_repository_impl import (
    UserPasswordResetRepository,
)
from api.infrastructure.persistence.repositories.user_repository_impl import UserRepository

logger = get_logger(__name__)


class UserService:
    def __init__(
        self,
        user_repository: UserRepository,
        user_password_reset_repository: UserPasswordResetRepository,
    ):
        self.user_repository = user_repository
        self.user_password_reset_repository = user_password_reset_repository
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
        existing = await self.user_repository.single_or_none(email=user_data.email)
        if existing is not None:
            raise EmailAlreadyExistsException(user_data.email)
        user_data.password = hash_it(user_data.password)
        user_id = await self.user_repository.create(user_data)
        return user_id

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
