"""Registry selection is explicit and never creates tables or tenants."""
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID
import pytest
from api.infrastructure.security.media_registry_settings import RegistrySettings,read_registry
from api.infrastructure.security.media_allocation_settings import load_issuers
from test_media_allocation_endpoint import configuration
from test_media_allocation_issuer import issuer
from test_media_allocation_grant import grant


def test_private_registry_schema_and_scope(tmp_path,issuer,grant):
    scope,_,_,_,key,*_=issuer
    path=tmp_path/'registry.json'
    value={'version':1,'tenant':scope.tenant,'issuer':scope.issuer,'schema':'storage_owner','databaseUrl':'postgresql+psycopg://runtime:secret@db/storage'}
    path.write_text(json.dumps(value));path.chmod(0o600)
    settings=read_registry(str(path),scope.tenant,scope.issuer)
    assert 'secret' not in repr(settings)
    assert settings.store().tenant==scope.tenant
    config=tmp_path/'issuer.json';body=configuration(scope,key,grant[1]);body['version']=2;body['bindings'][0]['registryFile']=str(path)
    config.write_text(json.dumps(body));config.chmod(0o600)
    loaded=load_issuers({'STORAGE_MEDIA_ALLOCATION_FILE':str(config)})[scope.tenant]
    assert loaded.registry==settings
    assert loaded.bind(None).store.schema=='storage_owner'
    for field,bad in [('version',True),('tenant',str(UUID(int=99))),('issuer',str(UUID(int=99))),('schema','bad;sql'),('databaseUrl',1),('databaseUrl','sqlite:///x'),('databaseUrl','postgresql+psycopg:///storage')]:
        path.write_text(json.dumps({**value,field:bad}))
        with pytest.raises(ValueError):read_registry(str(path),scope.tenant,scope.issuer)
    path.write_text(json.dumps(value));path.chmod(0o644)
    with pytest.raises(ValueError):read_registry(str(path),scope.tenant,scope.issuer)


@pytest.mark.asyncio
async def test_runtime_role_and_all_three_tables_must_be_restricted():
    settings=RegistrySettings('postgresql+psycopg://runtime@db/storage','storage_owner',str(UUID(int=1)),str(UUID(int=2)))
    cursor=SimpleNamespace(execute=AsyncMock(),fetchone=AsyncMock(),fetchall=AsyncMock())
    @asynccontextmanager
    async def context(value=None):yield value
    connection=SimpleNamespace(transaction=lambda:context(),cursor=lambda **kw:context(cursor))
    @asynccontextmanager
    async def connect():yield connection
    role={'rolsuper':False,'rolbypassrls':False,'rolcreaterole':False,'rolcreatedb':False,'rolreplication':False,'can_create':False}
    tables=[{'relname':name,'relrowsecurity':True,'relforcerowsecurity':True,'owns_table':False} for name in ('tenants','storage_media_objects','storage_media_refs')]
    cursor.fetchone.return_value=role;cursor.fetchall.return_value=tables
    async with settings.store(connect).connect() as checked:assert checked is connection
    for invalid in [None,*[{**role,field:True} for field in role]]:
        cursor.fetchone.return_value=invalid
        with pytest.raises(ValueError):
            async with settings.store(connect).connect():pass
    cursor.fetchone.return_value=role
    for invalid in [tables[:2],[{**tables[0],'owns_table':True},*tables[1:]], [{**tables[0],'relrowsecurity':False},*tables[1:]], [{**tables[0],'relforcerowsecurity':False},*tables[1:]]]:
        cursor.fetchall.return_value=invalid
        with pytest.raises(ValueError):
            async with settings.store(connect).connect():pass
