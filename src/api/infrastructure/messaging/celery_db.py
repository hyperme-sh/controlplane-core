"""Async DB session + RLS GUC for Celery tasks (no HTTP middleware)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from controlplane_api.config import settings
from api.infrastructure.persistence.db_session import (
    configure_session_maker,
    reset_db_session,
    set_db_session,
)
from controlplane_db.session import normalize_database_url

T = TypeVar("T")

_engine: AsyncEngine | None = None


def _engine_singleton() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            normalize_database_url(settings.database_url), poolclass=NullPool
        )
    return _engine


async def run_with_db_session(
    tenant_id: str | None,
    work: Callable[[], Awaitable[T]],
) -> T:
    """One transaction: set_config(app.current_tenant_id, ...) for RLS, then await work()."""
    maker = async_sessionmaker(
        _engine_singleton(), expire_on_commit=False, autoflush=False
    )
    configure_session_maker(maker)
    async with maker() as session:
        token = set_db_session(session)
        try:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id) if tenant_id else ""},
            )
            out = await work()
            await session.commit()
            return out
        except Exception:
            await session.rollback()
            raise
        finally:
            reset_db_session(token)
