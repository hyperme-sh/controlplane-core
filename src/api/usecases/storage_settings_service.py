from uuid import UUID
from typing import Optional
from controlplane_api.exceptions import NotFoundException
from controlplane_api.utils import get_logger
from api.domain.dtos.storage_settings_dto import AvailableStorageProviderDto, StorageSettingsDto
from api.infrastructure.persistence.repositories.storage_settings_repository_impl import StorageSettingsRepository
from api.domain.entities.storage_settings import StorageProvider, StorageSettings
from api.domain.storage_scope import storage_tenant

logger = get_logger(__name__)

class StorageSettingsService:
    def __init__(self, storage_settings_repository: StorageSettingsRepository):
        self.storage_settings_repository = storage_settings_repository


    async def configure_storage(
        self, setting: StorageSettingsDto, tenant_id: Optional[UUID] = None
    ) -> UUID:
        return await self.storage_settings_repository.configure_storage(
            setting, storage_tenant(tenant_id)
        )


    async def get_storages(self, tenant_id: UUID | None = None) -> list[AvailableStorageProviderDto]:
        return await self.storage_settings_repository.get_storages(storage_tenant(tenant_id))


    async def reset_storage(self, storage_id: str, updated_by_user_id: str, tenant_id: Optional[UUID] = None) -> None:
        tenant = storage_tenant(tenant_id)
        storage_setting: StorageSettings | None = await self.storage_settings_repository.single_or_none(
            id=UUID(storage_id), tenant_id=tenant
        )
        if storage_setting is None:
            raise NotFoundException("StorageSetting", storage_id)
        
        if storage_setting.tenant_id != tenant:
            raise NotFoundException("StorageSetting", storage_id)
        
        reset_payload: dict = {
            "is_enabled": False,
            "updated_by_user_id": updated_by_user_id,
        }
        if storage_setting.provider == StorageProvider.AWS_S3.value:
            reset_payload.update(
                {
                    "aws_access_key": None,
                    "aws_secret_key": None,
                    "aws_bucket_name": None,
                }
            )
        elif storage_setting.provider == StorageProvider.AZURE_BLOB.value:
            reset_payload.update(
                {
                    "azure_connection_string": None,
                    "azure_container_name": None,
                }
            )

        await self.storage_settings_repository.reset_storage(
            str(storage_setting.id), reset_payload, tenant
        )