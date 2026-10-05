from typing import List

from typing_extensions import get_args

from controlplane_api.exceptions import NotFoundException
from controlplane_api.utils import get_logger, get_sso_redirect_uri
from api.domain.dtos.sso_settings_dto import (
    CreateSSOSettingsDto,
    ReadSSOSettingsDto,
    SSOSettingsListDto,
    UpdateSSOSettingsDto,
)
from api.domain.entities.sso_settings import SSOProvider, SSOSettings
from api.domain.serialization import parse_uuid
from api.infrastructure.persistence.repositories.sso_settings_provider_respository_impl import (
    SSOSettingsProviderRepository,
)
from api.usecases.tenant_service import TenantService

logger = get_logger(__name__)


def _sso_row_to_read(row: SSOSettings) -> ReadSSOSettingsDto:
    return ReadSSOSettingsDto(
        id=str(row.id),
        enabled=row.enabled,
        provider=row.provider,  # type: ignore[arg-type]
        client_id=row.client_id or "",
        client_secret=row.client_secret,
        scopes=list(row.scopes or []) if row.scopes else [],
        redirect_uris=list(row.redirect_uris or []) if row.redirect_uris else [],
    )


class SSOSettingsService:
    def __init__(
        self,
        sso_settings_provider_repository: SSOSettingsProviderRepository,
        tenant_service: TenantService,
    ):
        self.sso_settings_provider_repository: SSOSettingsProviderRepository = (
            sso_settings_provider_repository
        )
        self.tenant_service: TenantService = tenant_service

    async def get_sso_settings_by_id(self, row_id: str) -> ReadSSOSettingsDto:
        res = await self.sso_settings_provider_repository.get_by_id(row_id)
        if res is None:
            raise NotFoundException("SSOSettings", row_id)
        return _sso_row_to_read(res)

    async def update_redirect_uri(self, row_id: str, redirect_uri: str) -> None:
        await self.sso_settings_provider_repository.update_sso_settings(
            row_id, {"redirect_uris": [redirect_uri]}
        )

    async def update_sso_settings(
        self, row_id: str, sso_settings: UpdateSSOSettingsDto
    ) -> None:
        dump = sso_settings.model_dump(exclude_unset=True, exclude_none=True)
        if "provider" in dump and dump["provider"] is not None:
            dump["provider"] = str(dump["provider"])
        await self.sso_settings_provider_repository.update_sso_settings(row_id, dump)

    async def create_sso_settings(self, sso_settings: CreateSSOSettingsDto) -> str:
        redirect_uri = get_sso_redirect_uri(sso_settings.provider)
        if sso_settings.tenant_id:
            tenant = await self.tenant_service.get_tenant_by_id(
                tenant_id=sso_settings.tenant_id
            )
            redirect_uri = get_sso_redirect_uri(
                sso_settings.provider,
                tenant.custom_domain if tenant.custom_domain else tenant.subdomain,
            )

        sso_settings.redirect_uris = [redirect_uri]
        payload = sso_settings.model_dump()
        payload["tenant_id"] = (
            parse_uuid(payload["tenant_id"]) if payload.get("tenant_id") else None
        )
        payload["provider"] = str(payload["provider"])
        row = await self.sso_settings_provider_repository.create_sso_settings(payload)
        return str(row.id)

    async def delete_sso_settings(self, row_id: str) -> bool:
        return await self.sso_settings_provider_repository.delete_sso_settings(row_id)

    async def list(self) -> SSOSettingsListDto:
        ssos: List[SSOSettings] = (
            await self.sso_settings_provider_repository.list_sso_settings()
        )
        logger.info("Retrieved %s SSO settings from repository", len(ssos))
        items = [_sso_row_to_read(sso) for sso in ssos]
        return SSOSettingsListDto(items=items)

    async def get_provider(self, provider_name: SSOProvider) -> SSOSettings:
        ssos: List[SSOSettings] = (
            await self.sso_settings_provider_repository.list_sso_settings()
        )
        for sso in ssos:
            if sso.provider == provider_name:
                return sso
        raise ValueError(
            f"SSO provider '{provider_name}' not found or not enabled."
        )

    async def get_only_enabled_providers(self) -> SSOSettingsListDto:
        ssos: List[SSOSettings] = (
            await self.sso_settings_provider_repository.list_sso_settings()
        )
        enabled_ssos = [sso for sso in ssos if sso.enabled]
        items = [_sso_row_to_read(sso) for sso in enabled_ssos]
        return SSOSettingsListDto(items=items)

    async def get_available_sso_providers(self) -> List[str]:
        return list(get_args(SSOProvider))
