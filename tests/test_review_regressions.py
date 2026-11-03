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


def test_webhook_has_signature_auth_without_user_login(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.interfaces.api_controllers import stripe_endpoint as endpoint
    app = FastAPI()
    @app.middleware('http')
    async def context(request, call_next):
        request.state.tenant_id = None
        return await call_next(request)
    app.include_router(endpoint.router)
    app.dependency_overrides[endpoint.get_stripe_setting_service] = lambda: SimpleNamespace(get_stripe_secret=AsyncMock(return_value=None))
    app.dependency_overrides[endpoint.get_billing_record_service] = lambda: object()
    monkeypatch.setattr(endpoint.settings, 'stripe_webhook_secret', 'test-only-secret')
    handled = AsyncMock()
    monkeypatch.setitem(endpoint.SUPPORTED_EVENTS, 'invoice.paid', handled)
    def verify(payload, sig_header, secret):
        assert secret == 'test-only-secret'
        if sig_header != 'valid':
            raise ValueError('invalid')
        return {'type':'invoice.paid','data':{'object':{'id':'test'}}}
    monkeypatch.setattr(endpoint.stripe.Webhook, 'construct_event', verify)
    with TestClient(app) as client:
        from controlplane_api.exceptions import UnauthorizedException
        with pytest.raises(UnauthorizedException):
            client.get('/configurations/stripe')
        assert client.post('/configurations/stripe/webhooks',content='{}').status_code == 400
        assert client.post('/configurations/stripe/webhooks',content='{}',headers={'Stripe-Signature':'valid'}).status_code == 200
    handled.assert_awaited_once()
    config_routes=[r for r in endpoint.router.routes if getattr(r,'path',None)=='/configurations/stripe']
    assert len(config_routes)==2 and all(len(r.dependencies)==2 for r in config_routes)

from test_oidc_platform_subject import database
