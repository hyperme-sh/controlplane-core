"""Narrow Spanopticon tenant storage process; no monolith startup or seeding."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from controlplane_api.exceptions import ApiBaseException, NotFoundException, ConflictException, UnauthorizedException, InvalidOperationException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from controlplane_api.config import settings
from controlplane_api.version import router as version_router
from controlplane_db.session import normalize_database_url
from api.infrastructure.persistence.db_session import (
    configure_session_maker, get_session_maker, reset_db_session, set_db_session,
)
from api.infrastructure.persistence.tenant_guc import set_current_tenant_id_guc
from api.infrastructure.security.storage_admin import storage_admin
from api.infrastructure.security.storage_session import storage_session, request_operation
from api.interfaces.api_controllers.tenant_objects_endpoint import router as objects
from api.interfaces.api_controllers.tenant_storage_admin_endpoint import router as administration
from api.interfaces.api_controllers.tenant_records_endpoint import router as records
from api.interfaces.api_controllers.tenant_replays_endpoint import router as replays
from api.interfaces.api_controllers.media_allocation_endpoint import router as allocations
from api.infrastructure.security.media_allocation_settings import load_issuers
from api.infrastructure.security.group_allocation_settings import load_issuers as load_group_issuers
from api.interfaces.api_controllers.group_payload_endpoint import router as group_payloads, group_payload_capacity
from api.interfaces.api_controllers.group_allocation_endpoint import router as group_allocations, group_allocation_capacity
from api.interfaces.api_controllers.media_retirement_endpoint import router as retirement
from api.interfaces.api_controllers.media_payload_endpoint import router as payloads
from api.interfaces.api_controllers.media_observation_endpoint import router as observations, observation_capacity


@asynccontextmanager
async def startup(app: FastAPI):
    app.state.media_allocation_issuers = load_issuers()
    app.state.group_allocation_issuers = load_group_issuers()
    engine = create_async_engine(normalize_database_url(settings.database_url))
    configure_session_maker(async_sessionmaker(engine, expire_on_commit=False, autoflush=False))
    try:
        yield
    finally:
        await engine.dispose()


async def request_database(request: Request):
    # Authenticate before opening a database session. Neither Host nor a query
    # parameter may select the RLS tenant for these routes.
    if request.url.path == '/api/v1/storage/tenant' or request.url.path.startswith('/api/v1/storage/tenant/'):
        tenant = (await storage_admin(request)).tenant
    else:
        auth = request.headers.get('authorization', '')
        if len(request.headers.getlist('authorization')) != 1 or not auth.startswith('Bearer ') or auth.count(' ') != 1:
            raise HTTPException(401, 'Storage authentication required')
        operation = request_operation(request.method, request.url.path)
        tenant = storage_session(auth[7:], operation)
        supplied = request.headers.getlist('x-tenant-id')
        if supplied and supplied != [str(tenant)]:
            raise HTTPException(403, 'Storage tenant mismatch')
    async with get_session_maker()() as session:
        token = set_db_session(session)
        try:
            await set_current_tenant_id_guc(session, tenant)
            yield
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            reset_db_session(token)


app = FastAPI(title='Spanopticon tenant storage', lifespan=startup, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(version_router)
app.include_router(objects, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])
app.include_router(administration, prefix='/api/v1', dependencies=[Depends(request_database, scope='function')])
app.include_router(records, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])
app.include_router(replays, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])
app.include_router(allocations, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])
app.include_router(group_allocations, prefix='/api/v1', dependencies=[Depends(group_allocation_capacity, scope='request'), Depends(request_database, scope='request')])
app.include_router(retirement, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])


@app.exception_handler(ApiBaseException)
async def storage_error(request: Request, error: ApiBaseException):
    status, detail = 400, 'Invalid storage request'
    for kind, code, message in [(NotFoundException, 404, 'Storage not found'), (ConflictException, 409, 'Storage conflict'),
                               (UnauthorizedException, 401, 'Storage authentication required'), (InvalidOperationException, 406, 'Invalid storage operation')]:
        if isinstance(error, kind):
            status, detail = code, message
            break
    return JSONResponse(status_code=status, content={'detail': detail})


@app.get('/health')
async def health():
    try:
        async with get_session_maker()() as session:
            await session.execute(text('SELECT 1'))
    except Exception:
        raise HTTPException(503, 'Storage database unavailable') from None
    return {'status': 'ok'}

app.include_router(observations, prefix='/api/v1', dependencies=[Depends(observation_capacity, scope='request'), Depends(request_database, scope='request')])
app.include_router(payloads, prefix='/api/v1', dependencies=[Depends(request_database, scope='request')])

app.include_router(group_payloads, prefix="/api/v1", dependencies=[Depends(group_payload_capacity, scope="request"), Depends(request_database, scope="request")])
