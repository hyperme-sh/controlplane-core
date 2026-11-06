import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import text


def test_celery_repeated_event_loops(database, monkeypatch):
    from api.infrastructure.messaging import celery_db
    from controlplane_tenancy.db_session import get_db_session
    monkeypatch.setattr(celery_db, '_engine', None)
    monkeypatch.setattr(celery_db.settings, 'database_url', database)
    async def work():
        return await get_db_session().scalar(text('SELECT 42'))
    try:
        assert asyncio.run(celery_db.run_with_db_session(None, work)) == 42
        assert asyncio.run(celery_db.run_with_db_session(None, work)) == 42
    finally:
        asyncio.run(celery_db._engine.dispose())


@pytest.mark.asyncio
async def test_oidc_email_fallback_never_uses_tenant_row():
    from api.usecases.user_service import UserService
    from controlplane_api.exceptions import InvalidOperationException
    repo = SimpleNamespace(single_or_none=AsyncMock(return_value=None),
        single_or_none_platform_email_ci=AsyncMock(return_value=None), update=AsyncMock())
    roles = SimpleNamespace(single_or_none=AsyncMock(return_value=None))
    service = UserService(repo, AsyncMock(), roles)
    with pytest.raises(InvalidOperationException, match='GUEST role'):
        await service.ensure_user_from_keycloak_claims({'sub':'new-sub','email':'tenant@example.com','email_verified':True})
    repo.single_or_none.assert_awaited_once_with(keycloak_sub='new-sub', tenant_id=None)
    repo.update.assert_not_awaited()



from test_oidc_platform_subject import database


@pytest.mark.asyncio
async def test_subscription_audit_uses_persisted_creator_after_database_fence(monkeypatch):
    import uuid
    from api.infrastructure.persistence.repositories.subscription_plan_repository_impl import SubscriptionPlanRepository
    from api.domain.dtos.subcription_plan_dto import CreateSubscriptionPlanDto
    repository = SubscriptionPlanRepository()
    # The database may clear a retired creator during insertion. The request's
    # historical user ID must not be restored in the resulting audit record.
    repository.create = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), user_id=None))
    repository.add_audit_log = AsyncMock()
    await repository.create_subscription_plan(CreateSubscriptionPlanDto(
        tenant_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), plan_id='paid',
        is_trial=False, actor='payment', plan_level={'premium': 2}))
    assert repository.add_audit_log.await_args.args[0].user_id is None
