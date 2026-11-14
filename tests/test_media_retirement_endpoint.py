"""Retirement HTTP is bounded, source-authenticated and explicit about errors."""
import base64
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from starlette.requests import Request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.interfaces.api_controllers import media_retirement_endpoint as endpoint
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects


@pytest.mark.asyncio
async def test_bounded_http_and_exact_command_no_automatic_retry():
    app=FastAPI();app.include_router(endpoint.router)
    result={'accepted':True,'command':'a'*64,'cleanup':'complete'}
    service=SimpleNamespace(retire=AsyncMock(return_value=result))
    app.dependency_overrides[endpoint.retirement_service]=lambda:service
    body={'object':'media','owner':'owner','command':'a'*64,'request':base64.b64encode(b'signed source command').decode()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://storage.example') as client:
        response=await client.post('/storage/media/retire',json=body)
        assert response.status_code==200 and response.json()==result
        assert response.headers['cache-control']=='private, no-store'
        assert service.retire.call_args.args==(b'signed source command',)
        assert service.retire.call_args.kwargs['command']=='a'*64
        for bad in [{},[],{**body,'request':'!!!'},{**body,'request':''},{**body,'owner':True},{**body,'digests':[]}]:
            assert (await client.post('/storage/media/retire',json=bad)).status_code==400
        assert service.retire.await_count==1
        assert (await client.post('/storage/media/retire',content=b'{}')).status_code==415
        assert (await client.post('/storage/media/retire',json=body,headers={'content-encoding':'gzip'})).status_code==415
        assert (await client.post('/storage/media/retire',content=b'x'*8193,headers={'content-type':'application/json'})).status_code==413
        for error,status in [(AllocationRejected(),403),(RecordConflict(),409),(ObjectInputError(),400),(ObjectUnavailable(),503),(TimeoutError(),503)]:
            service.retire.side_effect=error
            response=await client.post('/storage/media/retire',json=body)
            assert response.status_code==status and response.headers['cache-control']=='private, no-store'
        assert service.retire.await_count==6


def test_service_refuses_absent_registry_and_only_wraps_configured_provider():
    tenant=UUID(int=1);app=FastAPI();request=Request({'type':'http','app':app})
    objects=SimpleNamespace(tenant=tenant,backend=AzureTenantObjects(SimpleNamespace()))
    with pytest.raises(HTTPException):endpoint.retirement_service(request,objects)
    settings=SimpleNamespace(registry=None);app.state.media_allocation_issuers={str(tenant):settings}
    with pytest.raises(HTTPException):endpoint.retirement_service(request,objects)
    store=SimpleNamespace(schema='storage_owner',tenant=str(tenant),issuer=str(UUID(int=2)))
    settings.registry=SimpleNamespace(store=lambda:store);settings.source=str(UUID(int=3))
    settings.request_keys={'source':Ed25519PrivateKey.generate().public_key()}
    service=endpoint.retirement_service(request,objects)
    assert service.authority.store is service.objects.store is store
    assert service.objects.backend.container is objects.backend.container
    objects.backend=S3TenantObjects(SimpleNamespace(),'bucket',region='region',access_key='fixture',secret_key='fixture')
    assert endpoint.retirement_service(request,objects).objects.backend.bucket=='bucket'
    objects.backend=object()
    with pytest.raises(HTTPException):endpoint.retirement_service(request,objects)
