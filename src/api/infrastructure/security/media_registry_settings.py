"""Explicit private PostgreSQL registry configuration; never installs state."""
from contextlib import asynccontextmanager
from dataclasses import dataclass,field

from sqlalchemy.engine import make_url
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from api.domain.media_allocation_grant import _uuid
from api.infrastructure.persistence.media_allocation_postgres import PostgresAllocationStore
from api.storage.configure_provider import private_json


@dataclass(frozen=True)
class RegistrySettings:
    database_url: str=field(repr=False)
    schema: str
    tenant: str
    issuer: str

    def store(self, connect=None, *, require_locations=False):
        @asynccontextmanager
        async def raw_connection():
            url=make_url(self.database_url).set(drivername='postgresql').render_as_string(hide_password=False)
            async with await AsyncConnection.connect(url,autocommit=True,connect_timeout=4) as connection:
                yield connection
        @asynccontextmanager
        async def restricted():
            async with (connect or raw_connection)() as connection:
                async with connection.transaction():
                    async with connection.cursor(row_factory=dict_row) as cursor:
                        await cursor.execute("SET LOCAL search_path=pg_catalog,pg_temp")
                        await cursor.execute("SET LOCAL statement_timeout='3s'")
                        await cursor.execute("SET LOCAL lock_timeout='2s'")
                        await cursor.execute("SELECT r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,r.rolreplication,has_schema_privilege(%s,'CREATE') AS can_create FROM pg_roles r WHERE r.rolname=current_user",(self.schema,))
                        role=await cursor.fetchone()
                        if not role or any(role.values()):raise ValueError('Restricted media runtime role required')
                        names=['tenants','storage_media_objects','storage_media_refs']
                        if require_locations:names.append('storage_media_locations')
                        await cursor.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,pg_has_role(c.relowner,'MEMBER') AS owns_table FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind='r'",(self.schema,names))
                        tables=await cursor.fetchall()
                        if len(tables)!=len(names) or any(not row['relrowsecurity'] or not row['relforcerowsecurity'] or row['owns_table'] for row in tables):
                            raise ValueError('Forced media tenant isolation required')
                    yield connection
        return PostgresAllocationStore(self.tenant,self.issuer,restricted,schema=self.schema)


def read_registry(path,tenant,issuer):
    value=private_json(path)
    if (set(value)!={'version','tenant','issuer','schema','databaseUrl'} or type(value['version']) is not int
            or value['version']!=1 or value['tenant']!=tenant or value['issuer']!=issuer):
        raise ValueError('Invalid media registry binding')
    _uuid(tenant);_uuid(issuer)
    if type(value['databaseUrl']) is not str:raise ValueError('Invalid media database address')
    url=make_url(value['databaseUrl'])
    if url.drivername!='postgresql+psycopg' or not url.host or not url.username or not url.database:
        raise ValueError('Explicit media database address required')
    settings=RegistrySettings(value['databaseUrl'],value['schema'],tenant,issuer)
    settings.store() # Structural checks only; no database connection.
    return settings
