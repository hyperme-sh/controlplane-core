"""Tenant facts share the business transaction and stable operation identity."""
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from controlplane_events.contracts import validate_event_envelope
from controlplane_events.delivery import MAX_DELIVERY_BYTES, delivery_body
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.publisher import refuse_forbidden_payload

_INSERT = text("""
INSERT INTO service_outbox(id, tenant_id, envelope)
VALUES (:id, :tenant, :envelope)
ON CONFLICT (id) DO NOTHING RETURNING id
""").bindparams(bindparam('envelope', type_=JSONB))


def enqueue_fact(session, *, service, tenant_id, event_type, payload, operation_id):
    if tenant_id is None or operation_id is None:
        raise ValueError('Tenant and stable business operation identity required')
    tenant = uuid.UUID(str(tenant_id))
    operation = uuid.UUID(str(operation_id))
    if not session.in_transaction():
        raise RuntimeError('Active business transaction required')
    event_id = uuid.uuid5(uuid.NAMESPACE_URL, f'{service}:{tenant}:{event_type}:{operation}')
    refuse_forbidden_payload(payload)
    envelope = ControlPlaneEventEnvelope(service=service, tenant_id=tenant,
        event_type=event_type, payload=payload, correlation_id=str(operation))
    validate_event_envelope(envelope)
    if len(delivery_body(event_id, envelope)) > MAX_DELIVERY_BYTES:
        raise ValueError('Event exceeds delivery limit')
    encoded = envelope.model_dump(mode='json')
    inserted = session.execute(_INSERT, {'id':event_id, 'tenant':tenant,
                                         'envelope':encoded}).scalar_one_or_none()
    if inserted is None:
        prior = session.execute(text('SELECT envelope FROM service_outbox WHERE id=:id AND tenant_id=:tenant'),
                                {'id':event_id, 'tenant':tenant}).scalar_one()
        fields = ('service', 'tenant_id', 'event_type', 'schema_version', 'payload', 'correlation_id')
        if any(prior.get(key) != encoded.get(key) for key in fields):
            raise ValueError('Business operation already has different event facts')
    return event_id


def publish_new_tenant(session, publisher, *, tenant_id, **kwargs):
    """The authorized creator writes the new tenant's fact and restores its context."""
    previous = session.scalar(text("SELECT current_setting('app.current_tenant_id', true)"))
    session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                    {'tenant':str(tenant_id)})
    # A failed SQL transaction is rolled back by the caller's savepoint; do not
    # mask its error by issuing restoration SQL in the aborted transaction.
    result = publisher.publish_in_session(session, tenant_id=tenant_id, **kwargs)
    session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
                    {'tenant':previous or ''})
    return result
