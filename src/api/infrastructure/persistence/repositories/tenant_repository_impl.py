from __future__ import annotations

import uuid
from typing import List

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.common.audit_logs_repository import AuditLogRepository
from api.common.base_repository import BaseRepository
from controlplane_api.exceptions import ConflictException
from controlplane_api.utils import get_logger
from controlplane_api.dtos.audit import AuditLogDto
from api.domain.dtos.tenant_dto import (
    CreateTenantDto,
    FeatureDto,
    TenantDto,
    TenantListDto,
)
from api.domain.entities.tenant import Feature, Tenant
from api.domain.enum.feature import Feature as FeatureEnum
from api.domain.serialization import serialize_tenant

logger = get_logger(__name__)

default_enable_features = [
    Feature(name=FeatureEnum.REPORT, enabled=True),
]


def _tenant_to_dto(row: Tenant) -> TenantDto:
    s = serialize_tenant(row)
    feats_raw = s.get("features") or []
    feats: List[FeatureDto] = []
    for f in feats_raw:
        if isinstance(f, dict) and "name" in f:
            try:
                feats.append(FeatureDto(name=FeatureEnum(f["name"]), enabled=bool(f.get("enabled", False))))
            except ValueError:
                continue
    return TenantDto(
        id=s["id"],
        name=s["name"],
        subdomain=s["subdomain"],
        is_active=s["is_active"],
        custom_domain=s["custom_domain"],
        custom_domain_status=s["custom_domain_status"],  # type: ignore[arg-type]
        features=feats,
        subscription_id=s["subscription_id"],
        created_at=s["created_at"],
        updated_at=s["updated_at"],
    )


class TenantRepository(BaseRepository[Tenant], AuditLogRepository):
    def __init__(self) -> None:
        super().__init__(Tenant)

    async def list(self, skip: int = 0, limit: int = 10) -> TenantListDto:
        cnt = await self.session.execute(select(func.count()).select_from(Tenant))
        total = int(cnt.scalar_one())
        res = await self.session.execute(select(Tenant).offset(skip).limit(limit))
        docs = list(res.scalars().all())
        tenant_dto = [_tenant_to_dto(doc) for doc in docs]
        return TenantListDto(
            tenants=tenant_dto,
            skip=skip,
            limit=limit,
            total=total,
            has_previous=skip > 0,
            has_next=skip + limit < total,
        )

    async def create(self, data: CreateTenantDto) -> uuid.UUID | None:
        try:
            payload = {
                "name": data.name,
                "subdomain": data.subdomain,
                "features": [f.model_dump(mode="json") for f in default_enable_features],
            }
            result = await super().create(payload)
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    entity="Tenant",
                    user_id=None,
                    changes={"Info": "Created new tenant with id " + str(result.id)},
                    tenant_id=None,
                )
            )
            return result.id
        except IntegrityError as ex:
            logger.error(f"Error creating tenant: {str(ex)}")
            await self.add_audit_log(
                AuditLogDto(
                    action="create",
                    entity="Tenant",
                    user_id=None,
                    changes={"Info": "Attempted to create duplicate tenant"},
                    tenant_id=None,
                )
            )
            raise ConflictException("Tenant", data.name)
