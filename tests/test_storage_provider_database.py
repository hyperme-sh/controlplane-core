"""CI-only disposable PostgreSQL contract for deployment configuration."""
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from controlplane_db.models import StorageSettings

from api.storage.configure_provider import configure, fingerprint


@pytest.fixture
def isolated_storage():
    base = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql+psycopg")
    name = "storage_config_" + uuid4().hex
    owner, runtime = name + "_owner", name + "_runtime"
    admin_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(runtime)))
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            target = base.set(database=name)
            db_url = target.set(drivername="postgresql").render_as_string(hide_password=False)
            with psycopg.connect(db_url, autocommit=True) as setup:
                setup.execute(sql.SQL("CREATE SCHEMA storage_owner AUTHORIZATION {}").format(sql.Identifier(owner)))
                setup.execute("CREATE TABLE storage_owner.tenants(id uuid PRIMARY KEY)")
            engine = create_engine(target, connect_args={"options": "-csearch_path=storage_owner"})
            try:
                StorageSettings.__table__.create(engine)
            finally:
                engine.dispose()
            tenant, foreign = uuid4(), uuid4()
            with psycopg.connect(db_url, autocommit=True) as setup:
                setup.execute("INSERT INTO storage_owner.tenants VALUES (%s),(%s)", (tenant, foreign))
                for table, field in (("tenants", "id"), ("storage_settings", "tenant_id")):
                    setup.execute(sql.SQL("ALTER TABLE storage_owner.{} OWNER TO {}").format(sql.Identifier(table), sql.Identifier(owner)))
                    setup.execute(sql.SQL("ALTER TABLE storage_owner.{} ENABLE ROW LEVEL SECURITY").format(sql.Identifier(table)))
                    setup.execute(sql.SQL("ALTER TABLE storage_owner.{} FORCE ROW LEVEL SECURITY").format(sql.Identifier(table)))
                    setup.execute(sql.SQL("CREATE POLICY tenant_scope ON storage_owner.{} USING ({} = NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)").format(sql.Identifier(table), sql.Identifier(field)))
                setup.execute(sql.SQL("GRANT USAGE ON SCHEMA storage_owner TO {}").format(sql.Identifier(runtime)))
                setup.execute(sql.SQL("GRANT SELECT,UPDATE(id) ON storage_owner.tenants TO {}").format(sql.Identifier(runtime)))
                setup.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON storage_owner.storage_settings TO {}").format(sql.Identifier(runtime)))
            with psycopg.connect(db_url, autocommit=True) as connection:
                connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(runtime)))
                connection.execute("SET search_path=storage_owner")
                yield connection, tenant, foreign
        finally:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))


def test_restricted_provider_config_and_cross_tenant_isolation(isolated_storage):
    connection, tenant, foreign = isolated_storage
    desired = {"tenant_id": str(tenant), "provider": "azure_blob", "is_enabled": True, "region": "",
        "aws_access_key": None, "aws_secret_key": None, "aws_bucket_name": None,
        "azure_connection_string": "AccountName=private;AccountKey=fixture", "azure_container_name": "objects",
        "updated_by_user_id": None}
    assert configure(connection, desired, None, AsyncMock())["changed"] is True
    assert configure(connection, desired, None, AsyncMock())["changed"] is False
    assert connection.execute("SELECT id FROM storage_owner.storage_settings").fetchall() == []
    with connection.transaction():
        connection.execute("SELECT set_config('app.current_tenant_id',%s,true)", (str(foreign),))
        assert connection.execute("SELECT id FROM storage_owner.storage_settings").fetchall() == []
    rotated = {**desired, "azure_connection_string": "AccountName=private;AccountKey=rotated"}
    with pytest.raises(ValueError, match="has changed"):
        configure(connection, rotated, "0" * 64, AsyncMock())
    assert configure(connection, rotated, fingerprint(desired), AsyncMock())["changed"] is True
    with connection.transaction():
        connection.execute("SELECT set_config('app.current_tenant_id',%s,true)", (str(tenant),))
        connection.execute("UPDATE storage_owner.storage_settings SET updated_by_user_id='tenant-admin'")
    with pytest.raises(ValueError, match="not deployment-owned"):
        configure(connection, desired, fingerprint(rotated), AsyncMock())
    with pytest.raises(ValueError, match="missing"):
        configure(connection, {**desired, "tenant_id": str(uuid4())}, None, AsyncMock())
