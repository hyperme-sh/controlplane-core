"""Consumer of controlplane-db's explicit stream schema; no DDL/bootstrap."""
from contextlib import asynccontextmanager
import re
from uuid import UUID, uuid4
from psycopg import sql
from psycopg.rows import dict_row
from .contracts import BYTES_PER_SECOND, Refused, Scope, Subject, uuid


class PostgresStreams:
    def __init__(self, tenant, connect, *, schema='storage_owner'):
        self.tenant, self.connect = uuid(tenant), connect
        if type(schema) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
            raise Refused('Invalid stream schema')
        self.schema = schema

    def table(self, name):
        return sql.Identifier(self.schema, name)

    @asynccontextmanager
    async def locked(self):
        async with self.connect() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("SET LOCAL statement_timeout='200ms'")
                    await cursor.execute("SET LOCAL lock_timeout='100ms'")
                    await cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", (self.tenant,))
                    await cursor.execute(sql.SQL('SELECT id FROM {} WHERE id=%s FOR UPDATE').format(self.table('tenants')), (self.tenant,))
                    if await cursor.fetchone() is None: raise Refused('Stream tenant unavailable')
                    yield cursor

    @staticmethod
    def scope(row):
        return Scope(Subject(str(row['tenant_id']), row['principal']), row['room'], row['action'], str(row['source_ip']))

    async def policy(self, cursor, generation):
        await cursor.execute(sql.SQL('SELECT enabled,generation,daily_bytes FROM {} WHERE tenant_id=%s').format(self.table('stream_policies')), (self.tenant,))
        row = await cursor.fetchone()
        if (row is None or not row['enabled'] or str(row['generation']) != generation
                or not 391000 <= row['daily_bytes'] <= 32_000_000_000):
            raise Refused('Stream policy unavailable')

    async def reserve(self, scope, generation, reservation, token_hash, now, deadline):
        if scope.subject.tenant != self.tenant: raise Refused('Stream tenant mismatch')
        async with self.locked() as cursor:
            await self.policy(cursor, generation)
            if scope.action == 'publish':
                path = 'stream/' + UUID(self.tenant).hex + '/' + uuid4().hex
            else:
                await cursor.execute(sql.SQL("SELECT path,expires_at FROM {} WHERE tenant_id=%s AND room=%s AND generation=%s AND action='publish' AND phase=1").format(self.table('stream_sessions')), (self.tenant,scope.room,generation))
                publisher = await cursor.fetchone()
                if publisher is None: raise Refused('Publisher unavailable')
                path, deadline = publisher['path'], min(deadline,publisher['expires_at'])
            if deadline <= now: raise Refused('Stream expired')
            await cursor.execute(sql.SQL('INSERT INTO {} (tenant_id,reservation_id,generation,room,principal,action,source_ip,token_hash,path,issued_at,expires_at,reserved_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *').format(self.table('stream_sessions')),
                (self.tenant,reservation,generation,scope.room,scope.subject.principal,scope.action,scope.ip,token_hash,path,now,deadline,(deadline-now)*BYTES_PER_SECOND))
            return await cursor.fetchone()

    async def lookup(self, path, token_hash, generation):
        async with self.locked() as cursor:
            await self.policy(cursor, generation)
            await cursor.execute(sql.SQL('SELECT * FROM {} WHERE tenant_id=%s AND path=%s AND token_hash=%s AND generation=%s AND phase=0').format(self.table('stream_sessions')), (self.tenant,path,token_hash,generation))
            row = await cursor.fetchone()
            if row is None: raise Refused('Stream reservation unavailable')
            return row

    async def consume(self, row, session, now):
        async with self.locked() as cursor:
            await self.policy(cursor, str(row['generation']))
            if row['action'] == 'read':
                # Revalidate the publisher under the same tenant lock as
                # consumption. It may have closed since this grant was issued.
                await cursor.execute(sql.SQL(
                    "SELECT 1 FROM {} WHERE tenant_id=%s AND generation=%s AND path=%s"
                    " AND action='publish' AND phase=1 AND expires_at>%s"
                ).format(self.table('stream_sessions')), (self.tenant, row['generation'], row['path'], now))
                if await cursor.fetchone() is None:
                    raise Refused('Publisher unavailable')
            await cursor.execute(sql.SQL('UPDATE {} SET phase=1,media_session=%s WHERE tenant_id=%s AND reservation_id=%s AND phase=0 AND expires_at>%s AND issued_at>=%s RETURNING reservation_id').format(self.table('stream_sessions')),
                (session,self.tenant,row['reservation_id'],now,now-10))
            if await cursor.fetchone() is None: raise Refused('Stream reservation unavailable')

    async def outstanding(self, generation):
        async with self.locked() as cursor:
            await self.policy(cursor, generation)
            await cursor.execute(sql.SQL('SELECT * FROM {} WHERE tenant_id=%s AND phase<>2').format(self.table('stream_sessions')), (self.tenant,))
            rows = await cursor.fetchall()
            if len(rows)>22: raise Refused('Stream capacity exceeded')
            return rows

    async def close(self, reservation, *, pending_only=False):
        async with self.locked() as cursor:
            await cursor.execute(sql.SQL(
                'UPDATE {} SET phase=2 WHERE tenant_id=%s AND reservation_id=%s AND (NOT %s OR phase=0)'
            ).format(self.table('stream_sessions')), (self.tenant, reservation, pending_only))
