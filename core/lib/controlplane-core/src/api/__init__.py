import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, APIRouter, Request, status

from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from api.common.exceptions import ApiBaseException, ConflictException, InvalidOperationException, NotFoundException, UnauthorizedException
from api.common.utils import get_logger, is_tenancy_enabled
from api.core.container import container
from api.core.exceptions import InvalidSubdomainException, TenantNotFoundException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from controlplane_db.session import normalize_database_url
from api.infrastructure.persistence.db_session import configure_session_maker
from api.interfaces.middlewares.audit_logs_read_middleware import AuditLogsReadMiddleware
from api.interfaces.middlewares.database_middleware import DbSessionMiddleware
from api.interfaces.middlewares.redis_cache_middleware import RedisCacheMiddleware
from api.interfaces.api_controllers.account_endpoint import router as account_router
from api.interfaces.api_controllers.tenant_endpoint import router as tenant_router
from api.interfaces.api_controllers.role_endpoint import router as role_router
from api.interfaces.api_controllers.app_configuration_endpoint import router as app_configuration_router
from api.interfaces.api_controllers.storage_endpoint import router as storage_router
from api.interfaces.api_controllers.permissions_endpoint import router as permissions_router
from api.interfaces.api_controllers.dashboard_endpoint import router as dashboard_router
from api.interfaces.api_controllers.ai_endpoint import router as ai_router
from api.interfaces.api_controllers.health_endpoint import router as health_router
from api.interfaces.api_controllers.manage_security_endpoint import router as manage_security_router
from api.interfaces.api_controllers.features_endpoint import router as features_router
from api.interfaces.api_controllers.product_endpoint import router as product_router
from api.interfaces.api_controllers.prices_endpoint import router as prices_router
from api.interfaces.api_controllers.billing_endpoint import router as billing_router
from api.interfaces.api_controllers.stripe_endpoint import router as stripe_router
from api.interfaces.api_controllers.invoice_endpoint import router as invoice_router
from api.interfaces.api_controllers.checkout_endpoint import router as checkout_router
from api.interfaces.api_controllers.notification_endpoint import router as notification_router
from api.interfaces.api_controllers.audit_logs_endpoint import router as audit_logs_router
from api.interfaces.api_controllers.sso_settings_endpoint import router as sso_router
from api.interfaces.api_controllers.branding_endpoint import router as branding_router

from api.common.logging import configure_logging
from api.core.config import settings
from api.infrastructure.messaging.tenant_event_publisher import (
    install_tenant_event_publisher,
    shutdown_tenant_event_publisher,
)
from api.infrastructure.persistence.seeder import seed_initial_data

build_path = os.path.join(os.path.dirname(__file__), "ui")

_async_engine = None

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _async_engine
    configure_logging()
    _async_engine = create_async_engine(normalize_database_url(settings.database_url))
    maker = async_sessionmaker(_async_engine, expire_on_commit=False, autoflush=False)
    configure_session_maker(maker)
    await seed_initial_data()

    await install_tenant_event_publisher(service_name=settings.service_name)

    async with AsyncExitStack() as stack:
        try:
            from controlplane_edge.grpc_server import grpc_server_lifespan
            from controlplane_edge.edge_settings import GatewayCrossCuttingSettings

            gs = GatewayCrossCuttingSettings()
            await stack.enter_async_context(
                grpc_server_lifespan(
                    host=gs.grpc_host,
                    port=gs.grpc_port,
                    enabled=gs.grpc_enabled,
                )
            )
        except ImportError:
            logger.debug("controlplane_edge.grpc_server not available; skipping gRPC")

        yield

    await shutdown_tenant_event_publisher()
    if _async_engine is not None:
        await _async_engine.dispose()


app = FastAPI(
    lifespan=lifespan,
    title="Panopticon Control Plane API",
    description="Multi-tenant FastAPI backend (PostgreSQL + RLS).",
    version="1.0.0",
)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description or "",
        routes=app.routes,
    )
    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        "BearerKeycloak"
    ] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Keycloak access token (realm controlplane)",
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.exception_handler(ApiBaseException)
async def api_exception_handler(req: Request, ex: ApiBaseException) -> JSONResponse:
    status_code = status.HTTP_400_BAD_REQUEST
    if isinstance(ex, NotFoundException) or isinstance(ex, TenantNotFoundException):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(ex, ConflictException):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(ex, UnauthorizedException):
        status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(ex, InvalidSubdomainException):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(ex, InvalidOperationException):
        status_code = status.HTTP_406_NOT_ACCEPTABLE

    
    logger.error(f"API Exception: {ex.message}")
    return JSONResponse(
        status_code=status_code,
        content={"error": ex.message, "code": status_code}
    )

app.add_middleware(AuditLogsReadMiddleware)
app.add_middleware(RedisCacheMiddleware)
app.add_middleware(DbSessionMiddleware)
if is_tenancy_enabled():
    logger.info("Multi-tenancy is enabled (host/subdomain/header resolution + RLS).")


# Mount static files only if they exist (for production Docker deployment)
if os.path.exists(build_path) and os.path.exists(os.path.join(build_path, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(build_path, "assets")), name="assets")
    logger.info("Static files mounted successfully")
else:
    logger.warning(f"Static files directory not found - running in {settings.fastapi_env} mode")




router = APIRouter(prefix="/api/v1")


router.include_router(health_router)

if is_tenancy_enabled():
    router.include_router(tenant_router)
    router.include_router(features_router)
    router.include_router(branding_router)



router.include_router(app_configuration_router)
router.include_router(account_router)
router.include_router(manage_security_router)
router.include_router(dashboard_router)

# Deprecated monolith /users|/groups|/organizations are no longer mounted.
# The gateway edge proxy forwards those prefixes to user-service.
router.include_router(role_router)
router.include_router(permissions_router)
router.include_router(storage_router)
router.include_router(ai_router)
router.include_router(product_router)
router.include_router(prices_router)
router.include_router(billing_router)
router.include_router(stripe_router)
router.include_router(invoice_router)
router.include_router(checkout_router)
router.include_router(notification_router)
router.include_router(audit_logs_router)
router.include_router(sso_router)


app.include_router(router)

# Gateway: CORS, auth, rate limits, optional OTEL (before OpenAPI/GraphQL mounts).
try:
    from controlplane_edge.register_cross_cutting import register_gateway_cross_cutting

    register_gateway_cross_cutting(app)
except ImportError:
    logger.debug("controlplane_edge.register_cross_cutting not available; skipping gateway middleware")

# Phase 2: aggregated OpenAPI (HTTP merge from microservices). Must register before the catch-all route.
try:
    from controlplane_edge.aggregation import register_aggregation_routes

    register_aggregation_routes(app)
except ImportError:
    logger.debug("gateway package not available; skipping OpenAPI aggregation routes")

try:
    from controlplane_edge.graphql.register import register_graphql_routes

    register_graphql_routes(app)
except ImportError:
    logger.debug("gateway package not available; skipping GraphQL routes")


# Catch-all route for React Router (only in production with static files)
@app.get("/{full_path:path}", include_in_schema=False)
async def react_router(full_path: str):
    if os.path.exists(os.path.join(build_path, "index.html")):
        return FileResponse(os.path.join(build_path, "index.html"))
    else:
        return {"message": "API endpoint not found", "available_docs": "/docs", "api_base": "/api/v1"}

