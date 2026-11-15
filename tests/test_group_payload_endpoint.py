"""Private group wrap HTTP contracts; executed only in CI."""
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID
import anyio
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from api.interfaces.api_controllers import group_payload_endpoint as endpoint
from api.domain.group_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable,ObjectMetadata
from api.domain.tenant_records import RecordConflict
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects

SET='set_p_'+'a'*128+'_'+'b'*32
DIGEST='d'*64
PATH='/storage/groups/wraps/'+SET+'/'+DIGEST
OWNER='a'*43+'.'+'b'*43
HEADERS={'Content-Type':'application/octet-stream','X-Group-Owner':OWNER}


@pytest.fixture
def service():
    value=SimpleNamespace(put=AsyncMock(return_value=True))
    @asynccontextmanager
    async def opened(**kw):yield BytesIO(b'wrap'),ObjectMetadata(4,DIGEST)
    value.open=opened
    app=FastAPI();app.include_router(endpoint.router)
    app.dependency_overrides[endpoint.group_objects_service]=lambda:value
    return app,value


@pytest.mark.asyncio
async def test_bounded_upload_read_and_error_mapping(service):
    app,store=service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://fixture') as client:
        response=await client.put(PATH,headers=HEADERS,content=b'wrap')
        assert response.status_code==201 and response.headers['cache-control']=='private, no-store'
        assert store.put.call_args.kwargs['object']==SET
        store.put.return_value=False
        assert (await client.put(PATH,headers=HEADERS,content=b'wrap')).status_code==204
        response=await client.get(PATH)
        assert response.status_code==200 and response.content==b'wrap'
        for error,status in [(AllocationRejected(),403),(RecordConflict(),409),(ObjectInputError(),400),(ObjectUnavailable(),503)]:
            store.put.side_effect=error
            assert (await client.put(PATH,headers=HEADERS,content=b'wrap')).status_code==status
            @asynccontextmanager
            async def failed(**kw):raise error;yield
            store.open=failed
            assert (await client.get(PATH)).status_code==status


@pytest.mark.asyncio
async def test_bad_bodies_paths_and_capacity_refuse(service):
    app,store=service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://fixture') as client:
        for headers,body,status in [({},b'x',415),({**HEADERS,'Content-Encoding':'gzip'},b'x',415),
            ({**HEADERS,'X-Group-Owner':'bad'},b'x',400),({**HEADERS,'Content-Length':'0'},b'',411),
            ({**HEADERS,'Content-Length':'65537'},b'x',413)]:
            assert (await client.put(PATH,headers=headers,content=body)).status_code==status
        for path in [PATH.replace('set_p_','media_p_'),PATH[:-1]+'z']:
            assert (await client.get(path)).status_code==400
        store.put.assert_not_awaited()
        tokens=[object() for _ in range(4)]
        for token in tokens:endpoint.capacity.acquire_on_behalf_of_nowait(token)
        try:assert (await client.get(PATH)).status_code==503
        finally:
            for token in tokens:endpoint.capacity.release_on_behalf_of(token)
        @asynccontextmanager
        async def oversized(**kw):yield BytesIO(b'x'*65537),ObjectMetadata(65537,DIGEST)
        store.open=oversized
        assert (await client.get(PATH)).status_code==503


def test_service_requires_private_registry_and_supported_provider():
    tenant=UUID(int=1);app=SimpleNamespace(state=SimpleNamespace(group_allocation_issuers={}))
    request=SimpleNamespace(app=app)
    objects=SimpleNamespace(tenant=tenant,backend=object())
    with pytest.raises(HTTPException):endpoint.group_objects_service(request,objects)
    settings=SimpleNamespace(registry=None)
    app.state.group_allocation_issuers[str(tenant)]=settings
    with pytest.raises(HTTPException):endpoint.group_objects_service(request,objects)
    registry=SimpleNamespace(store=Mock(return_value=SimpleNamespace(schema='storage_owner')))
    settings.registry=registry
    with pytest.raises(HTTPException):endpoint.group_objects_service(request,objects)
    objects.backend=AzureTenantObjects(SimpleNamespace(url='https://fixture.blob.core.windows.net/groups'))
    assert endpoint.group_objects_service(request,objects).store.schema=='storage_owner'
    registry.store.assert_called_with(require_locations=True)
    objects.backend=S3TenantObjects(SimpleNamespace(meta=SimpleNamespace(endpoint_url='https://s3.invalid')),'bucket',region='region',access_key='fixture',secret_key='fixture')
    assert endpoint.group_objects_service(request,objects).store.schema=='storage_owner'


@pytest.mark.asyncio
async def test_assembled_admission_precedes_database_and_provider(monkeypatch):
    from api import storage_app
    seen=[]
    async def database():
        assert endpoint.capacity.borrowed_tokens==1
        seen.append('database');yield
    async def service():
        assert endpoint.capacity.borrowed_tokens==1
        seen.append('provider')
        raise HTTPException(503,'fixture')
    app=storage_app.app
    app.dependency_overrides[storage_app.request_database]=database
    app.dependency_overrides[endpoint.group_objects_service]=service
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='https://fixture') as client:
            assert (await client.get('/api/v1'+PATH)).status_code==503
        assert seen==['database','provider']
        assert endpoint.capacity.borrowed_tokens==0
    finally:app.dependency_overrides.clear()
