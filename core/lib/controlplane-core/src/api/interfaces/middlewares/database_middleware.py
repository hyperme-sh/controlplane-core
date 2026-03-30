from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import Depends, Request
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from api.common.utils import get_host_main_domain_name, get_logger, is_subdomain, is_tenancy_enabled
from api.core.config import settings
from api.core.container import get_tenant_service
from api.core.exceptions import TenantNotFoundException
from api.infrastructure.persistence.db_session import (
    configure_session_maker,
    get_session_maker,
    reset_db_session,
    set_db_session,
)

logger = get_logger(__name__)


def get_tenant_host(request: Request) -> Optional[str]:
    host = (
        request.headers.get(":authority")
        or request.headers.get("host")
        or request.url.hostname
    )
    if not host:
        return None
    host = host.split(":")[0].lower()
    main_domain = get_host_main_domain_name().lower()
    if host == main_domain:
        return None
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return None
    logger.debug(f"Extracted host: {host}")
    return host


class DbSessionMiddleware(BaseHTTPMiddleware):
    """Opens one AsyncSession per request, sets RLS GUC app.current_tenant_id, commits on success."""

    async def dispatch(self, request: Request, call_next):
        maker = get_session_maker()
        async with maker() as session:
            token = set_db_session(session)
            try:
                await session.execute(text("SET LOCAL app.current_tenant_id = ''"))

                tenant_uuid: uuid.UUID | None = None
                tenant_host = get_tenant_host(request) if is_tenancy_enabled() else None
                tenant_service = get_tenant_service()
                if tenant_host:
                    try:
                        if is_subdomain(tenant_host):
                            tenant = await tenant_service.find_by_subdomain(tenant_host)
                        else:
                            tenant = await tenant_service.find_by_custom_domain(tenant_host)
                        tenant_uuid = tenant.id  # type: ignore[assignment]
                        request.state.frontend_host = tenant_host
                    except TenantNotFoundException as e:
                        logger.warning(f"Tenant resolve failed for host '{tenant_host}': {e}")

                if tenant_uuid is None and is_tenancy_enabled():
                    tid = request.headers.get("X-Tenant-ID") or request.query_params.get("tenant_id")
                    if tid:
                        try:
                            tenant_uuid = uuid.UUID(str(tid))
                        except ValueError:
                            logger.warning(f"Invalid tenant id header/param: {tid}")

                if tenant_uuid is not None:
                    await session.execute(
                        text("SET LOCAL app.current_tenant_id = :tid"),
                        {"tid": str(tenant_uuid)},
                    )
                    request.state.tenant_id = tenant_uuid
                else:
                    request.state.tenant_id = None

                response = await call_next(request)
                await session.commit()
                return response
            except Exception:
                await session.rollback()
                raise
            finally:
                reset_db_session(token)


def get_tenant_id(request: Request) -> uuid.UUID | None:
    return getattr(request.state, "tenant_id", None)


def frontend_dynamic_host(request: Request) -> str:
    return getattr(request.state, "frontend_host", get_host_main_domain_name())


FrontendHost = Annotated[str, Depends(frontend_dynamic_host)]
