from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr

from api.common.enums.gender import Gender
from api.domain.entities.user import User
from api.domain.serialization import serialize_user


class BaseUserDto(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    gender: Gender
    tenant_id: str | None = None
    role_id: str | None = None
    sso_provider_id: Optional[str] = None


class CreateUserDto(BaseUserDto):
    password: str
    sub_domain: Optional[str] = None


class UpdateUserDto(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    gender: Optional[Gender] = None
    image_url: Optional[str] = None
    role_id: Optional[str] = None
    is_active: Optional[bool] = None
    activated_at: Optional[datetime] = None
    tenant_id: str | None = None
    sso_provider_id: Optional[str] = None


class UserDto(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: EmailStr
    gender: Gender
    role_id: Optional[str] = None
    is_active: bool
    activated_at: Optional[str] = None
    image_url: Optional[str] = None
    created_at: str
    updated_at: str
    tenant_id: Optional[str] = None
    sso_provider_id: Optional[str] = None


class UserListDto(BaseModel):
    users: List[UserDto]
    skip: int
    limit: int
    total: int
    has_previous: bool
    has_next: bool


def user_from_row(row: User) -> UserDto:
    d = serialize_user(row)
    g = d["gender"]
    if isinstance(g, str):
        g = Gender(g)
    return UserDto(
        id=d["id"],
        first_name=d["first_name"],
        last_name=d["last_name"],
        email=d["email"],
        gender=g,
        role_id=d["role_id"],
        is_active=d["is_active"],
        activated_at=d["activated_at"],
        image_url=d["image_url"],
        created_at=d["created_at"] or "",
        updated_at=d["updated_at"] or "",
        tenant_id=d["tenant_id"],
        sso_provider_id=d["sso_provider_id"],
    )


class CreateUserResponseDto(BaseModel):
    id: str


class UserActivationRequestDto(BaseModel):
    token: str


class UserResendActivationEmailRequestDto(BaseModel):
    email: EmailStr
    id: str
    first_name: str
    tenant_id: Optional[str] = None


class UserProfileImageUpdateDto(BaseModel):
    image_url: str


class UserRoleUpdateRequestDto(BaseModel):
    role_id: str
