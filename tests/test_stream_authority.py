"""Signed owner transport contracts. The HTTP owner here is a named test double."""
import base64
from dataclasses import asdict
import json
from uuid import uuid4
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from api.streaming.authority import DOMAIN, ProtectedAuthority, canonical
from api.streaming.contracts import Refused, Scope, Subject
from api.streaming.http import create_app
from test_stream_admission import Admission, Authority, Store, SCOPE, TENANT, GENERATION, NOW, wire


@pytest.mark.asyncio
async def test_fresh_signed_check_rejects_replay_forgery_and_wrong_scope():
    key=Ed25519PrivateKey.generate();mode='valid';previous=None
    def owner(request):
        nonlocal previous
        value=json.loads(request.content)
        assert request.headers['X-Stream-Service-Key']=='s'*32
        claims={'version':1,'nonce':value['nonce'],'operation':'check','evaluatedAt':NOW,
                'scope':value['scope'],'allowed':True,'validUntil':NOW+60,'revision':'a'*64}
        if mode=='scope':claims['scope']['room']='other'
        if mode=='stale':claims['evaluatedAt']=NOW-2
        result={'claims':claims,'signature':base64.b64encode(key.sign(DOMAIN+canonical(claims))).decode()}
        if mode=='forged':result['claims']['allowed']=False
        if mode=='replay':result=previous
        else:previous=result
        return httpx.Response(200,json=result)
    async with httpx.AsyncClient(transport=httpx.MockTransport(owner)) as client:
        authority=ProtectedAuthority(client,'https://authority.example/stream-check','s'*32,key.public_key(),clock=lambda:NOW)
        (await authority.check(SCOPE)).validate(SCOPE,NOW)
        for mode in ('replay','scope','stale','forged'):
            with pytest.raises(Refused):await authority.check(SCOPE)


@pytest.mark.asyncio
async def test_http_never_accepts_caller_subject_or_public_callback():
    store=Store();service=Admission(store,Authority(),GENERATION,clock=lambda:NOW)
    async def authenticate(request):
        if request.headers.get('authorization')!='Bearer contract-identity':raise Refused('Identity')
        return SCOPE.subject
    app=create_app({TENANT:service},authenticate,'c'*32)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('192.0.2.1',1234)),base_url='https://core.example') as client:
        payload={'room':'room','action':'publish','seconds':60,'fallback':True}
        assert (await client.post('/streams/reserve',json=payload)).status_code==403
        headers={'Authorization':'Bearer contract-identity'}
        assert (await client.post('/streams/reserve',json={**payload,'principal':'other'},headers=headers)).status_code==403
        response=await client.post('/streams/reserve',json=payload,headers=headers)
        assert response.status_code==200
        grant=response.json()
        assert (await client.post('/internal/mediamtx/auth',json=wire(grant),auth=('mediamtx','c'*32))).status_code==403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://localhost') as client:
        assert (await client.post('/internal/mediamtx/auth',json=wire(grant),auth=('mediamtx','wrong'))).status_code==403
        response=await client.post('/internal/mediamtx/auth',json=wire(grant),auth=('mediamtx','c'*32))
        assert response.status_code==200 and response.json()['expireAt'].endswith('Z')
        assert response.headers['cache-control']=='private, no-store'


@pytest.mark.asyncio
async def test_missing_bindings_and_oversized_body_fail_closed():
    async def authenticate(request):return SCOPE.subject
    app=create_app({},authenticate,'c'*32)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://core.example') as client:
        assert (await client.post('/streams/reserve',json={'room':'room','action':'publish','seconds':60,'fallback':True})).status_code==403
        assert (await client.post('/streams/reserve',content=b'x'*4097,headers={'content-type':'application/json'})).status_code==403
        assert (await client.post('/streams/reserve',content='{"room":"one","room":"two"}',headers={'content-type':'application/json'})).status_code==403


@pytest.mark.asyncio
async def test_owner_transport_uses_native_resolver_and_current_check():
    from api.streaming.owner import create_app as create_owner
    from starlette.requests import Request
    class NativeOwner:
        # Explicit unit double for protected owner, never a deployed fixture.
        production_ready=True
        allowed=True
        async def authenticate(self,request):
            if request.headers.get('authorization')!='Bearer native-session':raise Refused('No session')
            return SCOPE.subject
        async def check(self,scope):return __import__('api.streaming.contracts',fromlist=['Decision']).Decision(scope,self.allowed,NOW+60,'a'*64)
    source=NativeOwner();key=Ed25519PrivateKey.generate()
    app=create_owner(source,key,'s'*32,clock=lambda:NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://owner.example') as client:
        authority=ProtectedAuthority(client,'https://owner.example/internal/streams/authority','s'*32,key.public_key(),clock=lambda:NOW)
        request=Request({'type':'http','headers':[(b'authorization',b'Bearer native-session')]})
        assert await authority.authenticate(request)==SCOPE.subject
        assert (await authority.check(SCOPE)).allowed
        source.allowed=False
        with pytest.raises(Refused):await authority.check(SCOPE)
    with pytest.raises(Refused):create_owner(object(),key,'s'*32)
