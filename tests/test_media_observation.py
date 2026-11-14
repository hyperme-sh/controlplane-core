"""Read-only observation contracts with substituted cursor/provider metadata."""
from contextlib import asynccontextmanager
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID
import pytest
from psycopg import sql
from api.infrastructure.persistence.media_observation import observe_media, observation_request
from api.domain.tenant_objects import ObjectInputError, ObjectMetadata, ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.externals.media_retirement_backend import RETIRED

TENANT=str(UUID(int=1));ISSUER=str(UUID(int=2));OWNER='a'*43+'.'+'b'*43
OBJECT='media_p_'+'a'*128+'_'+'b'*32
DIGEST='c'*64;LOCATION='d'*64
REF={'kind':'ciphertext','digest':DIGEST}


@pytest.fixture
def fixture():
    cursor=Mock(execute=AsyncMock(),fetchone=AsyncMock(),fetchall=AsyncMock(return_value=[]))
    @asynccontextmanager
    async def locked():yield cursor
    store=SimpleNamespace(tenant=TENANT,issuer=ISSUER,_locked=locked,_table=lambda:sql.Identifier('storage_owner','storage_media_objects'))
    backend=Mock(head=AsyncMock(return_value=RETIRED),put=AsyncMock(),retire=AsyncMock())
    media=SimpleNamespace(store=store,backend=backend,location=LOCATION,locations=sql.Identifier('storage_owner','storage_media_locations'),refs=sql.Identifier('storage_owner','storage_media_refs'),
        _object=AsyncMock(return_value={'owner_principal':OWNER,'phase':2,'deletion_command':'e'*64}))
    cursor.fetchone.side_effect=cycle([{'provider_location':LOCATION},{'owner_objects':0,'owner_bytes':0,'tenant_bytes':99}])
    return media,cursor,backend


@pytest.mark.asyncio
async def test_retired_snapshot_uses_real_read_boundary_and_never_mutates(fixture):
    media,cursor,backend=fixture
    result=await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)
    assert result=={'tenant':TENANT,'issuer':ISSUER,'object':OBJECT,'owner':OWNER,'phase':2,'command':'e'*64,
        'providerLocation':LOCATION,'references':[],'quota':{'ownerObjects':0,'ownerBytes':0,'tenantBytes':99},
        'observed':[{**REF,'state':'retired','length':0}]}
    backend.head.assert_awaited_once_with('tenant-media/v1/'+UUID(TENANT).hex+'/ciphertext/'+DIGEST)
    backend.put.assert_not_awaited();backend.retire.assert_not_awaited()
    assert all(call.args[0].as_string().startswith('SELECT') for call in cursor.execute.call_args_list)
    assert all(TENANT in call.args[1] for call in cursor.execute.call_args_list)


@pytest.mark.parametrize('references',[None,{},[REF]*9,[REF,REF],[{'kind':'bad','digest':DIGEST}],[{'kind':'ciphertext','digest':'bad'}],[{**REF,'url':'https://other'}]])
def test_reference_request_bounds(references):
    with pytest.raises(ObjectInputError):observation_request(OBJECT,references)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'phase':True},{'phase':3},{'owner_principal':'bad'},{'deletion_command':None}])
async def test_malformed_registry_state_refuses_before_provider(fixture,change):
    media,cursor,backend=fixture
    media._object.return_value.update(change)
    with pytest.raises(ObjectUnavailable):await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)
    backend.head.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_or_unbound_provider_refuses_without_repair(fixture):
    media,cursor,backend=fixture
    for location in [{'provider_location':'f'*64},None]:
        cursor.fetchone.side_effect=[location]
        with pytest.raises(RecordConflict):await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)
    backend.head.assert_not_awaited()
    assert all(call.args[0].as_string().startswith('SELECT') for call in cursor.execute.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata,state,length',[(None,'missing',None),(ObjectMetadata(33,DIGEST),'present',33),(RETIRED,'retired',0)])
async def test_provider_states_are_distinct_not_inferred_from_http(fixture,metadata,state,length):
    media,cursor,backend=fixture;backend.head.return_value=metadata
    result=await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)
    assert result['observed']==[{**REF,'state':state,'length':length}]


@pytest.mark.asyncio
async def test_active_snapshot_retains_charged_refs_and_missing_payload(fixture):
    media,cursor,backend=fixture
    media._object.return_value.update(phase=0,deletion_command=None)
    cursor.fetchall.return_value=[{'kind':'ciphertext','digest':DIGEST,'byte_length':33}]
    cursor.fetchone.side_effect=cycle([{'provider_location':LOCATION},{'owner_objects':1,'owner_bytes':33,'tenant_bytes':33}])
    backend.head.return_value=None
    result=await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)
    assert result['references']==[{**REF,'length':33}]
    assert result['quota']['ownerBytes']==33
    assert result['observed'][0]['state']=='missing'


@pytest.mark.asyncio
async def test_retired_refs_invalid_quota_and_provider_metadata_refuse(fixture):
    media,cursor,backend=fixture
    cursor.fetchall.return_value=[{'kind':'ciphertext','digest':DIGEST,'byte_length':33}]
    with pytest.raises(ObjectUnavailable):await observe_media(media,object=OBJECT,references=[],guard=lambda:None)
    cursor.fetchall.return_value=[]
    cursor.fetchone.side_effect=[{'provider_location':LOCATION},{'owner_objects':True,'owner_bytes':0,'tenant_bytes':0}]
    with pytest.raises(ObjectUnavailable):await observe_media(media,object=OBJECT,references=[],guard=lambda:None)
    cursor.fetchone.side_effect=[{'provider_location':LOCATION},{'owner_objects':0,'owner_bytes':0,'tenant_bytes':0}]
    backend.head.return_value=ObjectMetadata(33,'f'*64)
    with pytest.raises(ObjectUnavailable):await observe_media(media,object=OBJECT,references=[REF],guard=lambda:None)


@pytest.mark.asyncio
async def test_provider_io_runs_without_tenant_lock_and_revalidates(fixture):
    media, cursor, backend = fixture
    locked = False
    acquisitions = 0
    @asynccontextmanager
    async def lock():
        nonlocal locked, acquisitions
        assert not locked
        locked = True
        acquisitions += 1
        try:
            yield cursor
        finally:
            locked = False
    media.store._locked = lock
    async def head(key):
        assert not locked
        return RETIRED
    backend.head.side_effect = head
    await observe_media(media, object=OBJECT, references=[REF], guard=lambda:None)
    assert acquisitions == 2 and not locked


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['quota', 'command', 'location'])
async def test_registry_changes_during_provider_io_refuse(fixture, change):
    media, cursor, backend = fixture
    async def head(key):
        if change == 'command':
            media._object.return_value = {**media._object.return_value, 'deletion_command':'f'*64}
        elif change == 'location':
            cursor.fetchone.side_effect = [{'provider_location':'f'*64}]
        else:
            cursor.fetchone.side_effect = [{'provider_location':LOCATION},
                {'owner_objects':1,'owner_bytes':33,'tenant_bytes':99}]
        return RETIRED
    backend.head.side_effect = head
    with pytest.raises(RecordConflict):
        await observe_media(media, object=OBJECT, references=[REF], guard=lambda:None)
