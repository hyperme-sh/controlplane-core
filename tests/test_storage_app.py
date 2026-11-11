"""Storage process units with no database or provider connections."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from api import storage_app as runtime


def request(path='/api/v1/storage/objects/'+'a'*64, headers=None, method='GET'):
    return Request({'type':'http','method':method,'path':path,'headers':headers if headers is not None else [(b'authorization',b'Bearer unit')]})


@pytest.fixture
def db(monkeypatch):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock())
    context = AsyncMock()
    context.__aenter__.return_value = session
    maker = Mock(return_value=context)
    monkeypatch.setattr(runtime, 'get_session_maker', lambda: maker)
    set_session, reset, guc = Mock(return_value='token'), Mock(), AsyncMock()
    monkeypatch.setattr(runtime, 'set_db_session', set_session)
    monkeypatch.setattr(runtime, 'reset_db_session', reset)
    monkeypatch.setattr(runtime, 'set_current_tenant_id_guc', guc)
    tenant = uuid4()
    verify = Mock(return_value=tenant)
    monkeypatch.setattr(runtime, 'storage_session', verify)
    admin = AsyncMock(return_value=SimpleNamespace(tenant=tenant))
    monkeypatch.setattr(runtime, 'storage_admin', admin)
    return SimpleNamespace(session=session,maker=maker,reset=reset,guc=guc,tenant=tenant,verify=verify,admin=admin)


@pytest.mark.parametrize('method,operation',[('GET','read'),('HEAD','read'),('PUT','write'),('DELETE','delete')])
@pytest.mark.asyncio
async def test_object_session_uses_signed_tenant_not_host_or_query(db,method,operation):
    req=request(method=method)
    req.scope['query_string']=b'tenant_id=another'
    flow=runtime.request_database(req)
    await anext(flow)
    db.verify.assert_called_once_with('unit',operation)
    db.guc.assert_awaited_once_with(db.session,db.tenant)
    with pytest.raises(StopAsyncIteration): await anext(flow)
    db.session.commit.assert_awaited_once()
    db.session.rollback.assert_not_awaited()
    db.reset.assert_called_once_with('token')


@pytest.mark.parametrize('headers', [[],[(b'authorization',b'Basic unit')],[(b'authorization',b'Bearer a b')],[(b'authorization',b'Bearer a'),(b'authorization',b'Bearer b')]])
@pytest.mark.asyncio
async def test_missing_or_ambiguous_authorization_never_opens_database(db,headers):
    with pytest.raises(HTTPException): await anext(runtime.request_database(request(headers=headers)))
    db.maker.assert_not_called()


@pytest.mark.asyncio
async def test_mismatched_or_duplicate_tenant_header_is_denied(db):
    for headers in [[(b'x-tenant-id',str(uuid4()).encode())],[(b'x-tenant-id',str(db.tenant).encode())]*2]:
        with pytest.raises(HTTPException):
            await anext(runtime.request_database(request(headers=[(b'authorization',b'Bearer unit'),*headers])))
    db.maker.assert_not_called()
    flow=runtime.request_database(request(headers=[(b'authorization',b'Bearer unit'),(b'x-tenant-id',str(db.tenant).encode())]))
    await anext(flow)
    await flow.aclose()
    db.session.rollback.assert_awaited_once()
    db.reset.assert_called_once_with('token')


@pytest.mark.parametrize('path',['/api/v1/storage/tenant','/api/v1/storage/tenant/configure'])
@pytest.mark.asyncio
async def test_configuration_uses_current_owner_tenant(db,path):
    flow=runtime.request_database(request(path))
    await anext(flow)
    db.admin.assert_awaited_once()
    db.verify.assert_not_called()
    db.guc.assert_awaited_once_with(db.session,db.tenant)
    with pytest.raises(ValueError): await flow.athrow(ValueError('unit'))
    db.session.rollback.assert_awaited_once()
    db.session.commit.assert_not_awaited()
    db.reset.assert_called_once_with('token')


@pytest.mark.asyncio
async def test_revoked_owner_never_opens_database(db):
    db.admin.side_effect=HTTPException(403,'Denied')
    with pytest.raises(HTTPException): await anext(runtime.request_database(request('/api/v1/storage/tenant')))
    db.maker.assert_not_called()


@pytest.mark.asyncio
async def test_health_hides_database_details(db):
    assert await runtime.health()=={'status':'ok'}
    db.session.execute.side_effect=RuntimeError('private database details')
    with pytest.raises(HTTPException) as caught: await runtime.health()
    assert caught.value.status_code==503
    assert 'private' not in caught.value.detail


@pytest.mark.asyncio
async def test_startup_only_installs_session_and_disposes_engine(monkeypatch):
    engine=SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(runtime,'create_async_engine',Mock(return_value=engine))
    monkeypatch.setattr(runtime,'async_sessionmaker',Mock(return_value='maker'))
    configured=Mock()
    monkeypatch.setattr(runtime,'configure_session_maker',configured)
    async with runtime.startup(runtime.app): configured.assert_called_once_with('maker')
    engine.dispose.assert_awaited_once()


def test_only_storage_and_health_routes_are_exposed():
    paths=set(runtime.app.openapi()['paths'])
    assert paths=={'/health','/api/v1/storage/objects/{object_id}','/api/v1/storage/tenant','/api/v1/storage/tenant/configure','/api/v1/storage/tenant/{storage_id}/reset'}
