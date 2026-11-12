"""Configure an explicitly declared tenant using the storage runtime role.

This operator command is separate from the tenant administrator HTTP API. It
never creates tenants, grants privileges, or runs schema migrations.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import stat
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4


def private_json(path: str) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
            raise ValueError("Private owner-readable configuration required")
        data = stream.read(8193)
    if len(data) > 8192:
        raise ValueError("Provider configuration too large")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate provider field")
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("Provider configuration must be an object")
    return result


def validate(value: dict, tenant: str, network: str) -> dict:
    fields = {"version", "tenant_id", "network", "provider", "account", "container", "connection_string"}
    if set(value) != fields or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unknown provider configuration")
    if str(UUID(tenant)) != tenant or UUID(tenant).int == 0 or value["tenant_id"] != tenant:
        raise ValueError("Declared tenant binding required")
    if not network or value["network"] != network or value["provider"] != "azure":
        raise ValueError("Declared Azure network binding required")
    account, container, connection = (value[key] for key in ("account", "container", "connection_string"))
    if not isinstance(account, str) or not re.fullmatch(r"[a-z0-9]{3,24}", account):
        raise ValueError("Invalid Azure account")
    if (not isinstance(container, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])", container)
            or "--" in container):
        raise ValueError("Invalid private container")
    if not isinstance(connection, str) or len(connection) > 4096:
        raise ValueError("Invalid Azure connection")
    parts = {}
    for part in connection.rstrip(";").split(";"):
        key, sep, val = part.partition("=")
        if not sep or key in parts or not val or val != val.strip():
            raise ValueError("Invalid Azure connection")
        parts[key] = val
    if (set(parts) != {"DefaultEndpointsProtocol", "AccountName", "AccountKey", "EndpointSuffix"}
            or parts["DefaultEndpointsProtocol"] != "https" or parts["AccountName"] != account
            or parts["EndpointSuffix"] != "core.windows.net"):
        raise ValueError("Bound public Azure endpoint required")
    try:
        if len(base64.b64decode(parts["AccountKey"], validate=True)) != 64:
            raise ValueError("Invalid Azure key")
    except Exception:
        raise ValueError("Invalid Azure key") from None
    # Normalize field order so equivalent Azure strings are idempotent.
    normalized = ";".join(f"{key}={parts[key]}" for key in
        ("DefaultEndpointsProtocol", "AccountName", "AccountKey", "EndpointSuffix"))
    return {"tenant_id": tenant, "provider": "azure_blob", "is_enabled": True,
        "region": "", "aws_access_key": None, "aws_secret_key": None, "aws_bucket_name": None,
        "azure_connection_string": normalized, "azure_container_name": container, "updated_by_user_id": None}


FIELDS = ("tenant_id", "provider", "is_enabled", "region", "aws_access_key", "aws_secret_key",
    "aws_bucket_name", "azure_connection_string", "azure_container_name", "updated_by_user_id")


def fingerprint(value: dict) -> str:
    content = {key: str(value[key]) if key == "tenant_id" else value[key] for key in FIELDS}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def decide(rows: list[dict], desired: dict, previous: str | None) -> str:
    if previous is not None and not re.fullmatch(r"[a-f0-9]{64}", previous):
        raise ValueError("Invalid previous configuration digest")
    if not rows:
        if previous is not None:
            raise ValueError("Previously managed configuration is missing")
        return "insert"
    if len(rows) != 1:
        raise ValueError("Existing tenant configuration requires administrator action")
    row = rows[0]
    if (str(row["tenant_id"]) != desired["tenant_id"] or row["provider"] != "azure_blob"
            or row["updated_by_user_id"] is not None or row["is_enabled"] is not True):
        raise ValueError("Existing tenant configuration is not deployment-owned")
    current = fingerprint(row)
    if current == fingerprint(desired):
        return "unchanged"
    if previous != current:
        raise ValueError("Tenant provider configuration has changed")
    # Credential rotation may not move existing objects to another namespace.
    old_account = re.search(r"(?:^|;)AccountName=([^;]+)(?:;|$)", row["azure_connection_string"] or "")
    new_account = re.search(r"(?:^|;)AccountName=([^;]+)(?:;|$)", desired["azure_connection_string"])
    if (not old_account or old_account.group(1) != new_account.group(1)
            or row["azure_container_name"] != desired["azure_container_name"]):
        raise ValueError("Provider namespace migration requires administrator action")
    return "update"


async def check_provider(desired: dict) -> None:
    from api.interfaces.api_controllers.tenant_objects_endpoint import azure_store
    async with azure_store(SimpleNamespace(**desired)):
        pass


def configure(connection, desired: dict, previous: str | None, probe=check_provider) -> dict:
    from psycopg.rows import dict_row
    from psycopg import sql
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SELECT current_schema() AS schema, r.rolsuper, r.rolbypassrls, r.rolcreaterole, "
                "r.rolcreatedb, r.rolreplication, has_schema_privilege(current_schema(), 'CREATE') AS can_create "
                "FROM pg_roles r WHERE r.rolname = current_user")
            role = cursor.fetchone()
            if not role or role["schema"] != "storage_owner" or any(role[key] for key in role if key != "schema"):
                raise ValueError("Restricted storage runtime role required")
            cursor.execute("SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "pg_has_role(c.relowner, 'MEMBER') AS owns_table FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='storage_owner' "
                "AND c.relname IN ('tenants','storage_settings') AND c.relkind='r'")
            tables = cursor.fetchall()
            if (len(tables) != 2 or any(not row["relrowsecurity"] or not row["relforcerowsecurity"]
                    or row["owns_table"] for row in tables)):
                raise ValueError("Forced tenant isolation required")
            cursor.execute("SELECT set_config('app.current_tenant_id', %s, true)", (desired["tenant_id"],))
            cursor.execute("SELECT id FROM storage_owner.tenants WHERE id=%s FOR UPDATE", (desired["tenant_id"],))
            if cursor.fetchone() is None:
                raise ValueError("Declared storage tenant is missing")
            cursor.execute("SELECT * FROM storage_owner.storage_settings WHERE tenant_id=%s FOR UPDATE", (desired["tenant_id"],))
            rows = cursor.fetchall()
            action = decide(rows, desired, previous)
            asyncio.run(probe(desired))
            if action == "insert":
                columns = ("id", *FIELDS)
                cursor.execute(sql.SQL("INSERT INTO storage_owner.storage_settings ({}) VALUES ({})").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns)),
                    (uuid4(), *(desired[key] for key in FIELDS)))
            elif action == "update":
                cursor.execute(sql.SQL("UPDATE storage_owner.storage_settings SET {}, updated_at=now() WHERE id=%s AND tenant_id=%s").format(
                    sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in FIELDS)),
                    (*(desired[key] for key in FIELDS), rows[0]["id"], desired["tenant_id"]))
                if cursor.rowcount != 1:
                    raise ValueError("Tenant configuration changed during update")
    return {"changed": action != "unchanged", "sha256": fingerprint(desired)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-file", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--previous-sha256")
    args = parser.parse_args(argv)
    try:
        import psycopg
        from sqlalchemy.engine import make_url
        desired = validate(private_json(args.provider_file), args.tenant, args.network)
        url = make_url(os.environ["DATABASE_URL"])
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Storage runtime database required")
        with psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False), connect_timeout=5) as connection:
            result = configure(connection, desired, args.previous_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # SDK and database exceptions may contain provider credentials or SQL parameters.
        print("Tenant provider configuration failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
