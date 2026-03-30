# Subscription plans in our DB (not Stripe API objects directly).

from __future__ import annotations

import uuid

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from api.domain.dtos.audit_logs_dto import AuditLogDto
from api.domain.dtos.subcription_plan_dto import CreateSubscriptionPlanDto
from api.domain.entities.subscription_plan import SubscriptionPlan
from api.domain.serialization import parse_uuid


class SubscriptionPlanRepository(BaseRepository[SubscriptionPlan], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(SubscriptionPlan)

    async def get_subscription_plan_by_id(
        self, subscription_id: str
    ) -> SubscriptionPlan | None:
        return await self.get(subscription_id)

    async def get_subscription_plan_by_user_id(
        self, user_id: str
    ) -> SubscriptionPlan | None:
        uid = uuid.UUID(str(user_id))
        return await self.single_or_none(user_id=uid)

    async def get_subscription_plans_by_tenant(
        self, tenant_id: str
    ) -> SubscriptionPlan | None:
        tid = uuid.UUID(str(tenant_id))
        return await self.single_or_none(tenant_id=tid)

    async def create_subscription_plan(
        self, subscription_plan: CreateSubscriptionPlanDto
    ) -> uuid.UUID | None:
        payload = {
            "tenant_id": parse_uuid(subscription_plan.tenant_id),
            "user_id": parse_uuid(subscription_plan.user_id),
            "plan_id": subscription_plan.plan_id,
            "is_trial": subscription_plan.is_trial,
            "actor": subscription_plan.actor,
            "plan_level": dict(subscription_plan.plan_level),
        }
        result = await self.create(payload)
        await self.add_audit_log(
            AuditLogDto(
                action="create",
                entity="SubscriptionPlan",
                user_id=str(subscription_plan.user_id)
                if subscription_plan.user_id
                else None,
                changes={
                    "Info": "Created new subscription plan with id " + str(result.id)
                },
                tenant_id=str(subscription_plan.tenant_id)
                if subscription_plan.tenant_id
                else None,
            )
        )
        return result.id

    async def update_subscription_plan(
        self, subscription_id: str, data: dict
    ) -> None:
        row = await self.update(subscription_id, data)
        if row is None:
            return
        await self.add_audit_log(
            AuditLogDto(
                action="update",
                entity="SubscriptionPlan",
                user_id=str(row.user_id) if row.user_id else None,
                changes={"Info": "Updated subscription plan with id " + subscription_id},
                tenant_id=str(row.tenant_id) if row.tenant_id else None,
            )
        )

    async def delete_subscription_plan(self, subscription_id: str) -> None:
        existing = await self.get_subscription_plan_by_id(subscription_id)
        if existing is None:
            await self.add_audit_log(
                AuditLogDto(
                    action="delete",
                    entity="SubscriptionPlan",
                    user_id=None,
                    changes={
                        "Info": "Attempted to delete non-existing subscription plan with id "
                        + subscription_id
                    },
                    tenant_id=None,
                )
            )
            return
        await self.delete(subscription_id)
        await self.add_audit_log(
            AuditLogDto(
                action="delete",
                entity="SubscriptionPlan",
                user_id=str(existing.user_id) if existing.user_id else None,
                changes={"Info": "Deleted subscription plan with id " + subscription_id},
                tenant_id=str(existing.tenant_id) if existing.tenant_id else None,
            )
        )
