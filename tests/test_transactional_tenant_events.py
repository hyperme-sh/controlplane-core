"""Business writes and tenant facts share one transaction; login is not enrollment."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from api.infrastructure.messaging import tenant_event_publisher as publishers
from api.usecases.tenant_service import TenantService
from api.usecases.user_service import UserService
from api.domain.dtos.tenant_dto import CreateTenantDto
from api.domain.dtos.user_dto import CreateUserDto
from controlplane_events.publisher import bind_outbox_session
from controlplane_api.utils import get_host_main_domain_name


class Transaction:
    def __init__(self):
        self.rows, self.events = {}, {}
        self.scope = ''
        self.fail_insert = False
        self.active = True

    @asynccontextmanager
    async def begin_nested(self):
        saved = deepcopy((self.rows, self.events, self.scope))
        try:
            yield self
        except BaseException:
            self.rows, self.events, self.scope = saved
            raise

    async def run_sync(self, call):
        return call(self)

    def in_transaction(self):
        return self.active

    def scalar(self, statement):
        return self.scope

    def execute(self, statement, values):
        sql = str(statement)
        value = None
        if 'set_config' in sql:
            self.scope = values['tenant']
        elif 'INSERT INTO service_outbox' in sql:
            if self.fail_insert:
                raise RuntimeError('outbox insert failed')
            assert self.scope == str(values['tenant'])
            if values['id'] not in self.events:
                self.events[values['id']] = values['envelope']
                value = values['id']
        else:
            value = self.events[values['id']]
        return SimpleNamespace(scalar_one_or_none=lambda:value, scalar_one=lambda:value)


def owner(session, monkeypatch):
    publisher = publishers.TransactionalTenantEventPublisher(None, 'user-service')
    monkeypatch.setattr(publishers, '_publisher', publisher)
    business_id = uuid.uuid4()
    async def create(data):
        session.rows[business_id] = data.model_dump(mode='json')
        return business_id
    repo = SimpleNamespace(session=session, create=create,
        single_or_none=AsyncMock(return_value=None),
        single_or_none_platform_email_ci=AsyncMock(return_value=None),
        get=AsyncMock(return_value=SimpleNamespace(id=business_id, tenant_id=None)))
    return publisher, repo, business_id


def operation(kind, repo):
    values = dict(first_name='Test', last_name='User', gender='prefer_not_to_say')
    if kind == 'tenant':
        return TenantService(repo).create_tenant(CreateTenantDto(
            name='tenant', subdomain='test.' + get_host_main_domain_name(), admin_email='test@example.com', **values))
    return UserService(repo, None, None).create_user(CreateUserDto(
        email='test@example.com', tenant_id=repo.session.scope or None, **values))


@pytest.mark.parametrize('kind', ['tenant', 'user'])
@pytest.mark.parametrize('outcome', ['commit', 'rollback', 'insert-failure'])
@pytest.mark.asyncio
async def test_business_and_outbox_are_atomic(kind, outcome, monkeypatch):
    session = Transaction()
    if kind == 'user':
        session.scope = str(uuid.uuid4())
    original_scope = session.scope
    publisher, repo, business_id = owner(session, monkeypatch)
    session.fail_insert = outcome == 'insert-failure'
    async def run():
        async with session.begin_nested():
            await operation(kind, repo)
            if outcome == 'rollback':
                raise RuntimeError('business rollback')
    if outcome == 'commit':
        await run()
        assert business_id in session.rows
        assert len(session.events) == 1
        event = next(iter(session.events.values()))
        assert event['tenant_id'] == (str(business_id) if kind == 'tenant' else original_scope)
    else:
        with pytest.raises(RuntimeError):
            await run()
        assert not session.rows and not session.events
    assert session.scope == original_scope


@pytest.mark.asyncio
async def test_unbound_publish_refused_and_bound_replay_is_stable(monkeypatch):
    session = Transaction();session.scope = str(uuid.uuid4())
    publisher, _, business_id = owner(session, monkeypatch)
    args = dict(tenant_id=uuid.UUID(session.scope), event_type='user.created.v1',
        correlation_id=str(business_id), payload={'user_id':str(business_id),
        'tenant_id':session.scope, 'email':'test@example.com'})
    with pytest.raises(RuntimeError, match='bound business session'):
        await publisher.publish(**args)
    with bind_outbox_session(session):
        first = await publisher.publish(**args)
        assert await publisher.publish(**args) == first
        assert len(session.events) == 1
        with pytest.raises(ValueError, match='different event facts'):
            await publisher.publish(**dict(args, payload={**args['payload'], 'email':'other@example.com'}))
    assert len(session.events) == 1


@pytest.mark.asyncio
async def test_oidc_projection_does_not_publish_or_create_tenant_membership(monkeypatch):
    session = Transaction()
    publisher, repo, business_id = owner(session, monkeypatch)
    roles = SimpleNamespace(single_or_none=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))
    result = await UserService(repo, None, roles).ensure_user_from_keycloak_claims(
        {'sub':'issuer-subject', 'email':'test@example.com', 'email_verified':True})
    assert result.id == business_id
    assert session.rows[business_id]['tenant_id'] is None
    assert not session.events
    repo.single_or_none.assert_awaited_once_with(keycloak_sub='issuer-subject', tenant_id=None)


@pytest.mark.asyncio
async def test_unscoped_legacy_user_is_not_a_tenant_event(monkeypatch):
    session = Transaction();_, repo, _ = owner(session, monkeypatch)
    await operation('user', repo)
    assert len(session.rows) == 1 and not session.events


def test_retired_transport_configuration_cannot_select_a_bus():
    publisher = publishers.resolve_event_publisher(service_name='user-service',
        service_bus_connection_string='retired', service_bus_sender=object())
    assert isinstance(publisher, publishers.TransactionalTenantEventPublisher)
    with pytest.raises(RuntimeError, match='Postgres outbox'):
        publishers.AzureServiceBusSender('retired')


def test_unconfigured_publisher_refuses_tenant_fact(monkeypatch):
    monkeypatch.setattr(publishers, '_publisher', None)
    with pytest.raises(RuntimeError, match='not configured'):
        publishers.get_tenant_event_publisher().publish_in_session(None)


def test_production_creation_uses_explicit_transaction_bound_writes():
    import ast
    import inspect
    from api.usecases import tenant_service, user_service
    for module in (tenant_service, user_service):
        tree = ast.parse(inspect.getsource(module))
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr == 'publish' for node in ast.walk(tree))
        assert 'publish_in_session' in inspect.getsource(module) or 'publish_new_tenant' in inspect.getsource(module)
    source = inspect.getsource(publishers)
    assert 'PostgresEventPublisher' not in source
    assert 'azure.servicebus' not in source


@pytest.mark.asyncio
async def test_tenant_user_cannot_write_another_tenants_outbox(monkeypatch):
    session = Transaction();session.scope = str(uuid.uuid4())
    publisher, _, business_id = owner(session, monkeypatch)
    with pytest.raises(AssertionError):
        publisher.publish_in_session(session, tenant_id=uuid.uuid4(),
            event_type='user.created.v1', correlation_id=str(business_id),
            payload={'user_id':str(business_id), 'email':'test@example.com'})
    assert not session.events
