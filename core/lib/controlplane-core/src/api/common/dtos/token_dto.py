from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, field_serializer

from api.common.utils import get_logger
from api.domain.dtos.role_dto import RoleDto

logger = get_logger(__name__)


class TokenPayloadDto(BaseModel):
    sub: str
    email: EmailStr
    is_active: bool
    activated_at: datetime | None = None
    tenant_id: str | None = None
    role: RoleDto | None = None

    @field_serializer("sub", "tenant_id", "activated_at")
    def serialize_ids(self, v: str | datetime | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return str(v)
        return str(v)


class RefreshTokenPayloadDto(BaseModel):
    sub: str
    tenant_id: str | None = None
    type: Literal["refresh"] = "refresh"

    @field_serializer("sub", "tenant_id")
    def serialize_ids(self, v: str | None) -> str | None:
        if v is None:
            return None
        return str(v)


class AccessTokenDto(BaseModel):
    access_token: str
    token_type: str
    expires_in: datetime


class RefreshTokenDto(BaseModel):
    refresh_token: str
    refresh_token_expires_in: datetime


class TokenSetDto(AccessTokenDto, RefreshTokenDto):
    expires_in: int
    refresh_token_expires_in: int


class TokenRefreshRequestDto(BaseModel):
    refresh_token: str


class ActivationTokenPayloadDto(BaseModel):
    user_id: str
    email: EmailStr
    tenant_id: str | None = None
    type: Literal["activation", "password_reset_confirmation", "change_email_confirmation"] = "activation"
    jwt_secret: str | None = None


class VerifyEmailTokenPayloadDto(BaseModel):
    user_id: str
    email: EmailStr
