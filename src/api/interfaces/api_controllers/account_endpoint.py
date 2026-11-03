from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import EmailStr

from controlplane_api.exceptions import InvalidOperationException
from controlplane_api.utils import get_logger
from api.core.container import get_auth_service
from api.domain.dtos.auth_dto import ChangeEmailConfirmRequestDto, ChangeEmailRequestDto, ChangeEmailResponseDto, MeResponseDto
from api.infrastructure.security.current_user import CurrentUser
from api.interfaces.middlewares.database_middleware import FrontendHost
from api.usecases.auth_service import AuthService

logger = get_logger(__name__)

router = APIRouter(prefix="/account", tags=["Account"])


@router.get("/me", response_model=MeResponseDto, status_code=status.HTTP_200_OK)
async def read_users_me(current_user: CurrentUser) -> MeResponseDto:
    return current_user


@router.get("/logout", status_code=status.HTTP_200_OK)
async def logout(
    current_user: CurrentUser,
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    await auth_service.logout(
        user_id=str(current_user.id),
        tenant_id=str(current_user.tenant_id) if current_user.tenant_id else None,
    )


@router.patch("/change_email_request", status_code=status.HTTP_202_ACCEPTED)
async def change_email(
    request: ChangeEmailRequestDto,
    current_user: CurrentUser,
    frontend_host: FrontendHost,
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    await auth_service.change_email_request(
        current_email=EmailStr(current_user.email),
        new_email=request.new_email,
        domain=frontend_host,
    )


@router.patch(
    "/change_email_confirmation",
    status_code=status.HTTP_200_OK,
    response_model=ChangeEmailResponseDto,
)
async def change_email_confirmation(
    request: ChangeEmailConfirmRequestDto,
    auth_service: AuthService = Depends(get_auth_service),
) -> ChangeEmailResponseDto:
    try:
        await auth_service.change_email_confirmation(token=request.token)
    except InvalidOperationException:
        raise HTTPException(
            status_code=400, detail="Email change confirmation failed."
        ) from None
    return ChangeEmailResponseDto(message="Email changed successfully.")


@router.post("/resend_activation_email", status_code=status.HTTP_410_GONE)
async def resend_activation_email() -> dict[str, str]:
    """Removed: activation is handled by Keycloak."""
    return {"detail": "Use Keycloak email verification; this endpoint is retired."}
