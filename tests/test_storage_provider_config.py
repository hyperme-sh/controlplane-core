"""Deployment configuration unit contracts; no database or provider connections."""
import base64
from contextlib import contextmanager
import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from api.storage import configure_provider as module

TENANT = "43bb14cb-bcc2-4a45-8ca0-2b1f85ac817a"


def config():
    key = base64.b64encode(b"a" * 64).decode()
    return {"version": 1, "tenant_id": TENANT, "network": "s3r.ch", "provider": "azure",
        "account": "privatetenant", "container": "objects",
        "connection_string": f"DefaultEndpointsProtocol=https;AccountName=privatetenant;AccountKey={key};EndpointSuffix=core.windows.net"}


def desired():
    return module.validate(config(), TENANT, "s3r.ch")


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("tenant_id", "00000000-0000-0000-0000-000000000000"),
    ("network", "other"), ("provider", "s3"), ("account", "different"), ("account", "a.b"),
    ("container", "a--b"), ("container", "a/b"), ("container", None), ("connection_string", None),
])
def test_binding_and_closed_schema(field, value):
    data = config()
    data[field] = value
    with pytest.raises(ValueError):
        module.validate(data, TENANT, "s3r.ch")


@pytest.mark.parametrize("replacement", [
    "DefaultEndpointsProtocol=http", "AccountName=evil", "AccountKey=bad!", "AccountKey=YQ==",
    "EndpointSuffix=example.com", "AccountName=privatetenant;AccountName=privatetenant",
])
def test_connection_rejects_endpoint_and_credential_ambiguity(replacement):
    data = config()
    key = replacement.split("=", 1)[0]
    data["connection_string"] = ";".join(replacement if part.startswith(key + "=") else part
        for part in data["connection_string"].split(";"))
    with pytest.raises(ValueError):
        module.validate(data, TENANT, "s3r.ch")


def test_connection_rejects_sas_and_endpoint_overrides():
    for suffix in (";BlobEndpoint=https://example.com", ";SharedAccessSignature=x", ";AccountKey=", ";broken"):
        data = config()
        data["connection_string"] += suffix
        with pytest.raises(ValueError):
            module.validate(data, TENANT, "s3r.ch")


def test_normalization_and_initial_idempotency():
    data = config()
    data["connection_string"] = ";".join(reversed(data["connection_string"].split(";"))) + ";"
    value = module.validate(data, TENANT, "s3r.ch")
    assert value == desired()
    assert module.decide([], value, None) == "insert"
    row = {**value, "id": UUID(TENANT), "tenant_id": UUID(TENANT)}
    assert module.decide([row], value, None) == "unchanged"
    assert module.fingerprint(row) == module.fingerprint(value)


def test_rotation_requires_exact_previous_and_preserves_namespace():
    old = desired()
    new = {**old, "azure_connection_string": old["azure_connection_string"].replace(
        base64.b64encode(b"a" * 64).decode(), base64.b64encode(b"b" * 64).decode())}
    with pytest.raises(ValueError):
        module.decide([old], new, None)
    with pytest.raises(ValueError):
        module.decide([old], new, "0" * 64)
    assert module.decide([old], new, module.fingerprint(old)) == "update"
    for field, value in (("azure_container_name", "other"),
            ("azure_connection_string", new["azure_connection_string"].replace("privatetenant", "otheraccount"))):
        with pytest.raises(ValueError):
            module.decide([old], {**new, field: value}, module.fingerprint(old))


@pytest.mark.parametrize("change", [
    {"updated_by_user_id": "admin"}, {"provider": "s3"}, {"is_enabled": False},
    {"tenant_id": "ce48ba5c-5ba9-4135-b7c5-d564e5dacdaf"},
])
def test_never_overwrites_tenant_admin_state(change):
    row = {**desired(), **change}
    with pytest.raises(ValueError):
        module.decide([row], desired(), module.fingerprint(row))


def test_missing_and_multiple_configurations_refuse():
    for rows, previous in (([], "0" * 64), ([desired(), desired()], None), ([], "bad")):
        with pytest.raises(ValueError):
            module.decide(rows, desired(), previous)


