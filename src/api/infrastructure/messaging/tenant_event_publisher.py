"""Tenant facts use the caller's transaction and the owner Postgres outbox.

Legacy transport arguments remain accepted during independent peer upgrades,
but neither publishing nor consumer resolution selects a cloud message bus.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol, runtime_checkable

from controlplane_events import (
    CONTROLPLANE_EVENTS_V1,
    ControlPlaneEventEnvelope,
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
    """Retained import name; the transport is retired."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError('Use the owner Postgres outbox and HTTP relay')


class AzureServiceBusReceiver(AzureServiceBusSender):
    pass


class ServiceBusRoutingConsumer(AzureServiceBusSender):
    """Retained import name; consumers use the owner HTTP inbox."""


class ServiceBusTenantEventPublisher(AzureServiceBusSender):
    """Retained import name; direct transport publication is refused."""


class TransactionalTenantEventPublisher(OwnedOutboxPublisher):
    """Never create a transaction separate from a business mutation."""
    async def start(self):
        pass

    async def stop(self):
        pass

    def publish_in_session(self, session, *, tenant_id, event_type, payload, correlation_id=None):
        from api.infrastructure.messaging.transactional_outbox import enqueue_fact
        return enqueue_fact(session, service=self._service_name, tenant_id=tenant_id,
                            event_type=event_type, payload=payload, operation_id=correlation_id)

    async def publish(self, **kwargs):
        # Use the pinned event library's binding, including existing consumers.
        from controlplane_events.publisher import _outbox_session
        session = _outbox_session.get()
        if session is None:
            raise RuntimeError('State-changing publication requires a bound business session')
        return self.publish_in_session(session, **kwargs)



def resolve_event_publisher(
    *,
    service_name: str,
    service_bus_connection_string: str | None = None,
    database_url: str | None = None,
    service_bus_sender: ServiceBusSender | None = None,
) -> TenantEventPublisher:
    """Always return the transaction-bound owner publisher."""
    return TransactionalTenantEventPublisher(database_url, service_name)


def build_routing_consumer(
    *,
    database_url: str,
    group_id: str,
    routes: dict[str, tuple[type[BaseModel], Handler]],
    client_id: str,
    service_bus_connection_string: str | None = None,
    service_bus_subscription: str | None = None,
):
    """Owner HTTP inbox, preserving the existing handler signature."""

    from controlplane_events.http_consumer import HttpRoutingConsumer
    def adapt(handler):
        async def deliver(envelope, payload, event_id):
            # The HTTP inbox owns receipt identity. Preserve the established
            # two-argument application handler contract across transports.
            await handler(envelope, payload)
        return deliver
    return HttpRoutingConsumer(database_url, {
        event_type: (model, adapt(handler))
        for event_type, (model, handler) in routes.items()
    })



_publisher: TenantEventPublisher | None = None
class UnconfiguredPublisher:
    def publish_in_session(self, *args, **kwargs):
        raise RuntimeError('Tenant event publisher is not configured')

    async def publish(self, *args, **kwargs):
        raise RuntimeError('Tenant event publisher is not configured')


_FALLBACK: TenantEventPublisher = UnconfiguredPublisher()


def set_tenant_event_publisher(publisher: TenantEventPublisher | None) -> None:
    global _publisher
    _publisher = publisher


def get_tenant_event_publisher() -> TenantEventPublisher:
    return _publisher if _publisher is not None else _FALLBACK


async def install_tenant_event_publisher(*, service_name: str) -> TenantEventPublisher:
    """Wire transaction-bound publication without opening a second engine."""

    from controlplane_api.config import settings

    publisher = resolve_event_publisher(
        service_name=service_name,
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
