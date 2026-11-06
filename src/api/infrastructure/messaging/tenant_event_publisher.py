"""Tenant-scoped domain event publishing.

Default transport: the service-owned Postgres outbox with an HTTP relay.
Optional production transport: Azure Service Bus, same ``publish()`` protocol,
selected when ``AZURE_SERVICE_BUS_CONNECTION_STRING`` is set. Service Bus is
not required for local compose, the VM, or tests. Prod images install it with
the Docker build arg ``CONTROLPLANE_SERVICEBUS=1`` (the ``controlplane-core[servicebus]``
extra). Each consumer uses its own subscription, defaulting to that consumer's
group id. The subscription's max delivery count is what dead-letters a message;
this process abandons on handler failure and does not complete it.

MQTT is not this bus. It remains a future IoT edge bridge idea only.
Never put prompt bodies, enroll tokens, or provider credentials on the bus.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from controlplane_events import (
    CONTROLPLANE_EVENTS_V1,
    ControlPlaneEventEnvelope,
    RoutingControlPlaneConsumer,
    dispatch_controlplane_envelope,
    refuse_forbidden_payload,
)
from controlplane_events.routing_consumer import Handler
from controlplane_events.publisher import OwnedOutboxPublisher
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


@runtime_checkable
class ServiceBusSender(Protocol):
    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None: ...


@runtime_checkable
class ServiceBusDelivery(Protocol):
    body: str

    async def complete(self) -> None: ...

    async def abandon(self) -> None: ...


@runtime_checkable
class ServiceBusReceiver(Protocol):
    async def receive_batch(self) -> list[ServiceBusDelivery]: ...


class MemoryServiceBusSender:
    """In-process Service Bus stand-in. Tests and local runs without Azure."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str | None]] = []

    async def send_json(self, *, topic: str, body: str, session_id: str | None) -> None:
        self.sent.append({"topic": topic, "body": body, "session_id": session_id})


class MemoryServiceBusMessage:
    def __init__(self, body: str, owner: MemoryServiceBusReceiver) -> None:
        self.body = body
        self._owner = owner

    async def complete(self) -> None:
        self._owner.completed.append(self.body)

    async def abandon(self) -> None:
        self._owner.abandoned.append(self.body)
        self._owner.inbox.append(self.body)


class MemoryServiceBusReceiver:
    """In-process Service Bus receive stand-in. Completes only when asked."""

    def __init__(self) -> None:
        self.inbox: list[str] = []
        self.received: list[str] = []
        self.completed: list[str] = []
        self.abandoned: list[str] = []

    def enqueue(self, body: str) -> None:
        self.inbox.append(body)

    async def receive_batch(self) -> list[MemoryServiceBusMessage]:
        batch = [MemoryServiceBusMessage(body, self) for body in self.inbox]
        self.inbox.clear()
        self.received.extend(message.body for message in batch)
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
                "is not installed. Install controlplane-core[servicebus]."
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


def _service_bus_body_text(message: Any) -> str:
    """Read a Service Bus body as bytes. ``str(message)`` is not the payload."""

    body = message.body
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        raw = b"".join(body)
    return raw.decode("utf-8")


class _AzureServiceBusDelivery:
    def __init__(self, receiver: Any, raw: Any, body: str) -> None:
        self._receiver = receiver
        self._raw = raw
        self.body = body

    async def complete(self) -> None:
        await self._receiver.complete_message(self._raw)

    async def abandon(self) -> None:
        await self._receiver.abandon_message(self._raw)


class AzureServiceBusReceiver:
    """Lazy azure-servicebus subscription receiver. Completes only after dispatch."""

    def __init__(self, connection_string: str, topic: str, subscription: str) -> None:
        self._connection_string = connection_string
        self._topic = topic
        self.subscription = subscription
        self._client = None
        self._receiver = None

    async def start(self) -> None:
        try:
            from azure.servicebus.aio import ServiceBusClient
        except ImportError as exc:
            raise RuntimeError(
                "AZURE_SERVICE_BUS_CONNECTION_STRING is set but azure-servicebus "
                "is not installed. Install controlplane-core[servicebus]."
            ) from exc
        self._client = ServiceBusClient.from_connection_string(self._connection_string)
        await self._client.__aenter__()
        self._receiver = self._client.get_subscription_receiver(
            topic_name=self._topic,
            subscription_name=self.subscription,
        )
        await self._receiver.__aenter__()

    async def stop(self) -> None:
        if self._receiver is not None:
            await self._receiver.__aexit__(None, None, None)
            self._receiver = None
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def receive_batch(self) -> list[_AzureServiceBusDelivery]:
        if self._receiver is None:
            raise RuntimeError("Service Bus receiver not started; call start() first")
        messages = await self._receiver.receive_messages(max_wait_time=1, max_message_count=16)
        return [
            _AzureServiceBusDelivery(self._receiver, message, _service_bus_body_text(message))
            for message in messages
        ]


