"""Signed cache publication contracts with only an in-memory conditional backend."""
import base64
import json
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from api.domain.experiments.publication import Publisher
from api.domain.experiments.store import ExperimentStore
from api.domain.tenant_records import RecordConflict, TenantRecords, record_key
from api.experiment_publisher import signing_binding
from test_experiment_store import Backend, config

TENANT = UUID(int=1)


async def setup():
    backend = Backend()
    key = Ed25519PrivateKey.generate()
    await ExperimentStore(TENANT, backend).initialize(config())
    return backend, key, Publisher(TENANT, "s3r.ch", key, backend)


@pytest.mark.asyncio
async def test_signed_publication_renews_without_changing_policy_revision():
    backend, key, publisher = await setup()
    assert await publisher.publish(1000)
    cached = await publisher.records.get("cache", publisher.cache_name)
    envelope = json.loads(cached.data)
    key.public_key().verify(base64.urlsafe_b64decode(envelope["signature"] + "=="), envelope["payload"].encode())
    payload = json.loads(envelope["payload"])
    assert set(payload) == {"version", "audience", "sequence", "issuedAt", "expiresAt", "experiments"}
    assert payload["sequence"] == 1 and payload["expiresAt"] == 1300
    assert "tenant" not in envelope["payload"] and "guardrails" not in envelope["payload"]
    writes = backend.count
    assert not await publisher.publish(1100) and backend.count == writes
    assert await publisher.publish(1180)
    assert (await publisher.store.load(TENANT, "s3r.ch")).sequence == 1
    assert publisher.verified_sequence((await publisher.records.get("cache", publisher.cache_name)).data) == 2
    assert publisher.state_key != record_key(TENANT, "cache", publisher.cache_name)


@pytest.mark.asyncio
async def test_policy_update_publishes_immediately_and_cannot_roll_back():
    backend, key, publisher = await setup()
    await publisher.publish(1000)
    assert await publisher.store.replace(config(), config(2))
    assert await publisher.publish(1001)
    # Simulate a damaged private policy record restored behind its publication.
    initial = Backend()
    await ExperimentStore(TENANT, initial).initialize(config())
    backend.rows[publisher.store.key(TENANT, "s3r.ch")] = next(iter(initial.rows.values()))
    with pytest.raises(ValueError, match="sequence"):
        await publisher.publish(1002)


@pytest.mark.asyncio
async def test_cache_corruption_does_not_allocate_or_trust_an_unsigned_sequence():
    backend, _, publisher = await setup()
    await publisher.publish(1000)
    cached = await publisher.records.get("cache", publisher.cache_name)
    await publisher.records.put("cache", publisher.cache_name,
        b'{"payload":"{\\"sequence\\":999999}","signature":"invalid"}', cached.revision)
    assert await publisher.publish(1001)
    assert publisher.verified_sequence((await publisher.records.get("cache", publisher.cache_name)).data) == 1


@pytest.mark.asyncio
async def test_stale_worker_cannot_replace_newer_signed_cache():
    backend, key, publisher = await setup()
    await publisher.publish(1000)
    old_state = backend.rows[publisher.state_key]
    await publisher.publish(1180)
    backend.rows[publisher.state_key] = old_state
    with pytest.raises(RecordConflict, match="newer"):
        await publisher.publish(1100)
    assert publisher.verified_sequence((await publisher.records.get("cache", publisher.cache_name)).data) == 2


@pytest.mark.asyncio
async def test_private_state_cas_conflict_does_not_publish():
    backend, _, publisher = await setup()
    backend.conflict = True
    with pytest.raises(RecordConflict):
        await publisher.publish(1000)
    assert await publisher.records.get("cache", publisher.cache_name) is None


