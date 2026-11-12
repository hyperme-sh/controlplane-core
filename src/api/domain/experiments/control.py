"""Bounded flag payloads and conditional guardrail disable decisions."""
from __future__ import annotations

import base64
import json
import math
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", max_length=64)]
Second = Annotated[int, Field(ge=0, le=9007199254740991)]
Metric = Literal["error_rate", "latency_ms", "dependency_failure_rate", "session_failure_rate"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Guardrail(ClosedModel):
    metric: Metric
    maximum: float = Field(ge=0, allow_inf_nan=False)
    minimum_samples: int = Field(ge=1, le=1000000)
    window_seconds: int = Field(ge=60, le=86400)

    @model_validator(mode="after")
    def bounded_rate(self):
        if self.metric != "latency_ms" and self.maximum > 1:
            raise ValueError("rate threshold exceeds one")
        return self


class Experiment(ClosedModel):
    key: Label
    control: Label
    variants: tuple[Label, ...] = Field(min_length=2, max_length=8)
    weights: tuple[float, ...] = Field(min_length=2, max_length=8)
    metrics: tuple[Label, ...] = Field(min_length=1, max_length=16)
    guardrails: tuple[Guardrail, ...] = Field(min_length=1, max_length=4)
    disabled: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if (len(set(self.variants)) != len(self.variants) or self.control not in self.variants
                or len(self.weights) != len(self.variants)
                or any(not math.isfinite(w) or w < 0 or w > 1 for w in self.weights)
                or abs(sum(self.weights) - 1) > 1e-9
                or len(set(self.metrics)) != len(self.metrics)
                or len({g.metric for g in self.guardrails}) != len(self.guardrails)):
            raise ValueError("inconsistent experiment configuration")
        return self


class Configuration(ClosedModel):
    tenant: UUID
    audience: Literal["s3r.ch", "hyperme.sh", "portal"]
    sequence: int = Field(ge=1, le=9007199254740991)
    experiments: tuple[Experiment, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def scoped(self):
        if self.tenant.int == 0 or len({e.key for e in self.experiments}) != len(self.experiments):
            raise ValueError("invalid experiment scope")
        return self


def signed_payload(config: Configuration, *, now: int, ttl: int,
                   sign: Callable[[bytes], bytes]) -> tuple[bytes, str]:
    """Exact v1 consumer shape; private policy/tenant state never enters the payload.

    The caller obtains config from its authoritative conditional store and owns
    the Ed25519 key. Do not accept a signing callback or config from an HTTP body.
    """
    if (type(now) is not int or now < 0 or type(ttl) is not int or not 1 <= ttl <= 7 * 86400
            or now + ttl > 9007199254740991):
        raise ValueError("invalid flag validity")
    body = json.dumps({"version": 1, "audience": config.audience, "sequence": config.sequence,
        "issuedAt": now, "expiresAt": now + ttl, "experiments": [
            {"key": e.key, "control": e.control, "variants": e.variants,
             "weights": e.weights, "disabled": e.disabled} for e in config.experiments
        ]}, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > 32768:
        raise ValueError("flag payload exceeds limit")
    signature = sign(body)
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("invalid flag signature")
    return body, base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


class Aggregate(ClosedModel):
    """Output of an authenticated server/provider query, already deduplicated by action ID."""
    tenant: UUID
    audience: Literal["s3r.ch", "hyperme.sh", "portal"]
    experiment: Label
    variant: Label
    metric: Metric
    start: Second
    end: Second
    samples: int = Field(ge=0, le=1000000000)
    value: float = Field(ge=0, allow_inf_nan=False)


class Store(Protocol):
    async def load(self, tenant: UUID, audience: str) -> Configuration: ...
    async def replace(self, previous: Configuration, next_config: Configuration) -> bool: ...


# A deployment-owned source authenticates upstream telemetry and deduplicates
# action IDs. No client endpoint may construct Aggregate and call the controller.
Metrics = Callable[[UUID, str, str, str, Metric, int, int], Awaitable[Aggregate | None]]


async def disable_regressed(store: Store, metrics: Metrics, *, tenant: UUID,
                           audience: str, now: int) -> tuple[str, ...]:
    """One conditional update. Conflict means reload/re-evaluate, never replay old signals.

    This controller only disables; enabling again requires an operator update.
    A failed/missing/stale query cannot create a disable decision.
    """
    if type(now) is not int or now < 0:
        raise ValueError("invalid evaluation time")
    current = await store.load(tenant, audience)
    if current.tenant != tenant or current.audience != audience:
        raise ValueError("experiment scope mismatch")
    disabled = set()
    for experiment in current.experiments:
        if experiment.disabled:
            continue
        for guardrail in experiment.guardrails:
            start = now - guardrail.window_seconds
            if start < 0:
                continue
            for variant in experiment.variants:
                if variant == experiment.control:
                    continue
                row = await metrics(tenant, audience, experiment.key, variant, guardrail.metric, start, now)
                if row is None:
                    continue
                if (row.tenant != tenant or row.audience != audience or row.experiment != experiment.key
                        or row.variant != variant or row.metric != guardrail.metric or row.start != start
                        or row.end != now or (row.metric != "latency_ms" and row.value > 1)):
                    raise ValueError("metric scope mismatch")
                if row.samples >= guardrail.minimum_samples and row.value > guardrail.maximum:
                    disabled.add(experiment.key)
    if not disabled:
        return ()
    updated = Configuration(tenant=current.tenant, audience=current.audience, sequence=current.sequence + 1,
        experiments=tuple(e.model_copy(update={"disabled": True}) if e.key in disabled else e
                          for e in current.experiments))
    if not await store.replace(current, updated):
        raise ValueError("experiment configuration changed; re-evaluate")
    return tuple(sorted(disabled))
