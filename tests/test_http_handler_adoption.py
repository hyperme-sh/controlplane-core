import uuid
from unittest.mock import AsyncMock
import pytest


@pytest.mark.asyncio
async def test_http_inbox_preserves_application_handler_contract(monkeypatch):
    from api.infrastructure.messaging.tenant_event_publisher import build_routing_consumer
    from controlplane_events import http_consumer
    from controlplane_events.payloads.payment_events import PaymentSucceededPayload
    observed = {}
    def capture(database_url, routes):
        observed.update(url=database_url, routes=routes)
        return observed
    monkeypatch.setattr(http_consumer, 'HttpRoutingConsumer', capture)
    handler = AsyncMock()
    result = build_routing_consumer(database_url='postgresql://unused', group_id='owner',
        routes={'payment.succeeded.v3': (PaymentSucceededPayload, handler)}, client_id='owner')
    model, deliver = result['routes']['payment.succeeded.v3']
    envelope, payload = object(), object()
    await deliver(envelope, payload, uuid.uuid4())
    assert model is PaymentSucceededPayload
    handler.assert_awaited_once_with(envelope, payload)
