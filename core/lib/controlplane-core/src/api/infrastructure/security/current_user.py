from typing import Annotated

from fastapi import Depends

from controlplane_api.exceptions import InvalidOperationException, UnauthorizedException
from api.common.security import bearer_token_optional, bearer_token_required
from controlplane_api.utils import get_logger
from api.domain.dtos.auth_dto import MeResponseDto
from api.domain.dtos.role_dto import role_from_row
from api.domain.dtos.user_dto import user_from_row
from api.infrastructure.security.keycloak_jwt import get_keycloak_jwt_validator
from api.usecases.role_service import RoleService
from api.usecases.subscription_plan_service import SubscriptionPlanService
from api.usecases.user_service import UserService
from api.core.container import (
    get_role_service,
    get_subscription_plan_service,
    get_user_service,
)

logger = get_logger(__name__)


async def resolve_me_optional_from_token(
    token: str | None,
    user_service: UserService,
    role_service: RoleService,
    subscription_service: SubscriptionPlanService,
) -> MeResponseDto | None:
    if token is None:
        return None
    try:
        validator = get_keycloak_jwt_validator()
        claims = await validator.validate(token)
        user = await user_service.ensure_user_from_keycloak_claims(claims)
    except Exception as e:
        logger.info("Optional user not resolved: %s", e)
        return None

    user_dto = user_from_row(user)
    role_dto = None
    if user.role_id is not None:
        role = await role_service.get_role_by_id(str(user.role_id))
        role_dto = role_from_row(role)
    subscription = await subscription_service.get_subscription_plan_by_user_id(
        str(user.id)
    )
    return MeResponseDto(
        **user_dto.model_dump(), role=role_dto, subscription=subscription
    )


async def get_current_user(
    token: Annotated[str, Depends(bearer_token_required)],
    user_service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
    subscription_service: SubscriptionPlanService = Depends(
        get_subscription_plan_service
    ),
) -> MeResponseDto:
    validator = get_keycloak_jwt_validator()
    try:
        claims = await validator.validate(token)
    except Exception as e:
        logger.warning("Keycloak JWT validation failed: %s", e)
        raise UnauthorizedException("Invalid or expired access token") from e

    try:
        user = await user_service.ensure_user_from_keycloak_claims(claims)
    except InvalidOperationException as e:
        logger.error("User resolution failed: %s", e)
        raise UnauthorizedException(str(e.message)) from e

    user_dto = user_from_row(user)
    role_dto = None
    if user.role_id is not None:
        role = await role_service.get_role_by_id(str(user.role_id))
        role_dto = role_from_row(role)
    subscription = await subscription_service.get_subscription_plan_by_user_id(
        str(user.id)
    )
    return MeResponseDto(
        **user_dto.model_dump(), role=role_dto, subscription=subscription
    )


CurrentUser = Annotated[MeResponseDto, Depends(get_current_user)]


async def current_user_optional(
    token: Annotated[str | None, Depends(bearer_token_optional)],
    user_service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
    subscription_service: SubscriptionPlanService = Depends(
        get_subscription_plan_service
    ),
) -> MeResponseDto | None:
    return await resolve_me_optional_from_token(
        token, user_service, role_service, subscription_service
    )


CurrentUserOptional = Annotated[MeResponseDto | None, Depends(current_user_optional)]
