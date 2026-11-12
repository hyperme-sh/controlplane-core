"""Provider conditional writes isolate flags from general tenant records."""
from uuid import UUID

import pytest

from api.domain.experiments.control import Configuration
from api.domain.experiments.store import ExperimentStore
from api.domain.tenant_records import RecordConflict, record_key
from api.domain.tenant_objects import ObjectUnavailable

TENANT = UUID(int=1)


class Backend:
    def __init__(self):
        self.rows = {}
        self.count = 0
        self.conflict = False

    async def fetch(self, key):
        return self.rows.get(key)

    async def replace(self, key, data, etag):
        held = self.rows.get(key)
        if self.conflict or (held[1] if held else None) != etag:
            raise RecordConflict()
        self.count += 1
        self.rows[key] = (data, f'"{self.count}"')


def config(sequence=1, tenant=TENANT, audience="s3r.ch"):
    return Configuration(tenant=tenant, audience=audience, sequence=sequence, experiments=())


@pytest.mark.asyncio
async def test_private_prefix_and_conditional_roundtrip():
    backend = Backend()
    store = ExperimentStore(TENANT, backend)
    first = config()
    await store.initialize(first)
    assert await store.load(TENANT, "s3r.ch") == first
    assert store.key(TENANT, "s3r.ch") != record_key(TENANT, "cache", "experiments")
    with pytest.raises(ValueError):
        record_key(TENANT, "experiments", "s3rch")
    with pytest.raises(RecordConflict):
        await store.initialize(first)
    assert await store.replace(first, config(2))
    assert not await store.replace(first, config(2))
    assert (await store.load(TENANT, "s3r.ch")).sequence == 2


@pytest.mark.asyncio
async def test_changed_provider_revision_is_not_retried():
    backend = Backend()
    store = ExperimentStore(TENANT, backend)
    await store.initialize(config())
    backend.conflict = True
    assert not await store.replace(config(), config(2))
    assert backend.count == 1


@pytest.mark.asyncio
async def test_scope_sequence_missing_and_corrupt_state_refuse():
    backend = Backend()
    store = ExperimentStore(TENANT, backend)
    with pytest.raises(ValueError, match="unavailable"):
        await store.load(TENANT, "s3r.ch")
    assert not await store.replace(config(), config(2))
    for value in [config(2), config(tenant=UUID(int=2))]:
        with pytest.raises(ValueError):
            await store.initialize(value)
    await store.initialize(config())
    for value in [config(), config(3), config(2, audience="portal"), config(2, tenant=UUID(int=2))]:
        with pytest.raises(ValueError):
            await store.replace(config(), value)
    with pytest.raises(ValueError):
        await store.load(UUID(int=2), "s3r.ch")
    key = store.key(TENANT, "s3r.ch")
    backend.rows[key] = (store.encode(config(audience="portal")), '"1"')
    with pytest.raises(ValueError, match="scope"):
        await store.load(TENANT, "s3r.ch")
    backend.rows[key] = (b"invalid", '"1"')
    with pytest.raises(ObjectUnavailable):
        await store.load(TENANT, "s3r.ch")


@pytest.mark.asyncio
async def test_slow_conditional_read_rechecks_guardrail_deadline_before_dispatch():
    from api.domain.experiments.control import disable_regressed
    from test_experiment_control import config as configured, metric_source
    moments = [100]
    class SlowBackend(Backend):
        calls = 0
        async def fetch(self, key):
            self.calls += 1
            if self.calls == 2:
                moments[0] = 131
            return await super().fetch(key)
    backend = SlowBackend()
    store = ExperimentStore(TENANT, backend)
    await store.initialize(configured())
    with pytest.raises(ValueError, match="expired"):
        await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", clock=lambda: moments[0])
    assert backend.count == 1
    assert (await store.load(TENANT, "s3r.ch")).sequence == 1
