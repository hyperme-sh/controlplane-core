"""Tenant-scoped domain event publishing.

Production: Azure Service Bus topic/envelope controlplane.events.v1.
Local leftover: Kafka on the same event_type strings. Do not expand Kafka.
Never put prompt bodies, enroll tokens, or Stripe secrets on the bus.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from controlplane_events import (
    AioKafkaEventProducer,
    CONTROLPLANE_EVENTS_V1,
    ControlPlaneEventEnvelope,
    dispatch_controlplane_envelope,
)
from controlplane_events.routing_consumer import Handler
from pydantic import BaseModel

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


@runtime_checkable
class ServiceBusReceiver(Protocol):
    async def receive_json(self) -> list[str]: ...


class MemoryServiceBusSender:
    """In-process Service Bus stand-in. Tests and local runs without Azure."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str | None]] = []

    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None:
        self.sent.append({"topic": topic, "body": body, "session_id": session_id})


class MemoryServiceBusReceiver:
    """In-process Service Bus receive stand-in. Tests without live Azure."""

    def __init__(self) -> None:
        self.inbox: list[str] = []
        self.received: list[str] = []

    def enqueue(self, body: str) -> None:
        self.inbox.append(body)

    async def receive_json(self) -> list[str]:
        batch = list(self.inbox)
        self.inbox.clear()
        self.received.extend(batch)
        return batch


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


class AzureServiceBusReceiver:
    """Lazy azure-servicebus subscription receiver. Not imported unless started."""

    def __init__(self, connection_string: str, topic: str, subscription: str) -> None:
        self._connection_string = connection_string
        self._topic = topic
        self._subscription = subscription
        self._client = None
        self._receiver = None

    async def start(self) -> None:
        try:
            from azure.servicebus.aio import ServiceBusClient
        except ImportError as exc:
            raise RuntimeError(
                "AZURE_SERVICE_BUS_CONNECTION_STRING is set but azure-servicebus "
                "is not installed. Production images must include azure-servicebus."
            ) from exc
        self._client = ServiceBusClient.from_connection_string(self._connection_string)
        await self._client.__aenter__()
        self._receiver = self._client.get_subscription_receiver(
            topic_name=self._topic,
            subscription_name=self._subscription,
        )
        await self._receiver.__aenter__()

    async def stop(self) -> None:
        if self._receiver is not None:
            await self._receiver.__aexit__(None, None, None)
            self._receiver = None
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def receive_json(self) -> list[str]:
        if self._receiver is None:
            raise RuntimeError("Service Bus receiver not started; call start() first")
        messages = await self._receiver.receive_messages(max_wait_time=5, max_message_count=16)
        bodies: list[str] = []
        for message in messages:
            bodies.append(str(message))
            await self._receiver.complete_message(message)
        return bodies


class ServiceBusRoutingConsumer:
    """Dispatch controlplane.events.v1 envelopes from a Service Bus receiver."""

    def __init__(
        self,
        receiver: ServiceBusReceiver,
        routes: dict[str, tuple[type[BaseModel], Handler]],
        *,
        topic: str = CONTROLPLANE_EVENTS_V1,
    ) -> None:
        self._receiver = receiver
        self._routes = routes
        self._topic = topic
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def handle_body(self, body: str) -> None:
        await dispatch_controlplane_envelope(body.encode("utf-8"), self._routes)

    async def drain(self) -> int:
        """Receive one in-process batch and dispatch. Tests use this; no live Azure."""

        bodies = await self._receiver.receive_json()
        for body in bodies:
            await self.handle_body(body)
        return len(bodies)

    async def start(self) -> None:
        start = getattr(self._receiver, "start", None)
        if start is not None:
            await start()
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Service Bus consumer started topic=%s", self._topic)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        stop = getattr(self._receiver, "stop", None)
        if stop is not None:
            await stop()
        logger.info("Service Bus consumer stopped topic=%s", self._topic)

    async def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self.drain()
                except Exception:
                    logger.exception("Service Bus receive failed; continuing")
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise


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
