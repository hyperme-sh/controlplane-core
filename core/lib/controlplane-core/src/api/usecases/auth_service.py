from fastapi_mail import MessageType
from pydantic import EmailStr

from api.common.dtos.token_dto import ActivationTokenPayloadDto
from api.common.exceptions import InvalidOperationException
from api.common.utils import get_email_sharing_link, get_logger
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.dtos.user_dto import UpdateUserDto
from api.domain.entities.user import User
from api.domain.interfaces.email_service import IEmailService
from api.infrastructure.security.jwt_token_service import JwtTokenService
from api.interfaces.email_templates.email_changes_confirmation_template_html import (
    email_changes_confirmation_template_html,
)
from api.interfaces.email_templates.notify_email_changes_template_html import (
    notify_email_change_template_html,
)
from api.usecases.audit_logs_service import AuditLogsService
from api.usecases.user_service import UserService

logger = get_logger(__name__)


class AuthService:
    def __init__(
        self,
        user_service: UserService,
        jwt_token_service: JwtTokenService,
        email_service: IEmailService,
        audit_log_service: AuditLogsService,
    ):
        self.user_service: UserService = user_service
        self.jwt_token_service: JwtTokenService = jwt_token_service
        self.email_service: IEmailService = email_service
        self.audit_log_service: AuditLogsService = audit_log_service
        logger.info("Initialized.")

    async def logout(self, user_id: str, tenant_id: str | None) -> None:
        await self.audit_log_service.create_audit_log(
            audit_log=AuditLogDto(
                entity="User",
                action="logout",
                user_id=user_id,
                tenant_id=tenant_id,
                changes={
                    "Info": "User logged out (Keycloak session cleared client-side)."
                },
            )
        )
        logger.info("User %s logged out (audit).", user_id)

    async def change_email_request(
        self, current_email: EmailStr, new_email: EmailStr, domain: str
    ) -> None:
        if current_email == new_email:
            raise InvalidOperationException(
                "The new email address must be different from the current email address."
            )
        if await self.user_service.check_email_exists(email=new_email):
            raise InvalidOperationException(
                f"The email '{new_email}' is already in use by another account."
            )
        user = await self.user_service.find_by_email(email=current_email)
        activation_token = await self.jwt_token_service.encode_activation_token(
            payload=ActivationTokenPayloadDto(
                user_id=str(user.id),
                email=new_email,
                type="change_email_confirmation",
                tenant_id=str(user.tenant_id) if user.tenant_id else None,
            )
        )
        link = get_email_sharing_link(
            token=activation_token,
            user_id=str(user.id),
            domain=domain,
            type="change_email_confirmation",
            tenant_id=str(user.tenant_id),
        )
        html = email_changes_confirmation_template_html(
            user_first_name=user.first_name, email_change_link=link
        )
        await self.email_service.send_email(
            to=user.email,
            subject="Confirm Your Email Change",
            body=html,
            type=MessageType.html,
        )

    async def change_email_confirmation(self, token: str) -> None:
        payload = await self.jwt_token_service.verify_change_email_token(token=token)
        user: User = await self.user_service.get_user_by_id(user_id=payload.user_id)
        if await self.user_service.check_email_exists(email=payload.email):
            raise InvalidOperationException(
                f"The email '{payload.email}' is already in use by another account."
            )
        await self.user_service.update_user(str(user.id), UpdateUserDto(email=payload.email))
        html = notify_email_change_template_html(user_first_name=user.first_name)
        await self.email_service.send_email(
            to=user.email,
            subject="Your Email Has Been Changed Successfully",
            body=html,
            type=MessageType.html,
        )
