from api.infrastructure.messaging.transactional_outbox import publish_new_tenant
from typing import Any, List
from uuid import UUID

from controlplane_api.event_types import TENANT_CREATED_V1
from controlplane_api.utils import get_logger, validate_password
from api.infrastructure.messaging.tenant_event_publisher import (
    get_tenant_event_publisher,
)
from api.core.exceptions import TenantNotFoundException
from api.domain.dtos.tenant_dto import CreateTenantDto, FeatureDto, TenantListDto, UpdateTenantDto
from api.domain.entities.tenant import Tenant
from api.domain.enum.feature import Feature as FeatureEnum
from api.domain.serialization import parse_uuid
from api.infrastructure.persistence.repositories.tenant_repository_impl import TenantRepository

logger = get_logger(__name__)

class TenantService:
    def __init__(self, tenant_repository: TenantRepository):
        self.tenant_repository = tenant_repository
        logger.info("Initialized.")


    async def list_tenants(self, skip: int = 0, limit: int = 10) -> TenantListDto:
        """List tenants with pagination."""
        return await self.tenant_repository.list(skip=skip, limit=limit)

    async def find_by_name(self, name: str) -> Tenant | None:
        """Get tenant by name. Raises TenantNotFoundException if not found."""
        exisiting = await self.tenant_repository.single_or_none(name=name)
        if exisiting is None:
            raise TenantNotFoundException(name)
        return exisiting

    async def find_by_custom_domain(self, custom_domain: str) -> Tenant | None:
        """Get tenant by custom domain. Raises TenantNotFoundException if not found."""
        existing = await self.tenant_repository.single_or_none(custom_domain=custom_domain)
        if existing is None:
            raise TenantNotFoundException(custom_domain)
        return existing

    async def find_by_subdomain(self, subdomain: str) -> Tenant | None:
        """Get tenant by subdomain. Raises TenantNotFoundException if not found."""
        existing = await self.tenant_repository.single_or_none(subdomain=subdomain)
        if existing is None:
            raise TenantNotFoundException(subdomain)
        return existing

    async def get_tenant_by_id(self, tenant_id: str) -> Tenant:
        """Get tenant by ID. Raises TenantNotFoundException if not found."""
        existing = await self.tenant_repository.get(tenant_id)
        if existing is None:
            raise TenantNotFoundException(tenant_id)
        return existing

    async def create_tenant(self, tenant_data: CreateTenantDto) -> UUID | None:
        """Create a new tenant. Raises InvalidOperationException if admin password is weak."""
        if tenant_data.admin_password:
            validate_password(tenant_data.admin_password)
        async with self.tenant_repository.session.begin_nested():
            response = await self.tenant_repository.create(tenant_data)
            if response is not None:
                publisher = get_tenant_event_publisher()
                await self.tenant_repository.session.run_sync(lambda session: publish_new_tenant(
                    session, publisher, tenant_id=response, event_type=TENANT_CREATED_V1,
                    correlation_id=str(response), payload={
                        "tenant_id": str(response), "name": tenant_data.name,
                        "subdomain": str(tenant_data.subdomain),
                    }))
        return response


    async def delete_tenant(self, tenant_id: str) -> None:
        """Delete tenant by ID. Raises TenantNotFoundException if not found."""
        if not await self.tenant_repository.delete(tenant_id):
            raise TenantNotFoundException(tenant_id)

    async def total_count(self) -> int:
        """Get total count of tenants. Returns an integer."""
        return await self.tenant_repository.count()

    def _features_as_dicts(self, raw: list | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in raw or []:
            if isinstance(item, dict):
                out.append(
                    {
                        "name": str(item["name"]),
                        "enabled": bool(item.get("enabled", False)),
                    }
                )
            else:
                nm = item.name.value if hasattr(item.name, "value") else str(item.name)
                out.append({"name": nm, "enabled": bool(item.enabled)})
        return out

    async def update_feature(self, tenant_id: str, feature: FeatureDto):
        """
            Update a feature for a tenant. If the feature does not exist, it will be added to the tenant's features.
            Raises TenantNotFoundException if tenant not found.
        """
        tenant = await self.get_tenant_by_id(tenant_id)
        feats = self._features_as_dicts(tenant.features)
        name_val = (
            feature.name.value
            if hasattr(feature.name, "value")
            else str(feature.name)
        )
        found = False
        for i, ef in enumerate(feats):
            if ef["name"] == name_val:
                feats[i] = {"name": name_val, "enabled": feature.enabled}
                found = True
                break
        if not found:
            feats.append({"name": name_val, "enabled": feature.enabled})
        updated = await self.tenant_repository.update(tenant_id, {"features": feats})
        if updated is None:
            raise TenantNotFoundException(tenant_id)
        logger.debug(
            f"Feature '{feature.name}' updated to '{feature.enabled}' for tenant '{tenant_id}'"
        )

    async def update_tenant(self, tenant_id: str, data: UpdateTenantDto) -> None:
        """Update tenant details. Raises TenantNotFoundException if not found."""
        await self.get_tenant_by_id(tenant_id)
        dump = data.model_dump(exclude_unset=True, exclude_none=True)
        if "subscription_id" in dump and dump["subscription_id"] is not None:
            dump["subscription_id"] = parse_uuid(dump["subscription_id"])
        updated = await self.tenant_repository.update(tenant_id, dump)
        if updated is None:
            raise TenantNotFoundException(tenant_id)

    async def update_tenant_fields(self, tenant_id: str, fields: dict[str, Any]) -> None:
        await self.get_tenant_by_id(tenant_id)
        payload = dict(fields)
        if "subscription_id" in payload and payload["subscription_id"] is not None:
            payload["subscription_id"] = parse_uuid(payload["subscription_id"])
        updated = await self.tenant_repository.update(tenant_id, payload)
        if updated is None:
            raise TenantNotFoundException(tenant_id)

    async def get_features_by_tenant_id(self, tenant_id: str) -> List[FeatureDto]:
        """
            Get features for a tenant by ID.
            Raises 
                TenantNotFoundException if tenant not found.
        """
        tenant = await self.get_tenant_by_id(tenant_id)
        all_features = [FeatureDto(name=name, enabled=False) for name in FeatureEnum]
        raw = tenant.features or []
        if len(raw) == 0:
            return all_features

        tenant_features: List[FeatureDto] = []
        for item in raw:
            if isinstance(item, dict):
                tenant_features.append(
                    FeatureDto(
                        name=FeatureEnum(item["name"]),
                        enabled=bool(item.get("enabled", False)),
                    )
                )
            else:
                tenant_features.append(
                    FeatureDto(name=item.name, enabled=item.enabled)
                )
        logger.debug(f"Tenant features for tenant {tenant_id}: {tenant_features}")
        
        # Check if any features are added in the enum that are not present in tenant features
        if len(all_features) > len(tenant_features):
            logger.debug(f"Some features are not set for tenant {tenant_id}, adding missing features as disabled.")
            available_features: List[FeatureDto] = []
            for all_feature in all_features:
                for tenant_feature in tenant_features:
                    if all_feature.name == tenant_feature.name:
                        # Feature exists in tenant, add it as is
                        available_features.append(tenant_feature)
                        break
                else:
                    # Feature not found in tenant, add it as enabled=False by default
                    available_features.append(FeatureDto(name=all_feature.name, enabled=False))
            return available_features
        
        # Return tenant features if all features are already set
        return tenant_features