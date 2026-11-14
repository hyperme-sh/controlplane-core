"""Authenticated source proof to durable signed grant, using fixture keys only."""
import json
from dataclasses import replace
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.domain.media_allocation_grant import (
    AllocationRejected, REQUEST_DOMAIN, canonical_message, verify_allocation_grant,
)
from api.domain.media_allocation_store import AllocationStore
from api.domain.media_allocation_issuer import AllocationIssuer
from test_media_allocation_grant import grant, b64
from test_media_allocation_store import Backend


@pytest.fixture
def issuer(grant):
    scope, source_key, _, _, _ = grant
    backend = Backend()
    store = AllocationStore(scope.tenant, scope.issuer, backend)
    clock = [1100]
    key = Ed25519PrivateKey.from_private_bytes(b'\x08' * 32)
    def configured(**overrides):
        return AllocationIssuer(store, **{'source':scope.source, 'policy_digest':scope.policy_digest,
            'policy_until':2000, 'request_keys':{'source':source_key.public_key()}, 'key_id':'storage',
            'signing_key':key, 'clock':lambda:clock[0], **overrides})
    def request(changes=None, domain=REQUEST_DOMAIN):
        claims={**scope.claims(), 'version':1, 'allocation':str(UUID(int=8)),
                'issuedAt':clock[0], 'expiresAt':clock[0]+30, **(changes or {})}
        return json.dumps({'keyId':'source','claims':claims,
            'signature':b64(source_key.sign(canonical_message('source',claims,domain=domain)))}).encode()
    async def issue(raw=None, instance=None, **changes):
        return await (instance or configured()).issue(request() if raw is None else raw,
            **{'object':scope.object,'owner':scope.owner,'ownership_digest':scope.ownership_digest,
               'cancel':lambda:None, **changes})
    return scope, backend, store, clock, key, configured, request, issue


@pytest.mark.asyncio
async def test_authenticated_request_reserves_before_signed_grant_and_retries(issuer):
    scope, backend, store, _, key, _, request, issue = issuer
    with pytest.raises(AllocationRejected,match='unavailable'):
        await issue()
    await store.initialize()
    raw = await issue()
    grant = verify_allocation_grant(raw,expected=scope,keys={'storage':key.public_key()},now=1100)
    assert grant.allocation != str(UUID(int=8))
    assert grant.expires_at == 1400
    assert await issue(request({'allocation':str(UUID(int=9))})) == raw
    assert backend.writes == 2


@pytest.mark.asyncio
async def test_forgery_cross_scope_and_grant_domain_do_not_reserve(issuer):
    scope, backend, store, _, _, _, request, issue = issuer
    await store.initialize()
    for raw in [b'{}', request({'tenant':str(UUID(int=9))}),request({'source':str(UUID(int=9))}),
                request({'policyDigest':'f'*64}),request({'expiresAt':1131}),
                request(domain=b'spanopticon-media-allocation-v1\x00')]:
        with pytest.raises(AllocationRejected):
            await issue(raw)
    with pytest.raises(AllocationRejected):
        await issue(ownership_digest='f'*64)
    assert backend.writes == 1


@pytest.mark.asyncio
async def test_expiry_delayed_provider_and_cancellation_never_sign(issuer):
    _, backend, store, clock, _, configured, _, issue = issuer
    await store.initialize()
    with pytest.raises(AllocationRejected):
        await issue(instance=configured(policy_until=1100))
    original_fetch=backend.fetch
    async def delayed(key):
        result=await original_fetch(key)
        clock[0]+=1
        return result
    backend.fetch=delayed
    with pytest.raises(AllocationRejected):
        await issue()
    assert backend.writes == 1
    backend.fetch=original_fetch
    def cancelled():raise TimeoutError('cancelled')
    with pytest.raises(TimeoutError):
        await issue(cancel=cancelled)
    assert backend.writes == 1


@pytest.mark.asyncio
async def test_post_commit_timeout_reconciles_and_expired_allocation_is_not_renewed(issuer):
    _, backend, store, clock, _, _, _, issue = issuer
    await store.initialize()
    original_replace=backend.replace
    async def delayed(key,data,etag):
        await original_replace(key,data,etag)
        clock[0]+=1
    backend.replace=delayed
    with pytest.raises(AllocationRejected):
        await issue()
    assert backend.writes == 2
    backend.replace=original_replace
    assert await issue()
    clock[0]=1500
    with pytest.raises(AllocationRejected):
        await issue()
    assert backend.writes == 2


def test_configuration_rejects_invalid_or_shared_trust_keys(issuer,grant):
    configured=issuer[5]
    for values in [{'source':'invalid'},{'policy_digest':'bad'},{'key_id':'bad id'},
                   {'request_keys':{}},{'request_keys':{'bad':None}},
                   {'signing_key':grant[1]}, {'policy_until':True}]:
        with pytest.raises(AllocationRejected):configured(**values)
