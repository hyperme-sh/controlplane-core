"""Pure domain/transport contracts: mock HTTP and conditional memory backend."""
import json
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.domain.experiments.growthbook import GrowthBookSource, import_and_publish
from api.domain.experiments.publication import Publisher
from test_experiment_store import Backend
from test_experiment_control import experiment

TENANT=UUID(int=1)


def source(**changes):
    return GrowthBookSource(**(dict(origin='https://growthbook.invalid',organization='org_test',project='prj_test',
        environment='test',connection='sdk_test',api_token='readonly-api-token',sdk_key='sdk-isolated-key',
        tenant=TENANT,audience='s3r.ch',maximum_stale_seconds=60) | changes))


def responses():
    return {'sdkConnection':dict(id='sdk_test',organization='org_test',project='prj_test',projects=['prj_test'],
        environment='test',key='sdk-isolated-key',encryptPayload=False)}, {
        'status':200,'features':{'controlplane-policy':{'defaultValue':{
            'version':1,'tenant':str(TENANT),'audience':'s3r.ch',
            'experiments':[experiment().model_dump(mode='json')]}}}}


def transport(meta,policy,status=200):
    def call(request):
        assert request.url.host=='growthbook.invalid'
        if request.url.path.startswith('/api/v1/'):
            assert request.headers['authorization']=='Bearer readonly-api-token'
            return httpx.Response(status,json=meta)
        assert request.url.path=='/api/features/sdk-isolated-key'
        assert 'authorization' not in request.headers
        return httpx.Response(status,json=policy)
    return httpx.MockTransport(call)


@pytest.mark.asyncio
async def test_import_is_scoped_idempotent_and_renewal_is_bounded():
    meta,policy=responses();backend=Backend();p=Publisher(TENANT,'s3r.ch',Ed25519PrivateKey.generate(),backend)
    async with httpx.AsyncClient(transport=transport(meta,policy)) as client:
        assert await import_and_publish(source(),client,p,1000)
        assert not await import_and_publish(source(),client,p,1001)
        assert await import_and_publish(source(),client,p,1030)
    config=await p.store.load(TENANT,'s3r.ch');assert config.sequence==1
    cache=json.loads((await p.records.get('cache',p.cache_name)).data)
    public=json.loads(cache['payload']);assert public['expiresAt']==1090
    assert 'tenant' not in public and 'metrics' not in public['experiments'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('organization','other'),('projects',['other']),('projects',['prj_test','other']),
    ('environment','production'),('key','foreign-key-value'),('encryptPayload',True),('includeDraftExperiments',True),('proxyEnabled',True)])
async def test_connection_mismatch_writes_nothing(field,value):
    meta,policy=responses();meta['sdkConnection'][field]=value;backend=Backend()
    async with httpx.AsyncClient(transport=transport(meta,policy)) as client:
        with pytest.raises(ValueError):await import_and_publish(source(),client,Publisher(TENANT,'s3r.ch',Ed25519PrivateKey.generate(),backend),1000)
    assert not backend.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[301,401,403,404,429,500])
async def test_refresh_failure_never_renews_cached_payload(status):
    meta,policy=responses();backend=Backend();p=Publisher(TENANT,'s3r.ch',Ed25519PrivateKey.generate(),backend)
    async with httpx.AsyncClient(transport=transport(meta,policy)) as client:await import_and_publish(source(),client,p,1000)
    before=dict(backend.rows)
    async with httpx.AsyncClient(transport=transport(meta,policy,status)) as client:
        with pytest.raises(ValueError):await import_and_publish(source(),client,p,1061)
    assert backend.rows==before


