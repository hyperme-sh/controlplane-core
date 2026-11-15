"""CI-only references, failure recovery and exactly-once quota release contracts."""
from dataclasses import replace
import hashlib
from uuid import UUID,uuid4
import pytest
from psycopg import sql
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from api.domain.group_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.group_object_postgres import PostgresGroupObjects
from api.infrastructure.externals.group_retirement_backend import RETIRED
from test_group_allocation_postgres import registry


class Backend:
    def __init__(self):self.objects={};self.bytes={};self.retirements=[];self.fail_put=False;self.fail_retire=False
    async def put(self,key,body,metadata):
        if key in self.objects:return False
        self.objects[key]=metadata;self.bytes[key]=body.read()
        if self.fail_put:raise ObjectUnavailable('uncertain write')
        return True
    async def head(self,key):return self.objects.get(key)
    async def read(self,key,start,length):yield self.bytes[key][start:start+length]
    async def retire(self,key):
        self.retirements.append(key)
        self.objects[key]=RETIRED
        if self.fail_retire:raise ObjectUnavailable('uncertain retirement')


async def upload(service,candidate,data=b'x'*33):
    async def chunks():yield data
    return await service.put(object=candidate.scope.set_id,owner=candidate.scope.owner,
        digest=hashlib.sha256(data).hexdigest(),size=len(data),chunks=chunks(),guard=lambda:None)


async def begin(store,candidate,command='e'*64):
    # Fixture-only authority boundary; no public retirement route exists yet.
    async with store._locked() as cursor:
        await cursor.execute(sql.SQL('UPDATE {} SET phase=1,deletion_command=%s WHERE tenant_id=%s AND set_id=%s').format(store._table()),(command,store.tenant,candidate.scope.set_id))


@pytest.mark.asyncio
async def test_delayed_retirement_cannot_erase_another_groups_new_wrap(registry):
    store,candidate,connect,schema=registry;backend=Backend();service=PostgresGroupObjects(store,backend,location='a'*64)
    other=replace(candidate,scope=replace(candidate.scope,set_id=candidate.scope.set_id[:-32]+'b'*32),allocation=str(uuid4()))
    for value in (candidate,other):await store.reserve(value,now=1100,before_write=lambda:None)
    assert await upload(service,candidate)
    await begin(store,candidate)
    pending=[]
    async def delayed(key):
        pending.append(key)
        raise ObjectUnavailable('provider retirement still in flight')
    backend.retire=delayed
    with pytest.raises(ObjectUnavailable):
        await service.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    with pytest.raises(RecordConflict):await upload(service,candidate)
    assert await upload(service,other)
    # Provider completes only after the new group's successful write.
    backend.objects[pending[0]]=RETIRED
    args=dict(object=other.scope.set_id,digest=hashlib.sha256(b'x'*33).hexdigest(),guard=lambda:None)
    async with service.open(**args) as (body,metadata):assert body.read()==b'x'*33
    backend.retire=Backend.retire.__get__(backend)
    assert await service.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    assert not await service.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    assert not await upload(service,other)
    async with service.open(**args) as (body,metadata):assert body.read()==b'x'*33


@pytest.mark.asyncio
async def test_unknown_write_result_keeps_durable_cleanup_target(registry):
    store,candidate,connect,schema=registry;backend=Backend();service=PostgresGroupObjects(store,backend,location="a"*64)
    await store.reserve(candidate,now=1100,before_write=lambda:None)
    backend.fail_put=True
    with pytest.raises(ObjectUnavailable):await upload(service,candidate)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        assert (await (await connection.execute(sql.SQL('SELECT count(*) FROM {}.storage_group_refs').format(sql.Identifier(schema)))).fetchone())==(1,)
    await begin(store,candidate)
    assert await service.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    assert set(backend.objects.values())=={RETIRED}


@pytest.mark.asyncio
async def test_changed_provider_cannot_confirm_cleanup_or_release_refs(registry):
    store,candidate,connect,schema=registry;original=Backend();replacement=Backend()
    first=PostgresGroupObjects(store,original,location='a'*64)
    changed=PostgresGroupObjects(store,replacement,location='b'*64)
    await store.reserve(candidate,now=1100,before_write=lambda:None)
    await upload(first,candidate)
    with pytest.raises(RecordConflict):await upload(changed,candidate)
    await begin(store,candidate)
    with pytest.raises(RecordConflict):await changed.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    assert not replacement.retirements and not original.retirements
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        assert (await (await connection.execute(sql.SQL('SELECT count(*) FROM {}.storage_group_refs').format(sql.Identifier(schema)))).fetchone())==(1,)
    assert await first.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)

@pytest.mark.asyncio
async def test_durable_read_requires_exact_live_reference_and_provider_location(registry):
    store,candidate,connect,schema=registry;backend=Backend()
    service=PostgresGroupObjects(store,backend,location='a'*64)
    await store.reserve(candidate,now=1100,before_write=lambda:None)
    await upload(service,candidate)
    args=dict(object=candidate.scope.set_id,digest=hashlib.sha256(b'x'*33).hexdigest(),guard=lambda:None)
    async with service.open(**args) as (body,metadata):
        assert body.read()==b'x'*33 and metadata.size==33
    wrong=PostgresGroupObjects(store,backend,location='b'*64)
    with pytest.raises(RecordConflict):
        async with wrong.open(**args):pass
    with pytest.raises(AllocationRejected):
        async with service.open(**{**args,'digest':'e'*64}):pass
    await begin(store,candidate)
    with pytest.raises(RecordConflict):
        async with service.open(**args):pass
    await service.cleanup(object=candidate.scope.set_id,command='e'*64,guard=lambda:None)
    with pytest.raises(RecordConflict):
        async with service.open(**args):pass
