"""Bounded consume-only replay state, isolated from mutable tenant records."""
import json
import re
import secrets
import time

from api.domain.storage_scope import storage_tenant
from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_records import RecordConflict, provider_etag

MAX_ENTRIES = 4096
MAX_WINDOW = 60
MAX_BYTES = 400_000
MAGIC = b"SPANRPL1"


class ReplayUsed(RuntimeError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ObjectInputError("Invalid replay identifier")
    return value


def decode(data):
    try:
        if not isinstance(data, bytes) or not 40 < len(data) <= MAX_BYTES or not data.startswith(MAGIC):
            raise ValueError()
        pairs = json.loads(data[40:], object_pairs_hook=list)
        if not isinstance(pairs, list) or len(pairs) > MAX_ENTRIES:
            raise ValueError()
        entries = {}
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError()
            key, expires = pair
            identifier(key)
            if key in entries or type(expires) is not int or not 0 < expires <= 2**53 - 1:
                raise ValueError()
            entries[key] = expires
        # Require an object; an array of pairs must not be accepted as state.
        if not data[40:].startswith(b"{"):
            raise ValueError()
        return entries
    except (ValueError, TypeError, ObjectInputError, RecursionError):
        raise ObjectUnavailable("Invalid replay state") from None


class TenantReplays:
    def __init__(self, tenant, backend, clock=time.time):
        self.tenant, self.backend, self.clock = storage_tenant(tenant), backend, clock

    def now(self, expires):
        at = self.clock()
        if type(at) not in (int, float) or not 0 <= at < 2**53 - MAX_WINDOW:
            raise ObjectUnavailable("Replay clock unavailable")
        if type(expires) is not int or not at < expires <= int(at) + MAX_WINDOW:
            raise ObjectInputError("Invalid replay expiry")
        return at

    async def consume(self, namespace, nonce, expires):
        key = f"tenant-replays/v1/{self.tenant.hex}/{identifier(namespace)}"
        identifier(nonce)
        self.now(expires)
        for _ in range(4):
            try:
                current = await self.backend.fetch(key)
                at = self.now(expires)
                entries = decode(current[0]) if current else {}
                etag = provider_etag(current[1]) if current else None
                if nonce in entries:
                    raise ReplayUsed("Replay already consumed")
                # Never discard a live entry to make capacity for another call.
                entries = {name: end for name, end in entries.items() if end > at}
                if len(entries) >= MAX_ENTRIES:
                    raise ObjectUnavailable("Replay capacity unavailable")
                entries[nonce] = expires
                data = MAGIC + secrets.token_bytes(32) + json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
                self.now(expires)
                await self.backend.replace(key, data, etag)
                # A delayed provider result must never authorize an expired proof.
                self.now(expires)
                return
            except RecordConflict:
                continue
        raise ObjectUnavailable("Replay contention unavailable")
