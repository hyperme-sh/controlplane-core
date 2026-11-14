"""Media reference and cleanup refusal unit contracts."""
from contextlib import asynccontextmanager
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from uuid import UUID
import pytest
from psycopg import sql
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectMetadata,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_object_postgres import PostgresMediaObjects
from api.infrastructure.externals.media_retirement_backend import RETIRED

TENANT=str(UUID(int=1));ISSUER=str(UUID(int=2));OWNER='a'*43+'.'+'b'*43
OBJECT='media_p_'+'a'*128+'_'+'b'*32
DATA=b'x'*33;DIGEST=hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def service():
    cursor=Mock(execute=AsyncMock(),fetchone=AsyncMock(),fetchall=AsyncMock())
    @asynccontextmanager
    async def locked():yield cursor
    store=SimpleNamespace(tenant=TENANT,issuer=ISSUER,schema='storage_owner',_locked=locked,_table=lambda:sql.Identifier('storage_owner','storage_media_objects'))
    backend=Mock(put=AsyncMock(return_value=True),head=AsyncMock(return_value=None),retire=AsyncMock())
    value=PostgresMediaObjects(store,backend,location="a"*64)
    value._location=AsyncMock()
    row={'issuer_id':ISSUER,'owner_principal':OWNER,'phase':0,'deletion_command':None}
    return value,cursor,backend,row


@pytest.mark.asyncio
async def test_object_binding_and_input_refusals(service):
    value,cursor,backend,row=service
    for object in [None,'x'*201]:
        with pytest.raises(ObjectInputError):await value._object(cursor,object)
    for held in [None,{**row,'issuer_id':str(UUID(int=9))},{**row,'owner_principal':'other'}]:
        cursor.fetchone.return_value=held
        with pytest.raises(AllocationRejected):await value._object(cursor,OBJECT,OWNER)
    cursor.fetchone.return_value=row
    assert await value._object(cursor,OBJECT,OWNER)==row


@pytest.mark.asyncio
async def test_reference_retry_immutability_and_quota_bounds(service):
    value,cursor,backend,row=service;value._object=AsyncMock(return_value=row)
    async def reserve(kind='ciphertext'):
        return await value._reserve(OBJECT,OWNER,kind,DIGEST,33,lambda:None)
    ref={'kind':'ciphertext','digest':DIGEST,'byte_length':33}
    cursor.fetchall.return_value=[ref]
    await reserve()
    cursor.fetchall.return_value=[{**ref,'byte_length':34}]
    with pytest.raises(RecordConflict):await reserve()
    cursor.fetchall.return_value=[{**ref,'digest':'f'*64}]
    with pytest.raises(RecordConflict):await reserve()
    for charged in [[{'owner_principal':OWNER,'byte_length':33}]*8,
                    [{'owner_principal':OWNER,'byte_length':64*1024*1024}],
                    [{'owner_principal':'other','byte_length':256*1024*1024}]]:
        cursor.fetchall.side_effect=[[],charged]
        with pytest.raises(AllocationRejected):await reserve()
    cursor.fetchall.side_effect=None;cursor.fetchall.return_value=[{'kind':'wrap','digest':f'{i:064x}','byte_length':33} for i in range(128)]
    with pytest.raises(AllocationRejected):await reserve('wrap')
    value._object.return_value={**row,'phase':1}
    with pytest.raises(RecordConflict):await reserve()
    value._object.return_value=row
    cursor.fetchall.side_effect=[[],[]]
    await reserve()
    assert 'INSERT INTO' in cursor.execute.call_args.args[0].as_string()
    cursor.fetchall.side_effect=[[]]
    await reserve('wrap')
    cursor.fetchall.side_effect=[[]];backend.head.return_value=RETIRED
    cursor.execute.reset_mock()
    with pytest.raises(ObjectUnavailable):await reserve()
    assert not any('INSERT' in str(call.args[0]) for call in cursor.execute.call_args_list)


@pytest.mark.asyncio
async def test_upload_bounds_checksum_durable_order_and_provider_conflict(service):
    value,cursor,backend,row=service
    value._reserve=AsyncMock();value._object=AsyncMock(return_value=row)
    async def put(data=DATA,**changes):
        async def chunks():yield data
        return await value.put(**{**dict(object=OBJECT,owner=OWNER,kind='ciphertext',digest=DIGEST,size=33,chunks=chunks(),guard=lambda:None),**changes})
    for data,changes in [(DATA,{'owner':None}),(DATA,{'size':True}),(DATA,{'size':32}),('bad',{}),(DATA+b'x',{}),(b'y'*33,{}),(b'x',{})]:
        with pytest.raises(ObjectInputError):await put(data,**changes)
    value._reserve.assert_not_awaited();backend.put.assert_not_awaited()
    def provider(*args):
        value._reserve.assert_awaited_once();return True
    backend.put.side_effect=provider
    assert await put()
    backend.put.side_effect=None;backend.put.return_value=False
    backend.head.return_value=ObjectMetadata(33,DIGEST)
    assert not await put()
    backend.head.return_value=RETIRED
    with pytest.raises(ObjectUnavailable):await put()
    value._object.return_value={**row,'phase':1}
    with pytest.raises(RecordConflict):await put()
    value._object.return_value=row;backend.put.return_value=True
    assert await put(b'w',kind='wrap',size=1,digest=hashlib.sha256(b'w').hexdigest())


