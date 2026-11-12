"""Replay contracts using only in-memory conditional storage and HTTP doubles."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
import httpx
import pytest

from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_records import RecordConflict, record_key
from api.domain.tenant_replays import MAGIC, MAX_ENTRIES, ReplayUsed, TenantReplays, decode
from api.infrastructure.security import storage_session as sessions
from api.interfaces.api_controllers import tenant_replays_endpoint as route

NS, NONCE = "a" * 64, "b" * 64


class Memory:
    def __init__(self):
        self.rows = {}
        self.barrier = None
        self.reads = 0

    async def fetch(self, key):
        result = self.rows.get(key)
        if self.barrier:
            self.reads += 1
            if self.reads == 2:
                self.barrier.set()
            await self.barrier.wait()
        return result

    async def replace(self, key, data, etag):
        current = self.rows.get(key)
        if (current[1] if current else None) != etag:
            raise RecordConflict()
        self.rows[key] = (data, '"' + hashlib.sha256(data).hexdigest() + '"')


def encoded(entries):
    return MAGIC + b"0" * 32 + json.dumps(entries).encode()


@pytest.mark.asyncio
async def test_once_per_tenant_and_namespace_with_no_mutable_record_alias():
    backend = Memory()
    owner, other = TenantReplays(uuid4(), backend, lambda: 100), TenantReplays(uuid4(), backend, lambda: 100)
    await owner.consume(NS, NONCE, 160)
    with pytest.raises(ReplayUsed):
        await owner.consume(NS, NONCE, 150)
    await other.consume(NS, NONCE, 160)
    await owner.consume("c" * 64, NONCE, 160)
    assert len(backend.rows) == 3
    assert all(key.startswith("tenant-replays/v1/") for key in backend.rows)
    assert not hasattr(owner, "delete") and not hasattr(owner, "get")
    for namespace in ("cache", "recovery", "fixtures"):
        assert record_key(owner.tenant, namespace, NS) not in backend.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("same", [True, False])
async def test_atomic_race_prevents_reuse_without_losing_another_nonce(same):
    backend = Memory()
    backend.barrier = asyncio.Event()
    store = TenantReplays(uuid4(), backend, lambda: 100)
    results = await asyncio.gather(store.consume(NS, NONCE, 160),
        store.consume(NS, NONCE if same else "c" * 64, 160), return_exceptions=True)
    assert sum(isinstance(result, ReplayUsed) for result in results) == int(same)
    assert all(result is None or isinstance(result, ReplayUsed) for result in results)
    assert len(decode(next(iter(backend.rows.values()))[0])) == (1 if same else 2)


@pytest.mark.asyncio
async def test_capacity_never_evicts_live_entries_but_prunes_expired_entries():
    backend = Memory()
    clock = [100]
    store = TenantReplays(uuid4(), backend, lambda: clock[0])
    key = f"tenant-replays/v1/{store.tenant.hex}/{NS}"
    entries = {f"{i:064x}": 120 for i in range(MAX_ENTRIES)}
    backend.rows[key] = (encoded(entries), '"first"')
    with pytest.raises(ObjectUnavailable):
        await store.consume(NS, NONCE, 160)
    assert decode(backend.rows[key][0]) == entries
    clock[0] = 120
    await store.consume(NS, NONCE, 160)
    assert decode(backend.rows[key][0]) == {NONCE: 160}
    with pytest.raises(ObjectInputError):
        await store.consume(NS, f"{0:064x}", 120)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["fetch", "replace"])
async def test_expiry_rechecked_after_each_provider_wait(phase):
    clock = [100]
    backend = Memory()
    original = getattr(backend, phase)
    async def delayed(*args):
        result = await original(*args)
        clock[0] = 160
        return result
    setattr(backend, phase, delayed)
    store = TenantReplays(uuid4(), backend, lambda: clock[0])
    with pytest.raises(ObjectInputError):
        await store.consume(NS, NONCE, 160)
    assert len(backend.rows) == int(phase == "replace")


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace,nonce,expiry", [("../x",NONCE,160),(NS,"x",160),
    (NS,NONCE,100),(NS,NONCE,161),(NS,NONCE,True),(NS,NONCE,150.0)])
async def test_invalid_inputs_do_not_touch_storage(namespace, nonce, expiry):
    backend = SimpleNamespace(fetch=AsyncMock(), replace=AsyncMock())
    with pytest.raises(ObjectInputError):
        await TenantReplays(uuid4(), backend, lambda: 100).consume(namespace, nonce, expiry)
    backend.fetch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [-1,float("nan"),float("inf"),True,"100"])
async def test_invalid_clock_fails_closed(at):
    with pytest.raises(ObjectUnavailable):
        await TenantReplays(uuid4(), Memory(), lambda: at).consume(NS, NONCE, 160)


@pytest.mark.asyncio
async def test_contention_and_provider_failures_never_authorize():
    backend = SimpleNamespace(fetch=AsyncMock(side_effect=RecordConflict()), replace=AsyncMock())
    with pytest.raises(ObjectUnavailable):
        await TenantReplays(uuid4(), backend, lambda: 100).consume(NS, NONCE, 160)
    assert backend.fetch.await_count == 4
    backend.fetch.side_effect = ObjectUnavailable("private")
    with pytest.raises(ObjectUnavailable):
        await TenantReplays(uuid4(), backend, lambda: 100).consume(NS, NONCE, 160)
    backend.replace.assert_not_called()


@pytest.mark.parametrize("data", [b"bad", MAGIC+b"0"*32+b"[]", MAGIC+b"0"*32+b"null",
    MAGIC+b"0"*32+b"{"+b'"'+NONCE.encode()+b'":120,"'+NONCE.encode()+b'":130}',
    encoded({"bad":150}),encoded({NONCE:True}),encoded({NONCE:0}), encoded({NONCE:2**53}),
    encoded({f"{i:064x}":150 for i in range(MAX_ENTRIES+1)}),MAGIC+b"0"*400_000])
def test_corrupt_state_is_not_reset(data):
    with pytest.raises(ObjectUnavailable):
        decode(data)


def test_dedicated_scope_and_post_only_operation(monkeypatch):
    tenant = uuid4()
    claims = {"azp":"controlplane-api-key","sub":"apikey:"+str(uuid4()),"iat":100,"exp":200,
        "https://spanopticon.dev/tenant_id":str(tenant),"scope":"storage:replays:consume"}
    monkeypatch.setattr(sessions, "try_decode_api_key_session_jwt", lambda _: claims)
    path = f"/api/v1/storage/replays/{NS}/{NONCE}"
    operation = sessions.request_operation("POST", path)
    assert sessions.storage_session("token", operation, now=150) == tenant
    for method in ("GET","HEAD","PUT","DELETE"):
        assert sessions.request_operation(method, path) is None
    for scope in ("storage:objects:write", "storage:records:write", "storage:records:delete"):
        claims["scope"] = scope
        with pytest.raises(HTTPException) as caught:
            sessions.storage_session("token", operation, now=150)
        assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_http_consume_only_contract_and_bounded_headers():
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    store = TenantReplays(uuid4(), Memory(), lambda: 100)
    app.dependency_overrides[route.replay_store] = lambda: store
    path = f"/api/v1/storage/replays/{NS}/{NONCE}"
    headers = {"X-Replay-Expires-At":"160", "Content-Length":"0"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://storage") as client:
        first = await client.post(path, headers=headers)
        assert first.status_code == 204 and first.headers["cache-control"] == "private, no-store"
        assert (await client.post(path, headers=headers)).status_code == 409
        for method in ("GET","PUT","DELETE"):
            assert (await client.request(method, path)).status_code == 405
        for invalid in ({}, {**headers,"Content-Length":"1"}, {**headers,"X-Replay-Expires-At":"1e2"},
                {**headers,"Transfer-Encoding":"chunked"}, {**headers,"Content-Encoding":"gzip"}):
            assert (await client.post(path, headers=invalid)).status_code == 400
        duplicate = list(headers.items()) + [("X-Replay-Expires-At","160")]
        assert (await client.post(path, headers=duplicate)).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(ObjectInputError("secret"),400),(ObjectUnavailable("secret"),503),
    (TimeoutError("secret"),504),(ReplayUsed("secret"),409)])
async def test_http_failures_are_sanitized(error, status):
    async def receive():
        return {"type":"http.request","body":b"","more_body":False}
    request = Request({"type":"http","method":"POST","path":"/","headers":[
        (b"x-replay-expires-at",b"160"),(b"content-length",b"0")]}, receive)
    with pytest.raises(HTTPException) as caught:
        await route.consume_replay(NS, NONCE, request, SimpleNamespace(consume=AsyncMock(side_effect=error)))
    assert caught.value.status_code == status and "secret" not in caught.value.detail


def test_store_reuses_authenticated_tenant_and_private_backend():
    records = SimpleNamespace(tenant=uuid4(), backend=Memory())
    result = route.replay_store(records)
    assert result.tenant == records.tenant and result.backend is records.backend


@pytest.mark.asyncio
async def test_body_bytes_are_rejected_before_storage():
    async def receive():
        return {"type":"http.request","body":b"unexpected","more_body":False}
    request = Request({"type":"http","method":"POST","path":"/","headers":[
        (b"x-replay-expires-at",b"160"),(b"content-length",b"0")]}, receive)
    store = SimpleNamespace(consume=AsyncMock())
    with pytest.raises(HTTPException) as caught:
        await route.consume_replay(NS, NONCE, request, store)
    assert caught.value.status_code == 400
    store.consume.assert_not_called()
