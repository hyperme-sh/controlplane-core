from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.common.utils import get_logger
from api.core.exceptions import StripeSettingsNotFoundException
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.dtos.stripe_setting_dto import CreateStripeSettingDto
from api.domain.entities.stripe_settings import StripeSettings
from api.domain.serialization import parse_uuid

logger = get_logger(__name__)


class StripeSettingsRepository(BaseRepository[StripeSettings], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(StripeSettings)

    async def store_stripe_settings(self, stripe_config: CreateStripeSettingDto) -> None:
        settings = await self.single_or_none()
        if settings:
            settings.stripe_secret_key = stripe_config.stripe_secret_key
            settings.default_currency = stripe_config.default_currency
            settings.stripe_webhook_secret = stripe_config.stripe_webhook_secret
            settings.mode = stripe_config.mode
            settings.trial_period_days = stripe_config.trial_period_days
            await self.session.flush()
            await self.session.refresh(settings)
            await self.add_audit_log(
                AuditLogDto(
                    action="update",
                    changes={"info": f"Updated Stripe settings for tenant {settings.tenant_id}"},
                    entity="StripeSettings",
                    tenant_id=str(settings.tenant_id) if settings.tenant_id else None,
                    user_id=None,
                )
            )
        else:
            payload = stripe_config.model_dump()
            payload["tenant_id"] = parse_uuid(stripe_config.tenant_id)
            await self.create(payload)
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    changes={"info": "Created Stripe settings"},
                    entity="StripeSettings",
                    tenant_id=str(stripe_config.tenant_id) if stripe_config.tenant_id else None,
                    user_id=None,
                )
            )

    async def get_stored_stripe_settings(self) -> StripeSettings:
        try:
            result = await self.single_or_none()
            if result is None:
                raise StripeSettingsNotFoundException("Stripe settings not found.")
            return result
        except StripeSettingsNotFoundException:
            raise
        except Exception as e:
            logger.error(f"Error fetching stored Stripe settings: {e}")
            await self.add_audit_log(
                AuditLogDto(
                    action="error",
                    changes={"info": f"Attempted to read Stripe settings, error: {e}"},
                    entity="StripeSettings",
                    tenant_id=None,
                    user_id=None,
                )
            )
            raise StripeSettingsNotFoundException("Stripe settings not found.")


class PaymentRepository(StripeSettingsRepository):
    pass
