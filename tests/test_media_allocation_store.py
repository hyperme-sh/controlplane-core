"""Private allocation persistence and conflict unit contracts."""
from dataclasses import replace
import json
from uuid import UUID

import pytest

from api.domain.media_allocation_grant import AllocationRejected
from api.domain.media_allocation_store import AllocationStore, Reservation, MAX_LEDGER_BYTES
from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import MAGIC, RecordConflict, record_key
from test_media_allocation_grant import grant


class Backend:
    def __init__(self):
        self.rows, self.writes, self.conflict = {}, 0, False

    async def fetch(self, key):
        return self.rows.get(key)

    async def replace(self, key, data, etag):
        current = self.rows.get(key)
        if self.conflict or (current[1] if current else None) != etag:
            raise RecordConflict("changed")
        self.writes += 1
        self.rows[key] = (data, f'"{self.writes}"')


@pytest.fixture
def registry(grant):
    scope = grant[0]
    backend = Backend()
    return AllocationStore(scope.tenant, scope.issuer, backend), backend, Reservation(scope, str(UUID(int=4)), 1000, 1300)


def allowed():
    pass


@pytest.mark.asyncio
async def test_missing_bootstrap_restart_and_original_retry(registry):
    store, backend, candidate = registry
    with pytest.raises(AllocationRejected, match="unavailable"):
        await store.reserve(candidate, now=1100, before_write=allowed)
    assert backend.writes == 0
    await store.initialize()
    with pytest.raises(RecordConflict):
        await store.initialize()
    assert store.key != record_key(UUID(store.tenant), "media", "registry")
    assert await store.reserve(candidate, now=1100, before_write=allowed) == candidate
    restarted = AllocationStore(store.tenant, store.issuer, backend)
    retry = replace(candidate, allocation=str(UUID(int=9)), issued_at=1400, expires_at=1700)
    # Expiry never releases or renews ownership; the old proof remains expired.
    assert await restarted.reserve(retry, now=1500, before_write=allowed) == candidate
    assert backend.writes == 2


@pytest.mark.asyncio
async def test_conflicting_source_policy_and_identifier_refuse(registry):
    store, backend, candidate = registry
    await store.initialize()
    await store.reserve(candidate, now=1100, before_write=allowed)
    for scope in [replace(candidate.scope, source=str(UUID(int=9))),
                  replace(candidate.scope, policy_digest="f" * 64),
                  replace(candidate.scope, ownership_digest="d" * 64)]:
        with pytest.raises(RecordConflict):
            await store.reserve(replace(candidate, scope=scope), now=1100, before_write=allowed)
    other = replace(candidate, scope=replace(candidate.scope, object=candidate.scope.object[:-32] + "f" * 32))
    with pytest.raises(RecordConflict, match="identifier"):
        await store.reserve(other, now=1100, before_write=allowed)
    assert backend.writes == 2


@pytest.mark.asyncio
async def test_guard_rechecked_on_write_and_retry_and_cas_conflict(registry):
    store, backend, candidate = registry
    await store.initialize()
    def denied():
        raise PermissionError("owner changed")
    with pytest.raises(PermissionError):
        await store.reserve(candidate, now=1100, before_write=denied)
    assert backend.writes == 1
    backend.conflict = True
    with pytest.raises(RecordConflict):
        await store.reserve(candidate, now=1100, before_write=allowed)
    backend.conflict = False
    await store.reserve(candidate, now=1100, before_write=allowed)
    with pytest.raises(PermissionError):
        await store.reserve(candidate, now=1100, before_write=denied)
    assert backend.writes == 2


@pytest.mark.asyncio
async def test_quota_is_retained_across_expiry(registry):
    store, backend, candidate = registry
    await store.initialize()
    for i in range(64):
        item = replace(candidate, scope=replace(candidate.scope, object=candidate.scope.object[:-32] + f"{i:032x}"), allocation=str(UUID(int=i+10)))
        await store.reserve(item, now=1100, before_write=allowed)
    with pytest.raises(AllocationRejected, match="quota"):
        await store.reserve(replace(candidate, issued_at=1400, expires_at=1500), now=1401, before_write=allowed)
    assert backend.writes == 65


@pytest.mark.asyncio
async def test_invalid_scope_times_and_stored_registry_refuse(registry):
    store, backend, candidate = registry
    await store.initialize()
    for invalid in [replace(candidate, issued_at=True), replace(candidate, expires_at=1000),
                    replace(candidate, expires_at=1400), replace(candidate, allocation="bad"),
                    replace(candidate, scope=replace(candidate.scope, tenant=str(UUID(int=9))))]:
        with pytest.raises(AllocationRejected):
            await store.reserve(invalid, now=1100, before_write=allowed)
    for now in [999, 1300, True]:
        with pytest.raises(AllocationRejected):
            await store.reserve(candidate, now=now, before_write=allowed)
    encoded = store._encode((candidate,))
    value = json.loads(encoded[len(MAGIC)+32:])
    invalid_values = [[], {**value, "version": True}, {**value, "tenant": str(UUID(int=9))},
        {**value, "entries": {}}, {**value, "entries": [None]},
        {**value, "entries": [{**value["entries"][0], "scope": {}}]},
        {**value, "entries": value["entries"] * 2}]
    for item in invalid_values:
        backend.rows[store.key] = (MAGIC + b"0" * 32 + json.dumps(item).encode(), '"1"')
        with pytest.raises(AllocationRejected):
            await store.reserve(candidate, now=1100, before_write=allowed)
    for raw in [b"x" * (MAX_LEDGER_BYTES+1), MAGIC + b"0" * 32 + b'{"version":1,"version":1}',
                MAGIC + b"0" * 32 + b"[" * 1500]:
        backend.rows[store.key] = (raw, '"1"')
        with pytest.raises(AllocationRejected):
            await store.reserve(candidate, now=1100, before_write=allowed)
    backend.rows[store.key] = (b"broken", '"1"')
    with pytest.raises(ObjectUnavailable):
        await store.reserve(candidate, now=1100, before_write=allowed)
    backend.rows[store.key] = (encoded, "bad-etag")
    with pytest.raises(ObjectUnavailable):
        await store.reserve(candidate, now=1100, before_write=allowed)