@pytest.mark.asyncio
async def test_import_preserves_controller_disable_and_requires_fresh_scope():
    meta,policy=responses();backend=Backend();p=Publisher(TENANT,'s3r.ch',Ed25519PrivateKey.generate(),backend)
    async with httpx.AsyncClient(transport=transport(meta,policy)) as client:
        await import_and_publish(source(),client,p,1000)
        previous=await p.store.load(TENANT,'s3r.ch')
        disabled=previous.model_copy(update={'sequence':2,'experiments':(previous.experiments[0].model_copy(update={'disabled':True}),)})
        assert await p.store.replace(previous,disabled)
        await import_and_publish(source(),client,p,1030)
        assert (await p.store.load(TENANT,'s3r.ch')).experiments[0].disabled
        assert (await p.store.load(TENANT,'s3r.ch')).sequence==2


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['tenant','audience','private','rules','extra-feature'])
async def test_policy_refuses_foreign_scope_targeting_and_private_fields(mutation):
    meta,policy=responses();feature=policy['features']['controlplane-policy'];value=feature['defaultValue']
    if mutation=='tenant':value['tenant']=str(UUID(int=2))
    if mutation=='audience':value['audience']='portal'
    if mutation=='private':value['experiments'][0]['identity']='private'
    if mutation=='rules':feature['rules']=[{'force':{}}]
    if mutation=='extra-feature':policy['features']['other']={}
    async with httpx.AsyncClient(transport=transport(meta,policy)) as client:
        with pytest.raises(ValueError):await source().read(client)


@pytest.mark.parametrize('changes',[{'origin':'http://growthbook.invalid'},{'origin':'https://user:secret@growthbook.invalid'},
    {'origin':'https://growthbook.invalid/path'},{'api_token':'sdk-isolated-key'},{'maximum_stale_seconds':301},
    {'maximum_stale_seconds':True},{'tenant':UUID(int=0)}])
def test_binding_rejects_unsafe_configuration(changes):
    with pytest.raises(ValueError):source(**changes)

@pytest.mark.asyncio
@pytest.mark.parametrize('ttl',[0,301,True,-1])
async def test_publisher_refuses_invalid_stale_limits_before_writes(ttl):
    backend=Backend();p=Publisher(TENANT,'s3r.ch',Ed25519PrivateKey.generate(),backend)
    with pytest.raises(ValueError):await p.publish(1000,ttl=ttl)
    assert not backend.rows


def test_private_credential_file_refuses_aliases_modes_and_oversize(tmp_path):
    from api.growthbook_publisher import read_secret
    value=tmp_path/'credential';value.write_text('readonly-api-token');value.chmod(0o600)
    assert read_secret(value)=='readonly-api-token'
    value.chmod(0o644)
    with pytest.raises(ValueError):read_secret(value)
    value.chmod(0o600);alias=tmp_path/'alias';alias.symlink_to(value)
    with pytest.raises(OSError):read_secret(alias)
    value.write_text('x'*513)
    with pytest.raises(ValueError):read_secret(value)


@pytest.mark.asyncio
async def test_worker_uses_import_source_instead_of_static_renewal(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock,Mock
    from api import experiment_publisher as worker
    from api.domain.experiments import growthbook
    session=Mock(commit=AsyncMock(),rollback=AsyncMock())
    @asynccontextmanager
    async def maker():yield session
    @asynccontextmanager
    async def provider(tenant):yield worker.AzureTenantObjects(object())
    monkeypatch.setattr(worker,'set_db_session',Mock(return_value='context'))
    reset=Mock();monkeypatch.setattr(worker,'reset_db_session',reset)
    monkeypatch.setattr(worker,'set_current_tenant_id_guc',AsyncMock())
    monkeypatch.setattr(worker,'tenant_backend',provider)
    monkeypatch.setattr(worker,'AzureTenantRecords',lambda _:Backend())
    imported=AsyncMock(side_effect=ValueError('source unavailable'))
    monkeypatch.setattr(growthbook,'import_and_publish',imported)
    direct=AsyncMock();monkeypatch.setattr(worker.Publisher,'publish',direct)
    with pytest.raises(ValueError):await worker.tick(maker,TENANT,'s3r.ch',Ed25519PrivateKey.generate(),1000,source=source())
    imported.assert_awaited_once();direct.assert_not_awaited()
    session.rollback.assert_awaited_once();session.commit.assert_not_awaited();reset.assert_called_once_with('context')
