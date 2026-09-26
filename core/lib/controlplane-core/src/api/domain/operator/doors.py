"""Door checks for active operator bans.

Same-process callers see the in-memory book. Other processes read Postgres
when DATABASE_URL is a real server. A dummy test URL does not open a connection.
"""

from __future__ import annotations

import uuid

from api.domain.operator.bans import ban_book

_engine = None


def _database_url() -> str:
    try:
        from api.core.config import settings

        return str(settings.database_url or "")
    except Exception:
        return ""


def _sql_usable(url: str) -> bool:
    if not url or "localhost:1" in url or url.startswith("sqlite"):
        return False
    return True


def _sync_database_url(url: str) -> str:
    """Open the ban door with psycopg, not SQLAlchemy's default psycopg2.

    The VM ``DATABASE_URL`` is a bare ``postgresql://`` URL. The async engine
    rewrites that to ``+asyncpg`` before it connects. This door builds its own
    sync engine. Replacing only ``+asyncpg`` leaves the bare URL alone, and
    ``create_engine`` then imports ``psycopg2``, which this image does not
    install. Lease create catches that as ``ModuleNotFoundError``.
    """

    if url.startswith("postgresql+psycopg://"):
        return url
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+psycopg2://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def _sql_banned(tenant_id: uuid.UUID, subject_kind: str, subject_id: uuid.UUID) -> bool:
    url = _database_url()
    if not _sql_usable(url):
        return False
    global _engine
    from sqlalchemy import create_engine, text

    if _engine is None:
        _engine = create_engine(_sync_database_url(url), pool_pre_ping=True)
    statement = text(
        """
        SELECT 1
        FROM operator_bans
        WHERE tenant_id = :tenant_id
          AND subject_kind = :subject_kind
          AND subject_id = :subject_id
          AND lifted_at IS NULL
        """
    )
    try:
        with _engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = conn.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                },
            ).first()
            return row is not None
    except Exception:
        # A ban the door cannot read is not a ban. Fail closed.
        return True


def subject_banned(
    tenant_id: uuid.UUID,
    subject_kind: str,
    subject_id: uuid.UUID,
) -> bool:
    if ban_book().active_row(tenant_id, subject_kind, subject_id) is not None:
        return True
    return _sql_banned(tenant_id, subject_kind, subject_id)
