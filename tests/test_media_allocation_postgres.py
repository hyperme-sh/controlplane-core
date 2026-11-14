"""Allocation PostgreSQL adapter unit contracts; real SQL runs separately in CI."""
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.media_allocation_store import Reservation
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_allocation_postgres import PostgresAllocationStore
from test_media_allocation_grant import grant


@asynccontextmanager
async def context(value):
    yield value


@pytest.fixture
def registry(grant):
    scope = grant[0]
    candidate = Reservation(scope, str(UUID(int=4)), 1000, 1300)
    cursor = Mock(execute=AsyncMock(), fetchone=AsyncMock())
    connection = Mock(transaction=lambda: context(None), cursor=lambda **_: context(cursor))
    store = PostgresAllocationStore(scope.tenant, scope.issuer, lambda: context(connection))
    return store, cursor, candidate


def row(candidate, phase=0):
    scope = candidate.scope
    return dict(tenant_id=UUID(scope.tenant), issuer_id=UUID(scope.issuer), object_id=scope.object,
                owner_principal=scope.owner, source_id=UUID(scope.source), policy_digest=scope.policy_digest,
                ownership_digest=scope.ownership_digest, allocation_id=UUID(candidate.allocation),
                issued_at=candidate.issued_at, expires_at=candidate.expires_at, phase=phase)


@pytest.mark.asyncio
async def test_new_reservation_and_expired_original_retry(registry):
    store, cursor, candidate = registry
    guard = Mock()
    cursor.fetchone.side_effect = [{'id': candidate.scope.tenant}, None, None, {'count': 63}]
    assert await store.reserve(candidate, now=1100, before_write=guard) == candidate
    guard.assert_called_once_with()
    assert 'INSERT INTO' in cursor.execute.call_args.args[0].as_string()
    cursor.fetchone.side_effect = [{'id': candidate.scope.tenant}, row(candidate)]
    later = replace(candidate, allocation=str(UUID(int=9)), issued_at=1400, expires_at=1700)
    assert await store.reserve(later, now=1500, before_write=guard) == candidate
    assert guard.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', [1, 2])
async def test_retirement_never_reallocates(registry, phase):
    store, cursor, candidate = registry
    cursor.fetchone.side_effect = [{'id': candidate.scope.tenant}, row(candidate, phase)]
    guard = Mock()
    with pytest.raises(RecordConflict, match='permanent'):
        await store.reserve(candidate, now=1100, before_write=guard)
    guard.assert_not_called()


@pytest.mark.asyncio
async def test_scope_duplicate_missing_and_quota_refusal(registry):
    store, cursor, candidate = registry
    cases = [([None], AllocationRejected, 'unavailable'),
             ([{'id': 1}, {**row(candidate), 'policy_digest': 'f'*64}], RecordConflict, 'scope'),
             ([{'id': 1}, None, {'exists': 1}], RecordConflict, 'identifier'),
             ([{'id': 1}, None, None, {'count': 64}], AllocationRejected, 'quota')]
    for replies, error, message in cases:
        cursor.fetchone.side_effect = replies
        guard = Mock()
        with pytest.raises(error, match=message):
            await store.reserve(candidate, now=1100, before_write=guard)
        guard.assert_not_called()


@pytest.mark.asyncio
async def test_current_authority_guard_on_insert_and_retry(registry):
    store, cursor, candidate = registry
    for replies in [[{'id': 1}, None, None, {'count': 0}], [{'id': 1}, row(candidate)]]:
        cursor.fetchone.side_effect = replies
        cursor.execute.reset_mock()
        with pytest.raises(PermissionError):
            await store.reserve(candidate, now=1100, before_write=Mock(side_effect=PermissionError))
        assert not any('INSERT' in str(call.args[0]) for call in cursor.execute.call_args_list)


@pytest.mark.asyncio
async def test_invalid_input_never_opens_transaction(registry):
    store, cursor, candidate = registry
    for changed in [replace(candidate, scope=replace(candidate.scope, tenant=str(UUID(int=9)))),
                    replace(candidate, scope=replace(candidate.scope, issuer=str(UUID(int=9)))),
                    replace(candidate, allocation='bad')]:
        with pytest.raises(AllocationRejected):
            await store.reserve(changed, now=1100, before_write=Mock())
    for now in [True, 999, 1300]:
        with pytest.raises(AllocationRejected):
            await store.reserve(candidate, now=now, before_write=Mock())
    cursor.execute.assert_not_called()
    for schema in [None, 'public;DROP', 'pg_temp.x', 'A', 'a'*64]:
        with pytest.raises(ValueError):
            PostgresAllocationStore(store.tenant, store.issuer, Mock(), schema=schema)