def test_private_file_and_size_limits(tmp_path):
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(config()))
    path.chmod(0o600)
    assert module.private_json(str(path)) == config()
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        module.private_json(str(link))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        module.private_json(str(path))
    path.chmod(0o400)
    assert module.private_json(str(path)) == config()
    path.chmod(0o600)
    for content in ('{"version":1,"version":1}', '[]', ' ' * 8193):
        path.write_text(content)
        with pytest.raises(ValueError):
            module.private_json(str(path))


class Connection:
    def __init__(self, rows=None):
        self.role = {"schema": "storage_owner", "rolsuper": False, "rolbypassrls": False,
            "rolcreaterole": False, "rolcreatedb": False, "rolreplication": False, "can_create": False}
        self.tables = [{"relname": name, "relrowsecurity": True, "relforcerowsecurity": True,
            "owns_table": False} for name in ("tenants", "storage_settings")]
        self.tenant = {"id": TENANT}
        self.rows = rows or []
        self.commands = []
        self.committed = False
        self.rowcount = 1

    @contextmanager
    def transaction(self):
        yield
        self.committed = True

    @contextmanager
    def cursor(self, **kwargs):
        yield self

    def execute(self, query, params=None):
        self.commands.append((query if isinstance(query, str) else query.as_string(), params))

    def fetchone(self):
        return self.role if "pg_roles" in self.commands[-1][0] else self.tenant

    def fetchall(self):
        return self.tables if "pg_class" in self.commands[-1][0] else self.rows


def test_insert_locks_existing_tenant_and_checks_private_provider_before_writing():
    conn = Connection()
    async def probe(value):
        assert not any("INSERT" in query for query, _ in conn.commands)
        assert conn.commands[-1][0].endswith("FOR UPDATE")
        assert value == desired()
    result = module.configure(conn, desired(), None, probe)
    assert result == {"changed": True, "sha256": module.fingerprint(desired())}
    assert conn.committed
    assert conn.commands[4][1] == (TENANT,)
    assert "INSERT INTO storage_owner.storage_settings" in conn.commands[-1][0]
    assert not any("INSERT INTO storage_owner.tenants" in query for query, _ in conn.commands)


def test_provider_failure_rolls_back_without_writes():
    conn = Connection()
    with pytest.raises(RuntimeError):
        module.configure(conn, desired(), None, AsyncMock(side_effect=RuntimeError("provider error")))
    assert not conn.committed
    assert not any("INSERT" in query or "UPDATE storage" in query for query, _ in conn.commands)


@pytest.mark.parametrize("attribute", ["rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication", "can_create", "schema"])
def test_privileged_or_wrong_schema_role_refused_before_provider(attribute):
    conn = Connection()
    conn.role[attribute] = "public" if attribute == "schema" else True
    probe = AsyncMock()
    with pytest.raises(ValueError):
        module.configure(conn, desired(), None, probe)
    probe.assert_not_called()
    assert not conn.committed


@pytest.mark.parametrize("attribute,value", [("relrowsecurity", False), ("relforcerowsecurity", False), ("owns_table", True)])
def test_schema_isolation_required(attribute, value):
    conn = Connection()
    conn.tables[0][attribute] = value
    with pytest.raises(ValueError):
        module.configure(conn, desired(), None, AsyncMock())
    assert not conn.committed


def test_missing_declared_tenant_is_not_created():
    conn = Connection()
    conn.tenant = None
    with pytest.raises(ValueError):
        module.configure(conn, desired(), None, AsyncMock())
    assert not conn.committed


def test_unchanged_does_not_write_and_rotation_updates_one_row():
    row = {**desired(), "id": UUID(TENANT)}
    conn = Connection([row])
    assert module.configure(conn, desired(), None, AsyncMock())["changed"] is False
    assert not any("UPDATE storage" in query for query, _ in conn.commands)
    old = {**row, "azure_connection_string": row["azure_connection_string"].replace(
        base64.b64encode(b"a" * 64).decode(), base64.b64encode(b"b" * 64).decode())}
    conn = Connection([old])
    assert module.configure(conn, desired(), module.fingerprint(old), AsyncMock())["changed"] is True
    assert "updated_at=now() WHERE id=%s AND tenant_id=%s" in conn.commands[-1][0]


def test_cli_does_not_log_secret_errors(monkeypatch, tmp_path, capsys):
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(config()))
    path.chmod(0o600)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@example/db")
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret")))
    assert module.main(["--provider-file", str(path), "--tenant", TENANT, "--network", "s3r.ch"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Tenant provider configuration failed\n"
