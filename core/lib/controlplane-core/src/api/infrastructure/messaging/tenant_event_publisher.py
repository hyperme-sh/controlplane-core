"""Tenant-scoped domain event publishing.

Production: Azure Service Bus topic/envelope controlplane.events.v1.
Local leftover: Kafka on the same event_type strings. Do not expand Kafka.
Never put prompt bodies, enroll tokens, or Stripe secrets on the bus.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from controlplane_events import (
    AioKafkaEventProducer,
    CONTROLPLANE_EVENTS_V1,
    ControlPlaneEventEnvelope,
)

logger = logging.getLogger(__name__)


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


@runtime_checkable
class ServiceBusSender(Protocol):
    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None: ...


class MemoryServiceBusSender:
    """In-process Service Bus stand-in. Tests and local runs without Azure."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str | None]] = []

    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None:
        self.sent.append({"topic": topic, "body": body, "session_id": session_id})


class AzureServiceBusSender:
    """Lazy azure-servicebus sender. Not imported unless a connection string is set."""

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string
        self._client = None

    async def start(self) -> None:
        try:
            from azure.servicebus.aio import ServiceBusClient
        except ImportError as exc:
            raise RuntimeError(
                "AZURE_SERVICE_BUS_CONNECTION_STRING is set but azure-servicebus "
                "is not installed. Production images must include azure-servicebus."
            ) from exc
        self._client = ServiceBusClient.from_connection_string(self._connection_string)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None:
        if self._client is None:
            raise RuntimeError("Service Bus sender not started; call start() first")
        sender = self._client.get_topic_sender(topic)
        async with sender:
            from azure.servicebus import ServiceBusMessage

            props = {"tenant_id": session_id} if session_id else None
            await sender.send_messages(
                ServiceBusMessage(
                    body,
                    content_type="application/json",
                    application_properties=props,
                )
            )


class ServiceBusTenantEventPublisher:
    """Production publisher for controlplane.events.v1 on Azure Service Bus."""

    def __init__(
        self,
        sender: ServiceBusSender,
        topic: str,
        service_name: str,
    ) -> None:
        self._sender = sender
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
        for forbidden in ("prompt", "messages", "device_secret", "enroll_token"):
            if forbidden in payload:
                raise ValueError(f"refusing to publish {forbidden} on controlplane.events.v1")
        await self._sender.send_json(
            topic=self._topic,
            body=env.model_dump_json(),
            session_id=str(tenant_id) if tenant_id is not None else None,
        )


def resolve_event_publisher(
    *,
    service_name: str,
    service_bus_connection_string: str | None,
    kafka_producer: AioKafkaEventProducer | None = None,
    kafka_topic: str = CONTROLPLANE_EVENTS_V1,
    service_bus_sender: ServiceBusSender | None = None,
) -> TenantEventPublisher:
    """Prefer Azure Service Bus. Kafka is leftover local only."""

    if service_bus_connection_string or service_bus_sender is not None:
        sender = service_bus_sender or AzureServiceBusSender(service_bus_connection_string or "")
        return ServiceBusTenantEventPublisher(sender, CONTROLPLANE_EVENTS_V1, service_name)
    if kafka_producer is not None:
        return KafkaTenantEventPublisher(kafka_producer, kafka_topic, service_name)
    return NoOpTenantEventPublisher()


_publisher: TenantEventPublisher | None = None
_FALLBACK: TenantEventPublisher = NoOpTenantEventPublisher()


def set_tenant_event_publisher(publisher: TenantEventPublisher | None) -> None:
    global _publisher
    _publisher = publisher


def get_tenant_event_publisher() -> TenantEventPublisher:
    return _publisher if _publisher is not None else _FALLBACK
