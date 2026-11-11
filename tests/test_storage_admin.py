"""Storage administration unit contracts with signed tokens and owner doubles."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException, Request, Response

from api.infrastructure.security import storage_admin as security
from api.infrastructure.security import tenant_session_jwt as tokens
from api.interfaces.api_controllers import tenant_storage_admin_endpoint as endpoint
from api.domain.dtos.storage_settings_dto import StorageSettingsDto


@pytest.fixture
def setup(monkeypatch):
    secret = 'unit-storage-admin-' + 'a' * 48
    original_settings = tokens.settings
    class Settings:
        def usable_api_key_exchange_secret(self):
            return secret
        def __getattr__(self, key):
            return getattr(original_settings, key)
    monkeypatch.setattr(tokens, 'settings', Settings())
    tenant, subject = str(uuid4()), str(uuid4())
    now = int(time.time())
    claims = dict(iss=tokens.ISSUER, spanopticon_auth=tokens.SPANOPTICON_AUTH, sub=subject,
        tenant_id=tenant, iat=now, exp=now+300, scope_version=1, scope_kind='network',
        network_id='s3r.ch', organization_id=None, scope_generation='unit-generation', scope_manifest_sha256='b'*64)
    def request(changes=None, header_tenant=tenant, signing_secret=secret):
        token = jwt.encode({**claims, **(changes or {})}, signing_secret, algorithm='HS256')
        return Request({'type':'http','method':'POST','path':'/','headers':[
            (b'authorization', ('Bearer '+token).encode()), (b'x-tenant-id', header_tenant.encode())]})
    monkeypatch.setenv('USER_SERVICE_URL', 'https://user.owner.example')
    requests = []
    response = {'status':200, 'headers':{'cache-control':'private, no-store'}, 'allowed':True}
    async def send(request):
        requests.append(request)
        data = json.loads(await request.aread())
        result = {**data, 'subject':subject, 'allowed':response['allowed'], **response.get('changes',{})}
        return httpx.Response(response['status'], headers=response['headers'], content=response.get('raw',json.dumps(result).encode()))
    original = httpx.AsyncClient
    monkeypatch.setattr(security.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(send), **kwargs))
    return SimpleNamespace(tenant=tenant,subject=subject,request=request,requests=requests,response=response)


@pytest.mark.asyncio
async def test_current_owner_checked_each_time_and_signed_tenant_is_fixed(setup):
    h=setup
    admin=await security.storage_admin(h.request())
    assert str(admin.tenant)==h.tenant and admin.subject==h.subject
    assert str(h.requests[0].url)=='https://user.owner.example/api/v1/authority/check'
    assert h.requests[0].headers['x-tenant-id']==h.tenant
    sent=json.loads(h.requests[0].content)
    assert sent['action']=='network.admin' and sent['network']=='s3r.ch'
    h.response['allowed']=False
    with pytest.raises(HTTPException) as exc:
        await security.storage_admin(h.request())
    assert exc.value.status_code==403 and len(h.requests)==2


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[
    {'iss':'https://attacker.example'}, {'scope_version':True}, {'scope_version':None},
    {'scope_kind':'organization','organization_id':str(uuid4())}, {'scope_kind':'network','organization_id':str(uuid4())},
    {'smoke_session':True}, {'sub':'apikey:'+str(uuid4())}, {'tenant_id':str(uuid4())},
    {'iat':int(time.time())+100}, {'exp':int(time.time())-1}, {'exp':int(time.time())+1200},
    {'scope_generation':''}, {'scope_manifest_sha256':'nope'}, {'network_id':''},
])
async def test_invalid_or_wrong_purpose_tokens_never_reach_owner(setup,changes):
    with pytest.raises(HTTPException):
        await security.storage_admin(setup.request(changes))
    assert not setup.requests


@pytest.mark.asyncio
async def test_bad_signature_and_duplicate_headers_are_rejected(setup):
    h=setup
    with pytest.raises(HTTPException):
        await security.storage_admin(h.request(signing_secret='wrong'*12))
    for name in (b'authorization',b'x-tenant-id'):
        request=h.request()
        request.scope['headers'].append(next(item for item in request.scope['headers'] if item[0]==name))
        with pytest.raises(HTTPException):
            await security.storage_admin(request)
    assert not h.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,status',[
    ({'status':403},403), ({'status':401},403), ({'status':500},503), ({'status':302},503),
    ({'headers':{}},503), ({'raw':b'not-json'},503), ({'raw':b'x'*17000},503),
    ({'changes':{'subject':str(uuid4())}},503), ({'changes':{'network':'other.example'}},503),
    ({'changes':{'request_id':str(uuid4())}},503), ({'allowed':1},403),
])
async def test_owner_errors_or_mismatched_decisions_fail_closed(setup,changes,status):
    setup.response.update(changes)
    with pytest.raises(HTTPException) as exc:
        await security.storage_admin(setup.request())
    assert exc.value.status_code==status


@pytest.mark.asyncio
@pytest.mark.parametrize('url',['','http://owner.example','https://user:pass@owner.example','https://owner.example/path','https://owner.example?query'])
async def test_unconfigured_or_unsafe_owner_url_does_not_receive_token(setup,monkeypatch,url):
    monkeypatch.setenv('USER_SERVICE_URL',url)
    with pytest.raises(HTTPException) as exc:
        await security.storage_admin(setup.request())
    assert exc.value.status_code==503 and not setup.requests


@pytest.mark.asyncio
async def test_admin_handlers_use_verified_tenant_and_actor_only(setup):
    from uuid import UUID
    h=setup
    admin=security.StorageAdmin(UUID(h.tenant),h.subject)
    service=SimpleNamespace(get_storages=AsyncMock(return_value=[]),configure_storage=AsyncMock(),reset_storage=AsyncMock())
    config=StorageSettingsDto(provider='s3',region='us-east-1',aws_bucket_name='private-bucket',aws_access_key='unit',aws_secret_key='unit-secret',is_enabled=True,updated_by_user_id='attacker')
    assert (await endpoint.configure(config,admin,service)).status_code==201
    assert config.updated_by_user_id==h.subject
    service.configure_storage.assert_awaited_once_with(config,admin.tenant)
    response=Response()
    assert await endpoint.list_settings(admin,service,response)==[]
    assert response.headers['cache-control']=='no-store'
    service.get_storages.assert_awaited_once_with(admin.tenant)
    storage_id=uuid4()
    assert (await endpoint.reset(storage_id,admin,service)).status_code==204
    service.reset_storage.assert_awaited_once_with(str(storage_id),h.subject,admin.tenant)


@pytest.mark.parametrize('replacement',[
    'DefaultEndpointsProtocol=http', 'BlobEndpoint=https://internal.example',
    'EndpointSuffix=internal.example', 'UseDevelopmentStorage=true',
    'AccountName=../internal', 'AccountKey=invalid', 'AccountName=duplicate;AccountName=unitaccount',
])
def test_network_admin_cannot_select_custom_or_insecure_provider_endpoints(replacement):
    import base64
    fields={'DefaultEndpointsProtocol':'https','AccountName':'unitaccount','AccountKey':base64.b64encode(b'a'*64).decode(),'EndpointSuffix':'core.windows.net'}
    connection=';'.join(k+'='+v for k,v in fields.items())
    configuration=StorageSettingsDto(provider='azure_blob',region='eastus',is_enabled=True,
        azure_container_name='private-container',azure_connection_string=connection)
    endpoint.validate_provider(configuration)
    key=replacement.partition('=')[0]
    altered=';'.join(k+'='+v for k,v in fields.items() if k != key)+';'+replacement
    configuration.azure_connection_string=altered
    with pytest.raises(HTTPException) as exc:
        endpoint.validate_provider(configuration)
    assert exc.value.status_code==400


@pytest.mark.asyncio
async def test_owner_deadline_cancels_a_stalled_response(setup,monkeypatch):
    import anyio
    stopped=False
    async def slow(request):
        nonlocal stopped
        try:
            await anyio.sleep_forever()
        finally:
            stopped=True
    # setup already installs a mock factory; retain a real client constructor.
    from httpx._client import AsyncClient
    monkeypatch.setattr(security.httpx,'AsyncClient',lambda **kwargs: AsyncClient(transport=httpx.MockTransport(slow),**kwargs))
    original=anyio.fail_after
    monkeypatch.setattr(security.anyio,'fail_after',lambda seconds: original(0.01))
    with pytest.raises(HTTPException) as exc:
        await security.storage_admin(setup.request())
    assert exc.value.status_code==503 and stopped


@pytest.mark.asyncio
async def test_account_status_check_runs_off_event_loop_and_closed_accounts_deny(setup,monkeypatch):
    import asyncio
    from jwt import InvalidTokenError
    checked=[]
    closed=False
    def account_status(claims):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise InvalidTokenError("Synchronous account check cannot run on the event loop")
        checked.append(claims['sub'])
        if closed:
            raise InvalidTokenError("Account closed")
    monkeypatch.setattr(tokens,'require_open_account',account_status)
    assert (await security.storage_admin(setup.request())).subject==setup.subject
    assert checked==[setup.subject]
    closed=True
    with pytest.raises(HTTPException) as exc:
        await security.storage_admin(setup.request())
    assert exc.value.status_code==403 and len(setup.requests)==1
    assert checked==[setup.subject,setup.subject]
