from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response, status
from api.core.container import get_storage_settings_service
from api.domain.dtos.storage_settings_dto import AvailableStorageProviderDto, StorageSettingsDto
from api.domain.entities.storage_settings import StorageProvider
from controlplane_api.permissions import Permission
from api.infrastructure.security.current_user import CurrentUser
from api.interfaces.security.role_checker import check_permissions_for_current_role
from api.usecases.storage_settings_service import StorageSettingsService
from api.domain.storage_scope import storage_tenant



router = APIRouter(
    prefix="/storage",
    dependencies=[
        Depends(check_permissions_for_current_role(required_permissions=[Permission.MANAGE_STORAGE_SETTINGS]))
    ]
)
router.tags = ["Storage Settings"]

@router.get("/", response_model=List[AvailableStorageProviderDto], response_model_exclude_none=True)
async def get_storage_settings(
    current_user: CurrentUser,
    setting_service: StorageSettingsService = Depends(get_storage_settings_service)
):
    res = await setting_service.get_storages(_tenant(current_user.tenant_id))
    return [s.model_dump(exclude_none=True) for s in res]

@router.get("/available", response_model=List[dict[str, str]])
async def get_available_providers():
    providers = [{"name": p.value} for p in StorageProvider]
    return providers

@router.post("/configure", status_code=status.HTTP_201_CREATED)
async def configure_storage(
    configuration: StorageSettingsDto,
    current_user: CurrentUser,
    setting_service: StorageSettingsService = Depends(get_storage_settings_service)
):

    tenant = _tenant(current_user.tenant_id)
    configuration.updated_by_user_id = current_user.id
    await setting_service.configure_storage(setting=configuration, tenant_id=tenant)
    return status.HTTP_201_CREATED


@router.patch("/{storage_id}/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_storage(
    storage_id: UUID,
    current_user: CurrentUser,
    setting_service: StorageSettingsService = Depends(get_storage_settings_service)
):
    await setting_service.reset_storage(storage_id=str(storage_id), updated_by_user_id=current_user.id, tenant_id=_tenant(current_user.tenant_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _tenant(value: str | None) -> UUID:
    try:
        return storage_tenant(value)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Tenant storage scope required") from exc
