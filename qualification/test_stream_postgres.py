"""CI-only PostgreSQL capacity, single-use and restart contracts."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import os
from pathlib import Path
import time
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url
from api.streaming.contracts import Refused, Scope, Subject
from api.streaming.postgres import PostgresStreams


@pytest.fixture
def streams():
    url=make_url(os.environ['TEST_DATABASE_URL']).set(drivername='postgresql').render_as_string(hide_password=False)
    schema,owner,runtime=('stream_'+uuid4().hex for _ in range(3))
    tenant,generation=str(uuid4()),str(uuid4())
    path=Path(os.environ['STORAGE_SCHEMA_DIRECTORY'])/'stream_migrate.py'
    spec=importlib.util.spec_from_file_location('qualified_stream_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with psycopg.connect(url,autocommit=True) as admin:
        try:
            for role in (owner,runtime):admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE').format(sql.Identifier(role)))
            admin.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(sql.Identifier(schema),sql.Identifier(owner)))
            with psycopg.connect(url,autocommit=True) as setup:
                setup.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(owner)))
                migration.apply(setup,schema,runtime)
                setup.execute("SELECT set_config('app.current_tenant_id',%s,false)",(tenant,))
                setup.execute(sql.SQL('INSERT INTO {}.tenants VALUES (%s)').format(sql.Identifier(schema)),(tenant,))
                setup.execute(sql.SQL('INSERT INTO {}.stream_policies VALUES (%s,32000000000,%s,true)').format(sql.Identifier(schema)),(tenant,generation))
            @asynccontextmanager
            async def connect():
                async with await psycopg.AsyncConnection.connect(url,autocommit=True) as connection:
                    await connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(runtime)))
                    yield connection
            yield PostgresStreams(tenant,connect,schema=schema),generation,connect,schema
        finally:
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            for role in (runtime,owner):admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))


async def reserve(store,generation,room='room',action='publish'):
    now=int(time.time());end=min(now+60,(now//86400+1)*86400)
    return await store.reserve(Scope(Subject(store.tenant,'owner-principal'),room,action,'192.0.2.1'),
        generation,str(uuid4()),uuid4().hex*2,now,end)


@pytest.mark.asyncio
async def test_atomic_publish_and_token_consumption(streams):
    store,generation,_,_=streams
    results=await asyncio.gather(reserve(store,generation),reserve(store,generation),return_exceptions=True)
    assert sum(isinstance(value,dict) for value in results)==1
    row=next(value for value in results if isinstance(value,dict))
    outcomes=await asyncio.gather(store.consume(row,str(uuid4()),int(time.time())),store.consume(row,str(uuid4()),int(time.time())),return_exceptions=True)
    assert sum(value is None for value in outcomes)==1
    assert sum(isinstance(value,Refused) for value in outcomes)==1
    for _ in range(10):await reserve(store,generation,action='read')
    with pytest.raises(psycopg.errors.RaiseException):await reserve(store,generation,action='read')


@pytest.mark.asyncio
async def test_restart_closes_without_refund_and_missing_policy_never_bootstraps(streams):
    store,generation,connect,schema=streams
    row=await reserve(store,generation)
    restarted=PostgresStreams(store.tenant,connect,schema=schema)
    await restarted.close(row['reservation_id']);await restarted.close(row['reservation_id'])
    assert await restarted.outstanding(generation)==[]
    async with connect() as connection:
        await connection.execute("SELECT set_config('app.current_tenant_id',%s,false)",(store.tenant,))
        result=await (await connection.execute(sql.SQL('SELECT reserved_bytes FROM {}.stream_sessions').format(sql.Identifier(schema)))).fetchone()
        assert result==(row['reserved_bytes'],)
    with pytest.raises(Refused):await reserve(store,str(uuid4()))
    with pytest.raises(Refused):await reserve(PostgresStreams(str(uuid4()),connect,schema=schema),generation)


@pytest.mark.asyncio
async def test_pending_cleanup_and_publisher_close_are_serialized_with_consumption(streams):
    store, generation, _, _ = streams
    publisher = await reserve(store, generation)
    await store.consume(publisher, str(uuid4()), int(time.time()))
    await store.close(publisher['reservation_id'], pending_only=True)
    viewer = await reserve(store, generation, action='read')

    await store.close(publisher['reservation_id'])
    with pytest.raises(Refused, match='Publisher unavailable'):
        await store.consume(viewer, str(uuid4()), int(time.time()))

    remaining = await store.outstanding(generation)
    assert [(row['reservation_id'], row['phase']) for row in remaining] == [(viewer['reservation_id'], 0)]
