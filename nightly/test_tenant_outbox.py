"""Scheduled Postgres contracts using only the Infra-owned disposable fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from controlplane_db.models import Tenant, User
from controlplane_api.utils import get_host_main_domain_name
from api.common import base_repository
from api.domain.dtos.tenant_dto import CreateTenantDto
from api.domain.dtos.user_dto import CreateUserDto
from api.infrastructure.persistence.repositories.tenant_repository_impl import TenantRepository
from api.infrastructure.persistence.repositories.user_repository_impl import UserRepository
from api.infrastructure.persistence.repositories.role_repository_impl import RoleRepository
from api.infrastructure.messaging import tenant_event_publisher as publishers
from api.usecases.tenant_service import TenantService
from api.usecases.user_service import UserService


def fixture_command(action):
    subprocess.run([sys.executable, os.environ['CORE_OUTBOX_FIXTURE_SCRIPT'], action], check=True, timeout=60)


@pytest.fixture
def fixture(monkeypatch):
    fixture_command('reset-case')
    root = Path(os.environ['CORE_OUTBOX_FIXTURE_SCRIPT']).resolve().parents[2]
    config = json.loads((root/'config/fixtures/core-outbox/scopes.json').read_text())
    url = make_url((Path(os.environ['CORE_OUTBOX_FIXTURE_DIR'])/'owner-url').read_text()).set(drivername='postgresql+asyncpg')
    monkeypatch.setattr(publishers, '_publisher', publishers.TransactionalTenantEventPublisher(None, 'user-service'))
    return url, config


async def invoke(operation, session, config, monkeypatch):
    monkeypatch.setattr(base_repository, 'get_db_session', lambda:session)
    values = dict(first_name='Test', last_name='User', gender='prefer_not_to_say')
    if operation == 'tenant':
        repo = TenantRepository();repo.add_audit_log = AsyncMock()
        return await TenantService(repo).create_tenant(CreateTenantDto(name='outbox-created',
            subdomain='outbox.'+get_host_main_domain_name(), admin_email='test@example.com', **values))
    repo = UserRepository();repo.add_audit_log = AsyncMock()
    return await UserService(repo, None, None).create_user(CreateUserDto(
        email='test@example.com', tenant_id=config['tenant_id'], **values))


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['tenant', 'user'])
@pytest.mark.parametrize('outcome', ['commit', 'rollback', 'insert_failure', 'retry'])
async def test_tenant_transaction(fixture, monkeypatch, operation, outcome):
    url, config = fixture
    if outcome == 'insert_failure': fixture_command('break-outbox')
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            transaction = await session.begin()
            scope = '' if operation == 'tenant' else config['tenant_id']
            await session.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':scope})
            if outcome == 'insert_failure':
                from sqlalchemy.exc import IntegrityError
                with pytest.raises(IntegrityError):
                    await invoke(operation, session, config, monkeypatch)
                # Catching the failure must not allow the business row to commit alone.
                await transaction.commit()
                business_id = None
                tenant = scope or config['tenant_id']
            else:
                business_id = await invoke(operation, session, config, monkeypatch)
                tenant = str(business_id) if operation == 'tenant' else scope
                assert await session.scalar(text("SELECT current_setting('app.current_tenant_id',true)")) == scope
                await session.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':tenant})
                event = (await session.execute(text('SELECT envelope FROM service_outbox'))).scalar_one()
                assert event['tenant_id'] == tenant
                if outcome == 'retry':
                    publisher = publishers.get_tenant_event_publisher()
                    await session.run_sync(lambda db: publisher.publish_in_session(db,
                        tenant_id=uuid.UUID(tenant), event_type=event['event_type'],
                        payload=event['payload'], correlation_id=str(business_id)))
                    assert await session.scalar(text('SELECT count(*) FROM service_outbox')) == 1
                if outcome == 'rollback': await transaction.rollback()
                else: await transaction.commit()
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':tenant})
                expected = int(outcome in ('commit', 'retry'))
                assert await session.scalar(text('SELECT count(*) FROM service_outbox')) == expected
                if operation == 'tenant':
                    rows = (await session.scalars(select(Tenant).where(Tenant.name=='outbox-created'))).all()
                else:
                    rows = (await session.scalars(select(User).where(User.email=='test@example.com'))).all()
                assert len(rows) == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_oidc_identity_does_not_emit_a_tenant_fact(fixture, monkeypatch):
    url, config = fixture;engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            async with session.begin():
                monkeypatch.setattr(base_repository, 'get_db_session', lambda:session)
                repo = UserRepository();repo.add_audit_log = AsyncMock()
                service = UserService(repo, None, RoleRepository())
                user = await service.ensure_user_from_keycloak_claims(
                    {'sub':'fixture-subject', 'email':'identity@example.com','email_verified':True})
                assert user.tenant_id is None
                assert await session.scalar(text('SELECT count(*) FROM service_outbox')) == 0
            async with session.begin():
                assert len((await session.scalars(select(User).where(User.keycloak_sub=='fixture-subject'))).all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_insert_is_refused(fixture):
    from sqlalchemy.exc import DBAPIError
    url, config = fixture;engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_tenant_id',:tenant,true)"), {'tenant':config['tenant_id']})
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.run_sync(lambda db: publishers.get_tenant_event_publisher().publish_in_session(db,
                            tenant_id=uuid.UUID(config['other_tenant_id']), event_type='user.created.v1',
                            correlation_id=str(uuid.uuid4()), payload={'user_id':str(uuid.uuid4()), 'email':'other@example.com'}))
                assert await session.scalar(text('SELECT count(*) FROM service_outbox')) == 0
    finally:
        await engine.dispose()
