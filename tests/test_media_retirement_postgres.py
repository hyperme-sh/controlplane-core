"""Authenticated retirement state transition unit contracts."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import UUID
from psycopg import sql
import pytest
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_retirement_postgres import PostgresMediaRetirement
from test_media_retirement_request import retirement
from test_media_allocation_grant import grant


@pytest.fixture
def adapter(retirement):
    scope,key,signer,encode,verify=retirement
    cursor=Mock(execute=AsyncMock(),fetchone=AsyncMock())
    @asynccontextmanager
    async def locked(): yield cursor
    store=Mock(tenant=scope.tenant,issuer=scope.issuer,_locked=locked,_table=lambda:sql.Identifier('storage_owner','storage_media_objects'))
    clock=Mock(return_value=1000)
    service=PostgresMediaRetirement(store,source=scope.source,request_keys={'source':key.public_key()},clock=clock)
    row={'issuer_id':UUID(scope.issuer),'source_id':UUID(scope.source),'owner_principal':scope.owner,'phase':0,'deletion_command':None}
    return service,cursor,clock,row,scope,encode


@pytest.mark.asyncio
async def test_begin_idempotence_and_completed_retirement(adapter):
    service,cursor,_,row,scope,encode=adapter
    for phase in (0,1,2):
        cursor.fetchone.return_value={**row,'phase':phase,'deletion_command':None if phase==0 else scope.command}
        cursor.execute.reset_mock()
        assert await service.begin(encode(),object=scope.object,owner=scope.owner,command=scope.command,cancel=lambda:None)==max(1,phase)
        assert cursor.execute.call_count==(2 if phase==0 else 1)


@pytest.mark.asyncio
async def test_missing_scope_conflict_and_different_command_refuse(adapter):
    service,cursor,_,row,scope,encode=adapter
    for changed,error in [(None,AllocationRejected),({**row,'issuer_id':UUID(int=9)},RecordConflict),({**row,'source_id':UUID(int=9)},RecordConflict),({**row,'owner_principal':'other'},RecordConflict),({**row,'phase':1,'deletion_command':'f'*64},RecordConflict)]:
        cursor.fetchone.return_value=changed;cursor.execute.reset_mock()
        with pytest.raises(error):await service.begin(encode(),object=scope.object,owner=scope.owner,command=scope.command,cancel=lambda:None)
        assert cursor.execute.call_count==1


@pytest.mark.asyncio
async def test_proof_rechecked_after_lock_and_before_commit(adapter):
    service,cursor,clock,row,scope,encode=adapter
    for times,queries in [([1030],0),([1000,1030],1),([1000,1000,1030],2)]:
        cursor.fetchone.return_value=row;cursor.execute.reset_mock();clock.side_effect=times
        with pytest.raises(AllocationRejected):await service.begin(encode(),object=scope.object,owner=scope.owner,command=scope.command,cancel=lambda:None)
        assert cursor.execute.call_count==queries
    clock.side_effect=None
    cursor.execute.reset_mock()
    with pytest.raises(PermissionError):await service.begin(encode(),object=scope.object,owner=scope.owner,command=scope.command,cancel=Mock(side_effect=PermissionError))
    cursor.execute.assert_not_called()


def test_invalid_source_key_configuration(adapter):
    service,_,_,_,_,_=adapter
    for keys in [{},{'!':object()},{'source':object()},{None:object()}]:
        with pytest.raises(AllocationRejected):PostgresMediaRetirement(service.store,source=service.source,request_keys=keys,clock=lambda:1000)
