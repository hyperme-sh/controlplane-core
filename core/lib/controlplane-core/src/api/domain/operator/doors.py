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


def _sql_banned(tenant_id: uuid.UUID, subject_kind: str, subject_id: uuid.UUID) -> bool:
    url = _database_url()
    if not _sql_usable(url):
        return False
    global _engine
    from sqlalchemy import create_engine, text

    if _engine is None:
        sync = url.replace("+asyncpg", "+psycopg")
        _engine = create_engine(sync, pool_pre_ping=True)
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
