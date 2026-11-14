"""CI-only PostgreSQL allocation consumer contracts with restricted roles."""
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import ec
import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url
from api.domain.media_allocation_grant import AllocationRejected, AllocationScope
from api.domain.media_allocation_store import Reservation
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_allocation_postgres import PostgresAllocationStore


@pytest.fixture
def registry():
    url = make_url(os.environ['TEST_DATABASE_URL']).set(drivername='postgresql').render_as_string(hide_password=False)
    schema, owner, runtime = ('media_' + uuid4().hex for _ in range(3))
    tenant, issuer, source = str(uuid4()), str(uuid4()), str(uuid4())
    path = Path(os.environ['STORAGE_SCHEMA_DIRECTORY']) / 'media_migrate.py'
    spec = importlib.util.spec_from_file_location('qualified_media_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with psycopg.connect(url, autocommit=True) as admin:
        try:
            for role in (owner, runtime):
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE').format(sql.Identifier(role)))
            admin.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(sql.Identifier(schema), sql.Identifier(owner)))
            with psycopg.connect(url, autocommit=True) as setup:
                setup.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(owner)))
                migration.apply(setup, schema, runtime)
                setup.execute("SELECT set_config('app.current_tenant_id',%s,false)", (tenant,))
                setup.execute(sql.SQL('INSERT INTO {}.tenants VALUES (%s)').format(sql.Identifier(schema)), (tenant,))
            @asynccontextmanager
            async def connect():
                async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
                    await connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(runtime)))
                    yield connection
            point = ec.derive_private_key(7, ec.SECP256R1()).public_key().public_numbers()
            x, y = point.x.to_bytes(32), point.y.to_bytes(32)
            principal = '.'.join(base64.urlsafe_b64encode(v).rstrip(b'=').decode() for v in (x, y))
            scope = AllocationScope(issuer, tenant, 'media_p_' + (x+y).hex() + '_' + 'a'*32, principal, source, 'b'*64, 'c'*64)
            candidate = Reservation(scope, str(uuid4()), 1000, 1300)
            yield PostgresAllocationStore(tenant, issuer, connect, schema=schema), candidate, connect, schema
        finally:
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            for role in (runtime, owner):
                admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_restart_original_retry_guard_rollback_and_retirement(registry):
    store, candidate, connect, schema = registry
    def deny(): raise PermissionError('changed owner')
    with pytest.raises(PermissionError):
        await store.reserve(candidate, now=1100, before_write=deny)
    assert await store.reserve(candidate, now=1100, before_write=lambda: None) == candidate
    restarted = PostgresAllocationStore(store.tenant, store.issuer, connect, schema=schema)
    assert await restarted.reserve(replace(candidate, allocation=str(uuid4()), issued_at=1500, expires_at=1800), now=1501, before_write=lambda: None) == candidate
    with pytest.raises(PermissionError):
        await restarted.reserve(candidate, now=1100, before_write=deny)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)", (store.tenant,))
        await connection.execute(sql.SQL('UPDATE {}.storage_media_objects SET phase=1,deletion_command=%s WHERE object_id=%s').format(sql.Identifier(schema)), ('e'*64, candidate.scope.object))
        with pytest.raises(RecordConflict, match='permanent'):
            await store.reserve(candidate, now=1100, before_write=lambda: None)
        await connection.execute(sql.SQL('UPDATE {}.storage_media_objects SET phase=2 WHERE object_id=%s').format(sql.Identifier(schema)), (candidate.scope.object,))
    with pytest.raises(RecordConflict, match='permanent'):
        await store.reserve(candidate, now=1100, before_write=lambda: None)
    other = replace(candidate, scope=replace(candidate.scope, object=candidate.scope.object[:-32]+'f'*32, source=str(uuid4())))
    with pytest.raises(RecordConflict, match='identifier'):
        await store.reserve(other, now=1100, before_write=lambda: None)
    missing = PostgresAllocationStore(str(uuid4()), store.issuer, connect, schema=schema)
    with pytest.raises(AllocationRejected, match='unavailable'):
        await missing.reserve(replace(candidate, scope=replace(candidate.scope, tenant=missing.tenant)), now=1100, before_write=lambda: None)


