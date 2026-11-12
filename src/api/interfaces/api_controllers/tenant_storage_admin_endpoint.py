"""Tenant configuration through current network-owner authority."""
from typing import Annotated
import base64
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from api.core.container import get_storage_settings_service
from api.domain.dtos.storage_settings_dto import AvailableStorageProviderDto, StorageSettingsDto
from api.infrastructure.security.storage_admin import StorageAdmin, storage_admin
from api.usecases.storage_settings_service import StorageSettingsService

router = APIRouter(prefix='/storage/tenant', tags=['Tenant storage configuration'])
Admin = Annotated[StorageAdmin, Depends(storage_admin)]
Service = Annotated[StorageSettingsService, Depends(get_storage_settings_service)]


def validate_provider(configuration: StorageSettingsDto) -> None:
    # Network administrators can configure only public-cloud storage endpoints,
    # never use provider configuration to send requests to internal services.
    if configuration.provider == 'azure_blob':
        connection = configuration.azure_connection_string
        container = configuration.azure_container_name
        try:
            if not isinstance(connection, str) or len(connection) > 4096:
                raise ValueError()
            parts = [part.split('=', 1) for part in connection.rstrip(';').split(';')]
            if any(len(part) != 2 for part in parts) or len({part[0] for part in parts}) != len(parts):
                raise ValueError()
            fields = dict(parts)
            if (set(fields) != {'DefaultEndpointsProtocol','AccountName','AccountKey','EndpointSuffix'}
                    or fields['DefaultEndpointsProtocol'] != 'https' or fields['EndpointSuffix'] != 'core.windows.net'
                    or not re.fullmatch('[a-z0-9]{3,24}', fields['AccountName'])
                    or len(base64.b64decode(fields['AccountKey'], validate=True)) != 64
                    or not isinstance(container, str) or not re.fullmatch('[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]', container)):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(400, 'Explicit public Azure account and private container configuration required') from None
    else:
        if (not re.fullmatch('[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+', configuration.region)
                or not isinstance(configuration.aws_bucket_name, str)
                or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', configuration.aws_bucket_name)
                or not configuration.aws_access_key or not configuration.aws_secret_key):
            raise HTTPException(400, 'Explicit AWS account, region and bucket configuration required')


@router.get('', response_model=list[AvailableStorageProviderDto], response_model_exclude_none=True)
async def list_settings(admin: Admin, service: Service, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await service.get_storages(admin.tenant)


@router.post('/configure', status_code=201)
async def configure(configuration: StorageSettingsDto, admin: Admin, service: Service):
    validate_provider(configuration)
    # The caller cannot select its tenant or the audit actor via the body.
    configuration.updated_by_user_id = admin.subject
    await service.configure_storage(configuration, admin.tenant)
    return Response(status_code=201, headers={'Cache-Control': 'no-store'})


@router.patch('/{storage_id}/reset', status_code=204)
async def reset(storage_id: UUID, admin: Admin, service: Service):
    await service.reset_storage(str(storage_id), admin.subject, admin.tenant)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


# Experiment configuration shares the existing verified tenant-admin boundary.
from api.interfaces.api_controllers.experiment_admin_endpoint import router as experiments_router
router.include_router(experiments_router, prefix="/experiments")