@pytest.mark.asyncio
async def test_cleanup_command_and_shared_reference_boundaries(service):
    value,cursor,backend,row=service
    value._object=AsyncMock(return_value=row)
    async def clean(command='e'*64):return await value.cleanup(object=OBJECT,command=command,guard=lambda:None)
    for command in [None,'bad']:
        with pytest.raises(ObjectInputError):await clean(command)
    with pytest.raises(RecordConflict):await clean()
    value._object.return_value={**row,'phase':1,'deletion_command':'f'*64}
    with pytest.raises(RecordConflict):await clean()
    value._object.return_value={**row,'phase':2,'deletion_command':'e'*64}
    assert not await clean();backend.retire.assert_not_awaited()
    value._object.return_value={**row,'phase':1,'deletion_command':'e'*64}
    cursor.fetchall.return_value=[{'kind':'ciphertext','digest':DIGEST},{'kind':'wrap','digest':'f'*64}]
    cursor.fetchone.side_effect=[{'exists':1},None]
    assert await clean()
    backend.retire.assert_awaited_once()
    assert backend.retire.call_args.args[0].endswith('/wrap/'+'f'*64)
    assert 'phase=2' in cursor.execute.call_args.args[0].as_string()
    cursor.fetchone.side_effect=[None];cursor.execute.reset_mock();backend.retire.side_effect=ObjectUnavailable()
    with pytest.raises(ObjectUnavailable):await clean()
    assert not any('DELETE' in str(call.args[0]) for call in cursor.execute.call_args_list)


@pytest.mark.asyncio
async def test_provider_location_is_bound_before_refs_and_never_guessed(service):
    value,cursor,backend,row=service
    check=PostgresMediaObjects._location.__get__(value)
    cursor.fetchone.side_effect=[{'provider_location':'a'*64}]
    await check(cursor,OBJECT)
    cursor.fetchone.side_effect=[{'provider_location':'b'*64}]
    with pytest.raises(RecordConflict):await check(cursor,OBJECT)
    cursor.fetchone.side_effect=[None]
    with pytest.raises(RecordConflict):await check(cursor,OBJECT)
    cursor.fetchone.side_effect=[None,{'exists':1}]
    with pytest.raises(RecordConflict):await check(cursor,OBJECT,bind=True)
    cursor.fetchone.side_effect=[None,None]
    await check(cursor,OBJECT,bind=True)
    assert cursor.execute.call_args.args[1]==(TENANT,OBJECT,'a'*64)
    with pytest.raises(ObjectInputError):PostgresMediaObjects(value.store,backend,location='bad')


@pytest.mark.asyncio
async def test_read_requires_active_bound_reference_and_verified_bytes(service):
    value,cursor,backend,row=service
    value._object=AsyncMock(return_value=row)
    cursor.fetchone.return_value={'byte_length':len(DATA)}
    backend.head.return_value=ObjectMetadata(len(DATA),DIGEST)
    async def chunks(*args):
        assert args[1:]==(0,len(DATA))
        yield DATA
    backend.read=chunks
    async with value.open(object=OBJECT,kind='ciphertext',digest=DIGEST,guard=lambda:None) as (body,metadata):
        assert body.read()==DATA and metadata==ObjectMetadata(len(DATA),DIGEST)
        held=body
    assert held.closed
    value._object.return_value={**row,'phase':1}
    with pytest.raises(RecordConflict):
        async with value.open(object=OBJECT,kind='ciphertext',digest=DIGEST,guard=lambda:None):pass
    value._object.return_value=row
    for ref,error in [(None,AllocationRejected),({'byte_length':True},ObjectUnavailable),
        ({'byte_length':32},ObjectUnavailable),({'byte_length':67108865},ObjectUnavailable)]:
        cursor.fetchone.return_value=ref
        with pytest.raises(error):
            async with value.open(object=OBJECT,kind='ciphertext',digest=DIGEST,guard=lambda:None):pass
    cursor.fetchone.return_value={'byte_length':33};backend.head.return_value=RETIRED
    with pytest.raises(ObjectUnavailable):
        async with value.open(object=OBJECT,kind='ciphertext',digest=DIGEST,guard=lambda:None):pass
    backend.head.return_value=ObjectMetadata(33,DIGEST)
    for data in ['bad',DATA+b'x',b'x',b'y'*33]:
        async def bad(*args):yield data
        backend.read=bad
        with pytest.raises(ObjectUnavailable):
            async with value.open(object=OBJECT,kind='ciphertext',digest=DIGEST,guard=lambda:None):pass


@pytest.mark.asyncio
async def test_read_wrap_cancel_and_provider_failure_close_context(service):
    value,cursor,backend,row=service;value._object=AsyncMock(return_value=row)
    data=b'w';digest=hashlib.sha256(data).hexdigest()
    cursor.fetchone.return_value={'byte_length':1};backend.head.return_value=ObjectMetadata(1,digest)
    closed=[]
    async def chunks(*args):
        try:yield data
        finally:closed.append(True)
    backend.read=chunks
    async with value.open(object=OBJECT,kind='wrap',digest=digest,guard=lambda:None) as (body,metadata):
        assert body.read()==data
    assert closed==[True]
    def cancel():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError):
        async with value.open(object=OBJECT,kind='wrap',digest=digest,guard=cancel):pass
    async def failed(*args):
        raise ObjectUnavailable()
        yield b''
    backend.read=failed
    with pytest.raises(ObjectUnavailable):
        async with value.open(object=OBJECT,kind='wrap',digest=digest,guard=lambda:None):pass