@pytest.mark.asyncio
async def test_competing_sources_and_final_quota_slot_are_serialized(registry):
    store, candidate, connect, schema = registry
    changed = replace(candidate, scope=replace(candidate.scope, source=str(uuid4())), allocation=str(uuid4()))
    results = await asyncio.gather(*(store.reserve(value, now=1100, before_write=lambda: None) for value in (candidate, changed)), return_exceptions=True)
    assert sum(isinstance(v, Reservation) for v in results) == 1
    assert sum(isinstance(v, RecordConflict) for v in results) == 1
    for i in range(62):
        value = replace(candidate, scope=replace(candidate.scope, object=candidate.scope.object[:-32]+f'{i:032x}'), allocation=str(uuid4()))
        await store.reserve(value, now=1100, before_write=lambda: None)
    candidates = [replace(candidate, scope=replace(candidate.scope, object=candidate.scope.object[:-32]+f'{i:032x}'), allocation=str(uuid4())) for i in (62, 63)]
    results = await asyncio.gather(*(store.reserve(value, now=1100, before_write=lambda: None) for value in candidates), return_exceptions=True)
    assert sum(isinstance(v, Reservation) for v in results) == 1
    assert sum(isinstance(v, AllocationRejected) for v in results) == 1
    loser = candidates[next(i for i, value in enumerate(results) if isinstance(value, AllocationRejected))]
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)", (store.tenant,))
        await connection.execute(sql.SQL('UPDATE {}.storage_media_objects SET phase=1,deletion_command=%s WHERE object_id=%s').format(sql.Identifier(schema)), ('e'*64, candidate.scope.object))
        with pytest.raises(AllocationRejected, match='quota'):
            await store.reserve(loser, now=1100, before_write=lambda: None)
        await connection.execute(sql.SQL('UPDATE {}.storage_media_objects SET phase=2 WHERE object_id=%s').format(sql.Identifier(schema)), (candidate.scope.object,))
    assert await store.reserve(loser, now=1100, before_write=lambda: None) == loser


@pytest.mark.asyncio
async def test_signed_retirement_matches_original_source_and_never_releases_early(registry):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from api.domain.media_retirement_request import RetirementRequestSigner
    from api.infrastructure.persistence.media_retirement_postgres import PostgresMediaRetirement
    store,candidate,connect,schema=registry
    await store.reserve(candidate,now=1100,before_write=lambda:None)
    key=Ed25519PrivateKey.from_private_bytes(b'\x08'*32)
    signer=RetirementRequestSigner(issuer=store.issuer,tenant=store.tenant,source=candidate.scope.source,key_id='source',signing_key=key)
    current=[1100]
    service=PostgresMediaRetirement(store,source=candidate.scope.source,request_keys={'source':key.public_key()},clock=lambda:current[0])
    args=dict(object=candidate.scope.object,owner=candidate.scope.owner,command='e'*64)
    raw=signer.sign(**args,now=1100)
    assert await service.begin(raw,**args,cancel=lambda:None)==1
    assert await service.begin(raw,**args,cancel=lambda:None)==1
    current[0]=1200
    with pytest.raises(AllocationRejected):await service.begin(raw,**args,cancel=lambda:None)
    retry=signer.sign(**args,now=1200)
    assert await service.begin(retry,**args,cancel=lambda:None)==1
    different={**args,'command':'f'*64}
    with pytest.raises(RecordConflict):await service.begin(signer.sign(**different,now=1200),**different,cancel=lambda:None)
    with pytest.raises(RecordConflict):await store.reserve(candidate,now=1200,before_write=lambda:None)
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        row=await (await connection.execute(sql.SQL('SELECT phase,deletion_command FROM {}.storage_media_objects').format(sql.Identifier(schema)))).fetchone()
        assert row==(1,args['command'])
