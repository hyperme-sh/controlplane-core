"""Explicit PostgreSQL allocation consumer; no schema creation or route activation."""
from contextlib import asynccontextmanager
from psycopg import sql
from psycopg.rows import dict_row

from api.domain.media_allocation_grant import AllocationRejected, AllocationScope, _integer, _uuid
from api.domain.media_allocation_store import MAX_ALLOCATIONS, Reservation
from api.domain.tenant_records import RecordConflict


class PostgresAllocationStore:
    """Keep permanent object bindings while charging only unfinished allocations.

    connect supplies a fresh, restricted async connection context manager. The
    caller independently authenticates the source before reserve and rechecks in
    before_write. Installation/import and source/credential cutover are explicit
    operator work; a missing schema or tenant never creates an empty registry.
    """
    def __init__(self, tenant, issuer, connect, *, schema="storage_owner"):
        import re
        self.tenant, self.issuer = _uuid(tenant), _uuid(issuer)
        if type(schema) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ValueError("Invalid storage schema")
        self.schema, self.connect = schema, connect

    @asynccontextmanager
    async def _locked(self):
        async with self.connect() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", (self.tenant,))
                    await cursor.execute(sql.SQL("SELECT id FROM {}.tenants WHERE id=%s FOR UPDATE").format(sql.Identifier(self.schema)), (self.tenant,))
                    if await cursor.fetchone() is None:
                        raise AllocationRejected("Allocation tenant unavailable")
                    yield cursor

    def _table(self):
        return sql.Identifier(self.schema, "storage_media_objects")

    async def reserve(self, candidate: Reservation, *, now, before_write):
        _integer(now)
        candidate.validate()
        if candidate.scope.tenant != self.tenant or candidate.scope.issuer != self.issuer:
            raise AllocationRejected("Allocation registry scope mismatch")
        if not candidate.issued_at <= now < candidate.expires_at:
            raise AllocationRejected("Reservation not current")
        async with self._locked() as cursor:
            await cursor.execute(sql.SQL("SELECT * FROM {} WHERE tenant_id=%s AND object_id=%s FOR UPDATE").format(self._table()), (self.tenant, candidate.scope.object))
            row = await cursor.fetchone()
            if row is not None:
                scope = AllocationScope(str(row["issuer_id"]), str(row["tenant_id"]), row["object_id"], row["owner_principal"], str(row["source_id"]), row["policy_digest"], row["ownership_digest"])
                original = Reservation(scope, str(row["allocation_id"]), row["issued_at"], row["expires_at"])
                original.validate()
                if scope != candidate.scope:
                    raise RecordConflict("Object already allocated to another scope")
                if row["phase"] != 0:
                    raise RecordConflict("Object retirement is permanent")
                before_write()
                return original
            await cursor.execute(sql.SQL("SELECT 1 FROM {} WHERE tenant_id=%s AND allocation_id=%s").format(self._table()), (self.tenant, candidate.allocation))
            if await cursor.fetchone() is not None:
                raise RecordConflict("Allocation identifier already used")
            await cursor.execute(sql.SQL("SELECT count(*) AS count FROM {} WHERE tenant_id=%s AND phase<>2").format(self._table()), (self.tenant,))
            if (await cursor.fetchone())["count"] >= MAX_ALLOCATIONS:
                raise AllocationRejected("Allocation quota exceeded")
            before_write()
            scope = candidate.scope
            await cursor.execute(sql.SQL("INSERT INTO {} (tenant_id,object_id,owner_principal,source_id,allocation_id,issuer_id,policy_digest,ownership_digest,issued_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)").format(self._table()), (self.tenant, scope.object, scope.owner, scope.source, candidate.allocation, self.issuer, scope.policy_digest, scope.ownership_digest, candidate.issued_at, candidate.expires_at))
        return candidate
