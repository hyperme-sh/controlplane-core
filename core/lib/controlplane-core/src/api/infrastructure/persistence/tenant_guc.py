"""Set Postgres session GUC for row-level security: app.current_tenant_id.

Middleware runs this on each request's AsyncSession before handlers. Empty string
means platform scope (policies allow tenant_id IS NULL rows where applicable).
Auth dependencies (e.g. require_matched_tenant) align JWT tenant with X-Tenant-ID;
they do not replace this call — the GUC must be set on the DB session used by repositories.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_current_tenant_id_guc(
    session: AsyncSession,
    tenant_id: uuid.UUID | None,
) -> None:
    """Apply tenant context for RLS policies on this session."""
    if tenant_id is None:
        await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
