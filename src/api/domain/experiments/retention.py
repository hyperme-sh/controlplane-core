"""Conditional expiry of one tenant/audience's private experiment outbox."""
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from uuid import UUID

from api.domain.experiments.records import AUDIENCES, outbox_document
from api.domain.storage_scope import storage_tenant
from api.domain.tenant_records import MAGIC, RecordBackend, RecordConflict, decode, provider_etag

PAGE_SIZE = 32

Page = Callable[[str, str | None], Awaitable[tuple[list[str], str | None]]]


async def sweep_page(backend: RecordBackend, page: Page, tenant: UUID, audience: str, now_ms: int):
    tenant = storage_tenant(tenant)
    if audience not in AUDIENCES or type(now_ms) is not int or now_ms < 0:
        raise ValueError("Invalid experiment retention binding")
    prefix = f"tenant-records/v1/{tenant.hex}/experiments/{audience}-outbox-"
    cursor_key = f"tenant-experiment-retention/v1/{tenant.hex}/{audience}"
    cursor = await backend.fetch(cursor_key)
    token, etag, pending = None, None, []
    if cursor is not None:
        etag = provider_etag(cursor[1])
        raw = decode(cursor[0]).data
        if len(raw) > 16384:
            raise ValueError("Invalid retention cursor")
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get("version")) is not int or not (
            value["version"] == 1 and set(value) == {"version", "token"} or
            value["version"] == 2 and set(value) == {"version", "token", "pending"}
        ):
            raise ValueError("Invalid retention cursor")
        token = value["token"]
        pending = value.get("pending", [])
    if token is not None and (not isinstance(token, str) or not 1 <= len(token) <= 8192):
        raise ValueError("Invalid retention cursor")
    if not isinstance(pending, list):
        raise ValueError("Invalid retention cursor")
    names, next_token = (pending, token) if pending else await page(prefix, token)
    if (not isinstance(names, list) or len(names) > PAGE_SIZE or len(set(names)) != len(names)
            or any(not isinstance(name, str) or not re.fullmatch(re.escape(prefix) + r"[a-f0-9]{64}", name) for name in names)
            or next_token is not None and (not isinstance(next_token, str) or not 1 <= len(next_token) <= 8192 or not pending and next_token == token)):
        raise ValueError("Invalid experiment retention page")
    removed = 0
    # Persist the rest of the page after each object. A deadline retries at
    # most this object instead of discarding a whole page of scan progress.
    for name in names[:1]:
        try:
            stored = await backend.fetch(name)
            if stored is None:
                continue
            value = outbox_document(decode(stored[0]).data)
            if value["expires"] <= now_ms:
                await backend.remove(name, provider_etag(stored[1]))
                removed += 1
        except RecordConflict:
            # Re-read on a later scan; a changed object must never be deleted
            # using a stale decision or an unconditional provider operation.
            continue
    state = json.dumps({"version": 2, "token": next_token, "pending": names[1:]}, separators=(",", ":")).encode()
    await backend.replace(cursor_key, MAGIC + secrets.token_bytes(32) + state, etag)
    return {"scanned": min(1, len(names)), "removed": removed, "more": bool(names[1:]) or next_token is not None}
