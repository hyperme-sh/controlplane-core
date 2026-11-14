"""Issuer HTTP and private configuration contracts, executed in CI."""
import base64
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from api.infrastructure.security.media_allocation_settings import load_issuers
from api.interfaces.api_controllers import media_allocation_endpoint as endpoint
from api.domain.tenant_records import RecordConflict
from test_media_allocation_issuer import issuer
from test_media_allocation_grant import grant, b64


def configuration(scope,key,source_key):
    return {'version':1,'bindings':[{'tenant':scope.tenant,'issuer':scope.issuer,'source':scope.source,
        'policyDigest':scope.policy_digest,'policyUntil':2000,'keyId':'storage',
        'signingKey':b64(key.private_bytes_raw()),'requestKeys':{'source':b64(source_key.public_key().public_bytes_raw())}}]}


def test_private_configuration_absence_permissions_and_scope(tmp_path,issuer,grant):
    scope,_,_,_,key,*_=issuer
    assert load_issuers({})=={}
    path=tmp_path/'issuer.json';value=configuration(scope,key,grant[1])
    path.write_text(json.dumps(value));path.chmod(0o600)
    env={'STORAGE_MEDIA_ALLOCATION_FILE':str(path)}
    assert list(load_issuers(env))==[scope.tenant]
    path.chmod(0o644)
    with pytest.raises(ValueError,match='Invalid private'):load_issuers(env)
    path.chmod(0o600)
    for invalid in [{}, {**value,'version':True}, {**value,'bindings':[]},
                    {**value,'bindings':value['bindings']*2},
                    {**value,'bindings':[{**value['bindings'][0],'requestKeys':[]}]},
                    {**value,'bindings':[{**value['bindings'][0],'extra':True}]}]:
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError,match='Invalid private'):load_issuers(env)


@pytest.mark.asyncio
async def test_http_requires_source_proof_and_reconciles_original_grant(issuer):
    scope,backend,store,_,_,configured,request,_=issuer
    app=FastAPI();app.include_router(endpoint.router)
    app.state.media_allocation_issuers={scope.tenant:SimpleNamespace(bind=lambda backend:configured())}
    app.dependency_overrides[endpoint.records_store]=lambda:SimpleNamespace(tenant=UUID(scope.tenant),backend=backend)
    body={'object':scope.object,'owner':scope.owner,'ownershipDigest':scope.ownership_digest,
          'request':base64.b64encode(request()).decode()}
    await store.initialize()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://storage.example') as client:
        first=await client.post('/storage/allocations/issue',json=body)
        assert first.status_code==200 and first.headers['cache-control']=='private, no-store'
        assert (await client.post('/storage/allocations/issue',json=body)).content==first.content
        assert backend.writes==2
        assert (await client.post('/storage/allocations/issue',json={**body,'ownershipDigest':'f'*64})).status_code==403
        for bad in [{},[],{**body,'request':'!!!'},{**body,'owner':True},{**body,'request':''}]:
            assert (await client.post('/storage/allocations/issue',json=bad)).status_code==400
        assert (await client.post('/storage/allocations/issue',content=b'{}')).status_code==415
        assert (await client.post('/storage/allocations/issue',content=b'x'*8193,headers={'content-type':'application/json'})).status_code==413
        app.state.media_allocation_issuers={}
        assert (await client.post('/storage/allocations/issue',json=body)).status_code==503


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status',[(RecordConflict(),409),(TimeoutError(),503)])
async def test_uncertain_provider_errors_do_not_implicitly_retry(issuer,error,status):
    scope,backend,store,_,_,configured,request,_=issuer
    app=FastAPI();app.include_router(endpoint.router)
    app.state.media_allocation_issuers={scope.tenant:SimpleNamespace(bind=lambda backend:configured())}
    app.dependency_overrides[endpoint.records_store]=lambda:SimpleNamespace(tenant=UUID(scope.tenant),backend=backend)
    async def fail(key):raise error
    backend.fetch=fail
    body={'object':scope.object,'owner':scope.owner,'ownershipDigest':scope.ownership_digest,
          'request':base64.b64encode(request()).decode()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://storage.example') as client:
        assert (await client.post('/storage/allocations/issue',json=body)).status_code==status
    assert backend.writes==0
