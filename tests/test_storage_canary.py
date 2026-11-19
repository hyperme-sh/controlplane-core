"""Real record routes/authentication with memory provider and revision doubles."""
import hashlib
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx
import jwt
import pytest

from api.domain.tenant_records import RecordConflict, TenantRecords
from api.infrastructure.security import api_key_exchange_jwt as exchange
from api.infrastructure.security.storage_session import request_operation, storage_session
from api.interfaces.api_controllers import tenant_records_endpoint as route
from api.storage.canary import CanaryRefused, Run, Target, cleanup, exercise


class Memory:
    def __init__(self): self.rows = {}; self.calls = []
    async def fetch(self, key): return self.rows.get(key)
    async def replace(self, key, data, etag):
        if (self.rows[key][1] if key in self.rows else None) != etag: raise RecordConflict()
        self.rows[key] = (data, '"' + hashlib.sha256(data).hexdigest() + '"')
        self.calls.append(('put', key))
    async def remove(self, key, etag):
        if key not in self.rows or self.rows[key][1] != etag: raise RecordConflict()
        del self.rows[key]; self.calls.append(('delete', key))


@pytest.fixture
def bound(monkeypatch):
    tenant, identity = uuid4(), uuid4()
    now = int(time.time()); run = Run(tenant, identity, now + 120, now)
    secret = 'unit-only-canary-signing-key-' + 'x'*40
    monkeypatch.setattr(exchange, 'settings', SimpleNamespace(usable_api_key_exchange_secret=lambda:secret))
    token = jwt.encode({'iss': exchange.ISSUER, 'sub': 'apikey:' + str(uuid4()),
        'azp': 'controlplane-api-key', 'spanopticon_auth': 'api_key', 'iat': now, 'exp': now+120,
        'https://spanopticon.dev/tenant_id': str(tenant),
        'scope': 'storage:records:read storage:records:write storage:records:delete'}, secret, algorithm='HS256')
    memory = Memory()
    def target(revision):
        async def admitted(request: Request):
            auth = request.headers.get('authorization', '')
            if not auth.startswith('Bearer '): raise HTTPException(401)
            selected = storage_session(auth[7:], request_operation(request.method, request.url.path))
            if request.headers.get('x-tenant-id') != str(selected): raise HTTPException(403)
            return TenantRecords(selected, memory)
        app = FastAPI()
        app.include_router(route.router, prefix='/api/v1')
        app.dependency_overrides[route.records_store] = admitted
        @app.get('/version')
        async def version(): return {'service': 'tenant-storage', 'git_sha': revision}
        return Target('https://storage.example.invalid', revision, token, enabled=True,
                      transport=httpx.ASGITransport(app=app))
    return run, memory, target('a'*40), target('b'*40)


@pytest.mark.asyncio
async def test_cross_target_crud_stale_refusal_and_cleanup(bound):
    run, memory, baseline, candidate = bound
    result = await exercise(baseline, candidate, run)
    assert result['marks'] == ['authority-refused', 'baseline-create-candidate-read',
                              'candidate-update-baseline-read', 'stale-mutations-refused', 'cleanup-absent']
    assert not memory.rows
    assert all('/fixtures/canary_' in key for _, key in memory.calls)


@pytest.mark.asyncio
async def test_expired_run_never_writes_but_independent_cleanup_recovers(bound):
    run, memory, baseline, candidate = bound
    store = TenantRecords(run.tenant, memory)
    await store.put('fixtures', 'canary_' + run.identity.hex, run.body(0), None)
    with pytest.raises(CanaryRefused, match='expired'):
        await exercise(baseline, candidate, run, clock=lambda:run.expires_at)
    assert len(memory.rows) == 1
    assert await cleanup(candidate, run) == {'cleanup': 'absent'}
    assert await cleanup(baseline, run) == {'cleanup': 'absent'}


@pytest.mark.asyncio
async def test_cleanup_never_deletes_foreign_or_changed_run(bound):
    run, memory, baseline, _ = bound
    await TenantRecords(run.tenant, memory).put('fixtures', 'canary_'+run.identity.hex, b'foreign', None)
    with pytest.raises(CanaryRefused, match='Foreign'):
        await cleanup(baseline, run)
    assert len(memory.rows) == 1


@pytest.mark.asyncio
async def test_disabled_or_misattributed_target_cannot_write(bound):
    run, memory, baseline, candidate = bound
    candidate.enabled = False
    with pytest.raises(CanaryRefused, match='disabled'): await exercise(baseline, candidate, run)
    candidate.enabled = True; candidate.revision = 'c'*40
    with pytest.raises(CanaryRefused, match='Wrong Storage'): await exercise(baseline, candidate, run)
    assert not memory.calls


@pytest.mark.asyncio
async def test_lost_update_reply_still_cleans_exact_owned_bytes(bound):
    run, memory, baseline, candidate = bound
    original = candidate.request
    async def lost(method, path, **kwargs):
        result = await original(method, path, **kwargs)
        if method == 'PUT': raise RuntimeError('lost reply')
        return result
    candidate.request = lost
    with pytest.raises(RuntimeError, match='lost reply'): await exercise(baseline, candidate, run)
    assert not memory.rows


@pytest.mark.asyncio
async def test_wrong_tenant_binding_is_refused_without_mutation(bound):
    run, memory, baseline, candidate = bound
    foreign = Run(uuid4(), run.identity, run.expires_at, run.created_at)
    with pytest.raises(CanaryRefused): await exercise(baseline, candidate, foreign)
    assert not memory.rows


@pytest.mark.parametrize('origin', ['http://storage.example.invalid', 'https://user:pass@storage.invalid',
                                  'https://storage.invalid/path', 'https://storage.invalid?token=secret'])
def test_origin_is_exact_and_credential_free(origin):
    with pytest.raises(CanaryRefused): Target(origin, 'a'*40, 'unit')


@pytest.mark.asyncio
async def test_storage_version_uses_existing_build_identity_without_database(monkeypatch):
    from api.storage_app import app
    monkeypatch.setenv('SERVICE_NAME', 'tenant-storage'); monkeypatch.setenv('GIT_SHA', 'd'*40)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://storage') as client:
        reply = await client.get('/version')
    assert reply.status_code == 200
    assert reply.json()['git_sha'] == 'd'*40
    assert reply.json()['service'] == 'tenant-storage'
