"""Tenant-scoped Kafka publishing; set from app lifespan (Kafka or no-op)."""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from controlplane_events import (
    AioKafkaEventProducer,
    ControlPlaneEventEnvelope,
)


@runtime_checkable
class TenantEventPublisher(Protocol):
    async def publish(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None: ...


class NoOpTenantEventPublisher:
    async def publish(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        return None


class KafkaTenantEventPublisher:
    def __init__(
        self,
        producer: AioKafkaEventProducer,
        topic: str,
        service_name: str,
    ) -> None:
        self._producer = producer
        self._topic = topic
        self._service_name = service_name

    async def publish(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        env = ControlPlaneEventEnvelope(
            event_type=event_type,
            service=self._service_name,
            tenant_id=tenant_id,
            payload=payload,
            correlation_id=correlation_id,
        )
        key = str(tenant_id).encode("utf-8") if tenant_id is not None else None
        await self._producer.send_envelope(self._topic, env, key=key)


_publisher: TenantEventPublisher | None = None
_FALLBACK: TenantEventPublisher = NoOpTenantEventPublisher()


def set_tenant_event_publisher(publisher: TenantEventPublisher | None) -> None:
    global _publisher
    _publisher = publisher


def get_tenant_event_publisher() -> TenantEventPublisher:
    return _publisher if _publisher is not None else _FALLBACK
