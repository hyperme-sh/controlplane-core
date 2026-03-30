from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from passlib.hash import pbkdf2_sha256

from api.common.exceptions import UnauthorizedException
from api.core.config import settings

JWT_SECRET = settings.jwt_secret

REFRESH_TOKEN_SECRET = settings.refresh_token_secret

ALGORITHM = settings.algorithm

REFRESH_ALGORITHM = settings.refresh_algorithm

ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days

ACTIVATION_TOKEN_EXPIRE_HOURS = 24  # Token expires in 24 hours

http_bearer_scheme = HTTPBearer(auto_error=False)
tenant_header = APIKeyHeader(name="x-tenant-id", auto_error=False)


async def bearer_token_optional(
    creds: Annotated[
        HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)
    ],
) -> str | None:
    if creds is None:
        return None
    return creds.credentials


async def bearer_token_required(
    creds: Annotated[
        HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)
    ],
) -> str:
    if creds is None or not creds.credentials:
        raise UnauthorizedException("Not authenticated")
    return creds.credentials


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pbkdf2_sha256.verify(plain_password, hashed_password)


def hash_it(password: str) -> str:
    return pbkdf2_sha256.hash(password)


# Utility function to hash and validate values (like tokens) with secret. Don't use for passwords.
def validate_hashed_value(value: str, hashed_value: str) -> bool:
    """Validate a value against its hashed version. Returns True if they match, False otherwise."""
    v = value + JWT_SECRET
    return pbkdf2_sha256.verify(v, hashed_value)