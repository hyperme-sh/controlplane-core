from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr

from api.common.enums.gender import Gender
from api.domain.entities.tenant import CustomDomain, Subdomain, Tenant
from api.domain.enum.feature import Feature as FeatureEnum
from api.domain.serialization import serialize_tenant

class FeatureDto(BaseModel):
    name: FeatureEnum
    enabled: bool

class TenantDto(BaseModel):
    id: Optional[str] = None
    name: str
    subdomain: Optional[str] | None = None
    is_active: bool = False
    custom_domain: Optional[str] | None = None
    custom_domain_status: Literal["active", "failed", "activation-progress"] = "failed"
    features: List[FeatureDto] = []
    subscription_id: Optional[str] | None = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CreateTenantDto(BaseModel):
    name: str
    subdomain: Subdomain
    admin_email: EmailStr
    admin_password: str | None = None
    first_name: str
    last_name: str
    gender: Gender

class TenantListDto(BaseModel):
    tenants: List[TenantDto]
    skip: int
    limit: int
    total: int
    has_previous: bool
    has_next: bool


class CreateTenantResponseDto(BaseModel):
    id: str


class SubdomainAvailabilityDto(BaseModel):
    is_available: bool

class UpdateTenantDto(BaseModel):
    is_active: Optional[bool] | None = None
    custom_domain: Optional[CustomDomain] | None = None
    subscription_id: Optional[str] | None = None
    custom_domain_status: Optional[
        Literal["active", "failed", "activation-progress"]
    ] = None


class UpdateTenantResponseDto(BaseModel):
   message: str


def tenant_from_row(row: Tenant) -> TenantDto:
    s = serialize_tenant(row)
    feats_raw = s.get("features") or []
    feats: List[FeatureDto] = []
    for f in feats_raw:
        if isinstance(f, dict) and "name" in f:
            try:
                feats.append(
                    FeatureDto(
                        name=FeatureEnum(f["name"]),
                        enabled=bool(f.get("enabled", False)),
                    )
                )
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