class ServiceBusRoutingConsumer:
    """Dispatch controlplane.events.v1 envelopes from a Service Bus receiver.

    Messages are completed only after the handler returns. A handler exception
    abandons the message so the broker can redeliver it.
    """

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
        self.subscription = getattr(receiver, "subscription", None)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def handle_body(self, body: str) -> None:
        await dispatch_controlplane_envelope(body.encode("utf-8"), self._routes)

    async def drain(self) -> int:
        """Receive one batch and dispatch. Tests use this; no live Azure."""

        messages = await self._receiver.receive_batch()
        for message in messages:
            try:
                await self.handle_body(message.body)
            except Exception:
                logger.exception("Service Bus dispatch failed; abandoning for retry")
                await message.abandon()
            else:
                await message.complete()
        return len(messages)

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
    """Optional publisher for controlplane.events.v1 on Azure Service Bus."""

    def __init__(
        self,
        sender: ServiceBusSender,
        topic: str,
        service_name: str,
    ) -> None:
        self._sender = sender
        self._topic = topic
        self._service_name = service_name

    async def start(self) -> None:
        start = getattr(self._sender, "start", None)
        if start is not None:
            await start()

    async def stop(self) -> None:
        stop = getattr(self._sender, "stop", None)
        if stop is not None:
            await stop()

    async def publish(
        self,
        *,
        tenant_id: uuid.UUID | None,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        refuse_forbidden_payload(payload)
        env = ControlPlaneEventEnvelope(
            event_type=event_type,
            service=self._service_name,
            tenant_id=tenant_id,
            payload=payload,
            correlation_id=correlation_id,
        )
        await self._sender.send_json(
            topic=self._topic,
            body=env.model_dump_json(),
            session_id=str(tenant_id) if tenant_id is not None else None,
        )


def resolve_event_publisher(
    *,
    service_name: str,
    service_bus_connection_string: str | None,
    database_url: str | None = None,
    service_bus_sender: ServiceBusSender | None = None,
) -> TenantEventPublisher:
    """Service Bus when a connection string or sender is set. Otherwise Postgres."""

    if service_bus_connection_string or service_bus_sender is not None:
        sender = service_bus_sender or AzureServiceBusSender(service_bus_connection_string or "")
        return ServiceBusTenantEventPublisher(sender, CONTROLPLANE_EVENTS_V1, service_name)
    if database_url:
        return OwnedOutboxPublisher(database_url, service_name)
    return NoOpTenantEventPublisher()


def build_routing_consumer(
    *,
    database_url: str,
    group_id: str,
    routes: dict[str, tuple[type[BaseModel], Handler]],
    client_id: str,
    service_bus_connection_string: str | None = None,
    service_bus_subscription: str | None = None,
) -> RoutingControlPlaneConsumer | ServiceBusRoutingConsumer:
    """Postgres poller, or Service Bus when the connection string is set."""

    if service_bus_connection_string:
        receiver = AzureServiceBusReceiver(
            service_bus_connection_string,
            CONTROLPLANE_EVENTS_V1,
            service_bus_subscription or group_id,
        )
        return ServiceBusRoutingConsumer(receiver, routes, topic=CONTROLPLANE_EVENTS_V1)
    from controlplane_events.http_consumer import HttpRoutingConsumer
    return HttpRoutingConsumer(database_url, routes)



_publisher: TenantEventPublisher | None = None
_FALLBACK: TenantEventPublisher = NoOpTenantEventPublisher()


def set_tenant_event_publisher(publisher: TenantEventPublisher | None) -> None:
    global _publisher
    _publisher = publisher


def get_tenant_event_publisher() -> TenantEventPublisher:
    return _publisher if _publisher is not None else _FALLBACK


async def install_tenant_event_publisher(*, service_name: str) -> TenantEventPublisher:
    """Wire the process publisher. Postgres unless Service Bus is configured."""

    from controlplane_api.config import settings

    publisher = resolve_event_publisher(
        service_name=service_name,
        service_bus_connection_string=settings.azure_service_bus_connection_string,
        database_url=settings.database_url,
    )
    start = getattr(publisher, "start", None)
    if start is not None:
        await start()
    set_tenant_event_publisher(publisher)
    return publisher


async def shutdown_tenant_event_publisher() -> None:
    publisher = get_tenant_event_publisher()
    set_tenant_event_publisher(None)
    if publisher is _FALLBACK:
        return
    stop = getattr(publisher, "stop", None)
    if stop is not None:
        await stop()
