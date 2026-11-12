"""Contract checks for signed configuration and conditional trusted guardrails."""
import base64
import json
from uuid import UUID

import pytest
from pydantic import ValidationError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.domain.experiments.control import (
    Aggregate, Configuration, Experiment, Guardrail, disable_regressed, signed_payload,
)

TENANT = UUID(int=1)


def experiment(**changes):
    return Experiment(**(dict(key="layout", control="control", variants=("control", "treatment"),
        weights=(0.5, 0.5), metrics=("sign_in_completed",),
        guardrails=(Guardrail(metric="error_rate", maximum=0.1, minimum_samples=100, window_seconds=60),)) | changes))


def config(**changes):
    return Configuration(**(dict(tenant=TENANT, audience="s3r.ch", sequence=1,
                                experiments=(experiment(),)) | changes))


class MemoryStore:
    def __init__(self, value=None, conflict=False):
        self.current = value or config()
        self.conflict = conflict
        self.writes = []

    async def load(self, tenant, audience):
        return self.current

    async def replace(self, before, after):
        self.writes.append((before, after))
        if self.conflict:
            return False
        assert before is self.current
        self.current = after
        return True


def metric_source(**changes):
    async def read(tenant, audience, key, variant, metric, start, end):
        return Aggregate(**(dict(tenant=tenant, audience=audience, experiment=key, variant=variant,
            metric=metric, start=start, end=end, samples=100, value=0.2) | changes))
    return read


def test_signed_payload_matches_existing_consumer_without_private_policy():
    key = Ed25519PrivateKey.generate()
    body, signature = signed_payload(config(), now=100, ttl=60, sign=key.sign)
    key.public_key().verify(base64.urlsafe_b64decode(signature + "=="), body)
    assert len(signature) == 86
    assert json.loads(body) == dict(version=1, audience="s3r.ch", sequence=1, issuedAt=100,
        expiresAt=160, experiments=[dict(key="layout", control="control", variants=["control", "treatment"],
        weights=[0.5, 0.5], disabled=False)])
    assert b"tenant" not in body and b"guardrails" not in body and b"metrics" not in body


@pytest.mark.parametrize("changes", [dict(weights=(0.2, 0.2)), dict(weights=(float("nan"), 0.5)),
    dict(variants=("control", "control")), dict(control="missing"), dict(metrics=("duplicate", "duplicate")),
    dict(metrics=("person@example.invalid",)), dict(key="layout\n"), dict(prompt="private")])
def test_configuration_refuses_ambiguous_or_private_fields(changes):
    with pytest.raises(ValidationError):
        experiment(**changes)


@pytest.mark.parametrize("now,ttl", [(True, 60), (-1, 60), (1, 0), (1, 604801), (9007199254740991, 1)])
def test_signing_refuses_invalid_validity_before_using_key(now, ttl):
    def fail(_):
        raise AssertionError("must not sign")
    with pytest.raises(ValueError, match="validity"):
        signed_payload(config(), now=now, ttl=ttl, sign=fail)


@pytest.mark.asyncio
async def test_regression_atomically_disables_without_changing_variants_or_other_policy():
    store = MemoryStore()
    assert await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", now=100) == ("layout",)
    assert store.current.sequence == 2 and store.current.experiments[0].disabled
    assert store.current.experiments[0].variants == ("control", "treatment")
    assert await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", now=101) == ()
    assert len(store.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [dict(samples=99), dict(value=0.1), dict(value=0.0)])
async def test_sparse_or_healthy_metrics_do_not_disable(changes):
    store = MemoryStore()
    assert await disable_regressed(store, metric_source(**changes), tenant=TENANT, audience="s3r.ch", now=100) == ()
    assert not store.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [dict(tenant=UUID(int=2)), dict(audience="portal"), dict(experiment="other"),
    dict(variant="control"), dict(metric="latency_ms"), dict(start=39), dict(end=99), dict(value=1.1)])
async def test_wrong_tenant_variant_window_or_metric_cannot_disable(changes):
    store = MemoryStore()
    with pytest.raises(ValueError, match="scope"):
        await disable_regressed(store, metric_source(**changes), tenant=TENANT, audience="s3r.ch", now=100)
    assert not store.writes


@pytest.mark.asyncio
async def test_missing_or_failed_provider_does_not_modify_configuration():
    store = MemoryStore()
    async def missing(*_):
        return None
    async def unavailable(*_):
        raise RuntimeError("unavailable")
    assert await disable_regressed(store, missing, tenant=TENANT, audience="s3r.ch", now=100) == ()
    with pytest.raises(RuntimeError):
        await disable_regressed(store, unavailable, tenant=TENANT, audience="s3r.ch", now=100)
    assert not store.writes


@pytest.mark.asyncio
async def test_conflict_never_replays_an_old_disable_against_new_operator_configuration():
    store = MemoryStore(conflict=True)
    with pytest.raises(ValueError, match="re-evaluate"):
        await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", now=100)
    assert len(store.writes) == 1 and store.current.sequence == 1
    assert not store.current.experiments[0].disabled


def test_scope_threshold_and_signer_boundaries():
    with pytest.raises(ValidationError):
        config(tenant=UUID(int=0))
    with pytest.raises(ValidationError):
        config(experiments=(experiment(), experiment()))
    with pytest.raises(ValidationError):
        Guardrail(metric="session_failure_rate", maximum=1.01, minimum_samples=100, window_seconds=60)
    with pytest.raises(ValueError, match="signature"):
        signed_payload(config(), now=100, ttl=60, sign=lambda _: b"short")


@pytest.mark.asyncio
async def test_store_scope_and_exhausted_sequence_are_refused_before_write():
    store = MemoryStore(config(tenant=UUID(int=2)))
    with pytest.raises(ValueError, match="scope"):
        await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", now=100)
    assert not store.writes
    store = MemoryStore(config(sequence=9007199254740991))
    with pytest.raises(ValidationError):
        await disable_regressed(store, metric_source(), tenant=TENANT, audience="s3r.ch", now=100)
    assert not store.writes


@pytest.mark.asyncio
async def test_window_cannot_precede_epoch_and_invalid_clock_never_reads():
    async def unused(*_):
        raise AssertionError("must not query")
    store = MemoryStore()
    assert await disable_regressed(store, unused, tenant=TENANT, audience="s3r.ch", now=59) == ()
    with pytest.raises(ValueError, match="time"):
        await disable_regressed(store, unused, tenant=TENANT, audience="s3r.ch", now=True)
    assert not store.writes
