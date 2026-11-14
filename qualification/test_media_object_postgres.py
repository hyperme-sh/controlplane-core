"""CI-only references, failure recovery and exactly-once quota release contracts."""
from dataclasses import replace
import hashlib
from uuid import UUID,uuid4
import pytest
from psycopg import sql
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.media_retirement_request import RetirementRequestSigner
from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_retirement_postgres import PostgresMediaRetirement
from api.infrastructure.persistence.media_object_postgres import PostgresMediaObjects
from api.infrastructure.externals.media_retirement_backend import RETIRED
from test_media_allocation_postgres import registry


class Backend:
    def __init__(self):self.objects={};self.retirements=[];self.fail_put=False;self.fail_retire=False
    async def put(self,key,body,metadata):
        if key in self.objects:return False
        self.objects[key]=metadata
        if self.fail_put:raise ObjectUnavailable('uncertain write')
        return True
    async def head(self,key):return self.objects.get(key)
    async def retire(self,key):
        self.retirements.append(key)
        self.objects[key]=RETIRED
        if self.fail_retire:raise ObjectUnavailable('uncertain retirement')


async def upload(service,candidate,data=b'x'*33,kind='ciphertext'):
    async def chunks():yield data
    return await service.put(object=candidate.scope.object,owner=candidate.scope.owner,kind=kind,
        digest=hashlib.sha256(data).hexdigest(),size=len(data),chunks=chunks(),guard=lambda:None)


async def begin(store,candidate,command='e'*64):
    key=Ed25519PrivateKey.from_private_bytes(b'\x08'*32)
    signer=RetirementRequestSigner(issuer=store.issuer,tenant=store.tenant,source=candidate.scope.source,key_id='source',signing_key=key)
    source=PostgresMediaRetirement(store,source=candidate.scope.source,request_keys={'source':key.public_key()},clock=lambda:1100)
    args=dict(object=candidate.scope.object,owner=candidate.scope.owner,command=command)
    return await source.begin(signer.sign(**args,now=1100),**args,cancel=lambda:None)


@pytest.mark.asyncio
async def test_shared_payload_and_failed_cleanup_keep_other_object_and_quota(registry):
    store,candidate,connect,schema=registry;backend=Backend();service=PostgresMediaObjects(store,backend)
    other=replace(candidate,scope=replace(candidate.scope,object=candidate.scope.object[:-32]+'b'*32),allocation=str(uuid4()))
    for value in (candidate,other):await store.reserve(value,now=1100,before_write=lambda:None)
    assert await upload(service,candidate)
    assert not await upload(service,other)
    await begin(store,candidate)
    assert await service.cleanup(object=candidate.scope.object,command='e'*64,guard=lambda:None)
    assert not backend.retirements
    assert not await service.cleanup(object=candidate.scope.object,command='e'*64,guard=lambda:None)
    with pytest.raises(RecordConflict):await upload(service,candidate)
    assert not await upload(service,other)
    await begin(store,other,'f'*64)
    backend.fail_retire=True
    with pytest.raises(ObjectUnavailable):await service.cleanup(object=other.scope.object,command='f'*64,guard=lambda:None)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        row=await (await connection.execute(sql.SQL('SELECT phase,(SELECT count(*) FROM {}.storage_media_refs) FROM {}.storage_media_objects WHERE object_id=%s').format(sql.Identifier(schema),sql.Identifier(schema)),(other.scope.object,))).fetchone()
        assert row==(1,1)
    backend.fail_retire=False
    assert await service.cleanup(object=other.scope.object,command='f'*64,guard=lambda:None)
    assert set(backend.objects.values())=={RETIRED}
    assert not await service.cleanup(object=other.scope.object,command='f'*64,guard=lambda:None)
    with pytest.raises(RecordConflict):await store.reserve(other,now=1100,before_write=lambda:None)
    fresh=replace(candidate,scope=replace(candidate.scope,object=candidate.scope.object[:-32]+'c'*32),allocation=str(uuid4()))
    await store.reserve(fresh,now=1100,before_write=lambda:None)
    with pytest.raises(ObjectUnavailable):await upload(service,fresh)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        assert (await (await connection.execute(sql.SQL('SELECT count(*) FROM {}.storage_media_refs').format(sql.Identifier(schema)))).fetchone())==(0,)
    assert await upload(service,fresh,b'fresh encrypted bytes'.ljust(33,b'x'))


@pytest.mark.asyncio
async def test_unknown_write_result_keeps_durable_cleanup_target(registry):
    store,candidate,connect,schema=registry;backend=Backend();service=PostgresMediaObjects(store,backend)
    await store.reserve(candidate,now=1100,before_write=lambda:None)
    backend.fail_put=True
    with pytest.raises(ObjectUnavailable):await upload(service,candidate)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        assert (await (await connection.execute(sql.SQL('SELECT count(*) FROM {}.storage_media_refs').format(sql.Identifier(schema)))).fetchone())==(1,)
    await begin(store,candidate)
    assert await service.cleanup(object=candidate.scope.object,command='e'*64,guard=lambda:None)
    assert set(backend.objects.values())=={RETIRED}


@pytest.mark.asyncio
async def test_owner_capacity_reclaimed_only_after_confirmed_cleanup(registry):
    store,candidate,connect,schema=registry;backend=Backend();service=PostgresMediaObjects(store,backend)
    values=[]
    for i in range(9):
        value=replace(candidate,scope=replace(candidate.scope,object=candidate.scope.object[:-32]+f'{i:032x}'),allocation=str(uuid4()))
        await store.reserve(value,now=1100,before_write=lambda:None);values.append(value)
    for value in values[:8]:await upload(service,value)
    with pytest.raises(AllocationRejected,match='quota'):await upload(service,values[-1])
    await begin(store,values[0])
    with pytest.raises(AllocationRejected,match='quota'):await upload(service,values[-1])
    assert await service.cleanup(object=values[0].scope.object,command='e'*64,guard=lambda:None)
    assert not await upload(service,values[-1])
    with pytest.raises(RecordConflict):await upload(service,values[0])
    with pytest.raises(RecordConflict):await upload(service,values[1],b'y'*33)
    assert await upload(service,values[1],b'wrap',kind='wrap')
