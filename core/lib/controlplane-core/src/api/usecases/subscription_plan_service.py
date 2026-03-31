import uuid

from api.common.exceptions import InvalidOperationException
from api.domain.dtos.subcription_plan_dto import (
    CreateSubscriptionPlanDto,
    SubscriptionPlanDto,
)
from api.domain.entities.subscription_plan import SubscriptionPlan
from api.domain.serialization import parse_uuid
from api.infrastructure.persistence.repositories.subscription_plan_repository_impl import (
    SubscriptionPlanRepository,
)


def _row_to_dto(row: SubscriptionPlan) -> SubscriptionPlanDto:
    return SubscriptionPlanDto(
        id=str(row.id),
        plan_level=dict(row.plan_level or {}),
        is_trial=row.is_trial,
        tenant_id=str(row.tenant_id) if row.tenant_id else None,
        actor=row.actor,
        user_id=str(row.user_id) if row.user_id else None,
        plan_id=row.plan_id,
    )


class SubscriptionPlanService:
    def __init__(self, subscription_plan_repository: SubscriptionPlanRepository):
        self.subscription_plan_repository: SubscriptionPlanRepository = (
            subscription_plan_repository
        )

    async def get_subscription_plan_by_id(
        self, subscription_id: str
    ) -> SubscriptionPlanDto | None:
        result = await self.subscription_plan_repository.get_subscription_plan_by_id(
            subscription_id
        )
        if result:
            return _row_to_dto(result)
        return None

    async def get_subscription_plans_by_tenant(
        self, tenant_id: str
    ) -> SubscriptionPlanDto | None:
        result = await self.subscription_plan_repository.get_subscription_plans_by_tenant(
            tenant_id
        )
        if result:
            return _row_to_dto(result)
        return None

    async def get_subscription_plan_by_user_id(
        self, user_id: str
    ) -> SubscriptionPlanDto | None:
        result = await self.subscription_plan_repository.get_subscription_plan_by_user_id(
            user_id
        )
        if result:
            return _row_to_dto(result)
        return None

    async def create_trial_plan_for_tenant(self, tenant_id: str, plan_id: str) -> None:
        subscription_plan = CreateSubscriptionPlanDto(
            tenant_id=tenant_id,
            plan_id=plan_id,
            is_trial=True,
            user_id=None,
            actor="tenant",
            plan_level={"premium": 2},
        )
        result = await self.subscription_plan_repository.create_subscription_plan(
            subscription_plan=subscription_plan
        )
        if result is None:
            raise InvalidOperationException("Failed to create trial subscription plan.")

    async def ensure_paid_plan_for_tenant(
        self,
        tenant_id: str,
        *,
        user_id: str | None = None,
        plan_id: str = "payment_succeeded",
    ) -> uuid.UUID:
        """
        Upsert a non-trial subscription plan with premium tier (payment checkout success).
        Returns the subscription plan row id for attaching to the tenant.
        """
        paid_level = {"premium": 2}
        existing = await self.subscription_plan_repository.get_subscription_plans_by_tenant(
            tenant_id
        )
        if existing is not None:
            data: dict = {
                "plan_level": paid_level,
                "is_trial": False,
                "plan_id": plan_id,
            }
            if user_id is not None:
                data["user_id"] = parse_uuid(user_id)
            await self.subscription_plan_repository.update_subscription_plan(
                str(existing.id), data
            )
            return existing.id
        created = await self.subscription_plan_repository.create_subscription_plan(
            CreateSubscriptionPlanDto(
                tenant_id=tenant_id,
                plan_id=plan_id,
                is_trial=False,
                user_id=user_id,
                actor="payment",
                plan_level=paid_level,
            )
        )
        if created is None:
            raise InvalidOperationException("Failed to create paid subscription plan.")
        return created
