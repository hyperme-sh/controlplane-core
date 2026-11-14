"""Provider retirement unit contracts; network services run only in CI."""
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID
import pytest
from azure.core.exceptions import ResourceExistsError
from api.domain.tenant_objects import ObjectInputError,ObjectMetadata,ObjectUnavailable
from api.infrastructure.externals.media_retirement_backend import AzureMediaObjects,S3MediaObjects,media_key,checked,RETIRED,EMPTY_DIGEST

DATA=b'x'*33
DIGEST=hashlib.sha256(DATA).hexdigest()
KEY=media_key(UUID(int=1),'ciphertext',DIGEST)


@pytest.mark.asyncio
async def test_azure_marker_overwrites_payload_and_stops_late_create():
    state={'size':33,'digest':DIGEST}
    async def upload(body,**kw):
        if not kw['overwrite']:
            error=ResourceExistsError('exists');error.error_code='BlobAlreadyExists';raise error
        assert body==b'' and kw['length']==0
        state.update(size=0,digest=EMPTY_DIGEST)
    blob=SimpleNamespace(upload_blob=AsyncMock(side_effect=upload),get_blob_properties=AsyncMock(side_effect=lambda:SimpleNamespace(size=state['size'],metadata={'sha256':state['digest']})))
    backend=AzureMediaObjects(SimpleNamespace(get_blob_client=Mock(return_value=blob)))
    await backend.retire(KEY)
    assert await backend.head(KEY)==RETIRED
    await backend.retire(KEY)
    assert blob.upload_blob.await_count==1
    assert not await backend.put(KEY,BytesIO(DATA),ObjectMetadata(33,DIGEST))
    assert await backend.head(KEY)==RETIRED
    with pytest.raises(ObjectInputError):await backend.delete(KEY)


@pytest.mark.asyncio
async def test_azure_failure_and_unconfirmed_write_never_report_completion():
    blob=SimpleNamespace(upload_blob=AsyncMock(),get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=33,metadata={'sha256':DIGEST})))
    backend=AzureMediaObjects(SimpleNamespace(get_blob_client=Mock(return_value=blob)))
    with pytest.raises(ObjectUnavailable,match='not confirmed'):await backend.retire(KEY)
    blob.upload_blob.side_effect=RuntimeError('private provider error')
    with pytest.raises(ObjectUnavailable,match='^Media retirement failed$'):await backend.retire(KEY)


@pytest.mark.asyncio
async def test_namespace_bounds_and_reads():
    blob=SimpleNamespace()
    backend=AzureMediaObjects(SimpleNamespace(get_blob_client=Mock(return_value=blob)))
    for key in [None,'tenant-objects/v1/x/y',KEY.replace('00000000000000000000000000000001','0'*32),KEY+'/extra']:
        for operation in [lambda:backend.head(key),lambda:backend.retire(key),lambda:backend.put(key,BytesIO(DATA),ObjectMetadata(33,DIGEST))]:
            with pytest.raises(ObjectInputError):await operation()
        with pytest.raises(ObjectInputError):_=[x async for x in backend.read(key,0,1)]
    for metadata in [ObjectMetadata(True,DIGEST),ObjectMetadata(32,DIGEST),ObjectMetadata(67108865,DIGEST),ObjectMetadata(33,'f'*64)]:
        with pytest.raises(ObjectInputError):await backend.put(KEY,BytesIO(DATA),metadata)
    wrap=media_key(UUID(int=1),'wrap',DIGEST)
    with pytest.raises(ObjectInputError):await backend.put(wrap,BytesIO(DATA),ObjectMetadata(65537,DIGEST))
    closed=[]
    async def chunks():
        try:yield b'x';yield b'x'
        finally:closed.append(True)
    blob.download_blob=AsyncMock(return_value=SimpleNamespace(size=2,chunks=chunks))
    assert b''.join([chunk async for chunk in backend.read(KEY,0,2)])==b'xx'
    assert closed==[True]


def test_key_validation():
    for args in [(UUID(int=0),'wrap',DIGEST),('tenant','wrap',DIGEST),(UUID(int=1),'other',DIGEST),(UUID(int=1),'wrap',None)]:
        with pytest.raises(ObjectInputError):media_key(*args)
    assert checked(KEY)==('ciphertext',DIGEST)


@pytest.mark.asyncio
async def test_s3_zero_marker_is_signed_confirmed_and_idempotent(monkeypatch):
    from api.infrastructure.externals import media_retirement_backend as module
    backend=S3MediaObjects(Mock(),'bucket')
    backend.head=AsyncMock(side_effect=[ObjectMetadata(33,DIGEST),RETIRED,RETIRED])
    backend._signed=Mock(return_value=('https://fixture.invalid/key',{'Authorization':'fixture'}))
    calls=[]
    class Client:
        def __init__(self,**kw):assert kw==dict(timeout=30,follow_redirects=False,trust_env=False)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,*args,**kw):calls.append((args,kw));yield SimpleNamespace(status_code=200)
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    await backend.retire(KEY);await backend.retire(KEY)
    assert len(calls)==1 and calls[0][1]['content']==b''
    headers=backend._signed.call_args.args[2]
    assert headers['Content-Length']=='0' and headers['x-amz-meta-retired']=='1'
    assert 'If-None-Match' not in headers


@pytest.mark.asyncio
async def test_s3_failed_or_unconfirmed_retirement_refuses(monkeypatch):
    from api.infrastructure.externals import media_retirement_backend as module
    backend=S3MediaObjects(Mock(),'bucket');backend._signed=Mock(return_value=('https://fixture.invalid/key',{}))
    response={'status':500}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,*args,**kw):yield SimpleNamespace(status_code=response['status'])
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    backend.head=AsyncMock(return_value=None)
    with pytest.raises(ObjectUnavailable,match='failed'):await backend.retire(KEY)
    response['status']=200
    with pytest.raises(ObjectUnavailable,match='not confirmed'):await backend.retire(KEY)