@pytest.mark.asyncio
async def test_cache_failure_is_repaired_from_private_publication():
    backend, _, publisher = await setup()
    original = backend.replace
    async def fail_cache(key, data, etag):
        if key.startswith("tenant-records/"):
            raise RuntimeError("provider unavailable")
        await original(key, data, etag)
    backend.replace = fail_cache
    with pytest.raises(RuntimeError):
        await publisher.publish(1000)
    backend.replace = original
    assert await publisher.publish(1001)
    assert publisher.verified_sequence((await publisher.records.get("cache", publisher.cache_name)).data) == 1


@pytest.mark.asyncio
async def test_changed_policy_during_signing_is_not_published():
    backend, _, publisher = await setup()
    original = publisher.store.load
    calls = 0
    async def changed(*args):
        nonlocal calls
        calls += 1
        return await original(*args) if calls == 1 else config(2)
    publisher.store.load = changed
    with pytest.raises(RecordConflict):
        await publisher.publish(1000)
    assert await publisher.records.get("cache", publisher.cache_name) is None


@pytest.mark.asyncio
async def test_backward_clock_or_wrong_signing_binding_refuses():
    backend, _, publisher = await setup()
    await publisher.publish(1000)
    with pytest.raises(ValueError):
        await publisher.publish(999)
    other = Publisher(TENANT, "s3r.ch", Ed25519PrivateKey.generate(), backend)
    with pytest.raises(ValueError):
        await other.publish(1001)
    with pytest.raises(ValueError):
        Publisher(TENANT, "untrusted", Ed25519PrivateKey.generate(), backend)
    with pytest.raises(ValueError):
        Publisher(UUID(int=0), "s3r.ch", Ed25519PrivateKey.generate(), backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [-1, True, 1.5, 9007199254740692])
async def test_invalid_clock_does_not_write(now):
    backend, _, publisher = await setup()
    writes = backend.count
    with pytest.raises(ValueError):
        await publisher.publish(now)
    assert backend.count == writes


def test_binding_reads_bounded_private_key_and_rejects_other_key_types(tmp_path, monkeypatch):
    private = tmp_path / "key.pem"
    monkeypatch.setenv("EXPERIMENT_TENANT_ID", str(TENANT))
    monkeypatch.setenv("EXPERIMENT_AUDIENCE", "s3r.ch")
    monkeypatch.setenv("EXPERIMENT_SIGNING_KEY_FILE", str(private))
    key = Ed25519PrivateKey.generate()
    private.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    tenant, audience, parsed = signing_binding()
    assert tenant == TENANT and audience == "s3r.ch"
    key.public_key().verify(parsed.sign(b"unit"), b"unit")
    private.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError):
        signing_binding()
    private.write_bytes(generate_private_key(public_exponent=65537, key_size=2048).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(ValueError):
        signing_binding()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_worker_scopes_provider_and_resets_database_context(monkeypatch, failed):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, Mock
    from api import experiment_publisher as worker
    session = Mock(commit=AsyncMock(), rollback=AsyncMock())
    @asynccontextmanager
    async def maker():
        yield session
    backend, key, publisher = await setup()
    @asynccontextmanager
    async def provider(tenant):
        assert tenant == TENANT
        yield worker.AzureTenantObjects(object())
    set_context, reset_context = Mock(return_value="context"), Mock()
    scope = AsyncMock()
    monkeypatch.setattr(worker, "set_db_session", set_context)
    monkeypatch.setattr(worker, "reset_db_session", reset_context)
    monkeypatch.setattr(worker, "set_current_tenant_id_guc", scope)
    monkeypatch.setattr(worker, "tenant_backend", provider)
    monkeypatch.setattr(worker, "AzureTenantRecords", lambda _: backend)
    if failed:
        backend.conflict = True
        with pytest.raises(RecordConflict):
            await worker.tick(maker, TENANT, "s3r.ch", key, 1000)
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    else:
        assert await worker.tick(maker, TENANT, "s3r.ch", key, 1000)
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    set_context.assert_called_once_with(session)
    scope.assert_awaited_once_with(session, TENANT)
    reset_context.assert_called_once_with("context")
