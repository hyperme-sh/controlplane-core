"""Source-authenticated allocation signing after exclusive durable reservation."""
import base64
import json
import re
from collections.abc import Callable, Mapping
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from api.domain.media_allocation_grant import (
    AllocationRejected, AllocationScope, canonical_message, verify_allocation_grant,
    verify_allocation_request, _integer, _uuid,
)
from api.domain.media_allocation_store import AllocationStore, Reservation


class AllocationIssuer:
    def __init__(self, store: AllocationStore, *, source: str, policy_digest: str,
                 policy_until: int, request_keys: Mapping[str, Ed25519PublicKey],
                 key_id: str, signing_key: Ed25519PrivateKey, clock: Callable[[], int]):
        _uuid(source)
        _integer(policy_until)
        if (not re.fullmatch(r'[0-9a-f]{64}', policy_digest)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key_id)
                or not isinstance(signing_key, Ed25519PrivateKey)
                or not 1 <= len(request_keys) <= 16
                or any(not isinstance(key, Ed25519PublicKey) for key in request_keys.values())):
            raise AllocationRejected('Invalid allocation issuer configuration')
        if any(key.public_bytes_raw() == signing_key.public_key().public_bytes_raw() for key in request_keys.values()):
            raise AllocationRejected('Source and issuer keys must differ')
        self.store, self.source, self.policy_digest = store, source, policy_digest
        self.policy_until, self.request_keys = policy_until, dict(request_keys)
        self.key_id, self.signing_key, self.clock = key_id, signing_key, clock

    async def issue(self, raw_request: bytes, *, object: str, owner: str,
                    ownership_digest: str, cancel: Callable[[], None]) -> bytes:
        """Authenticate the source proof, reserve, then sign the original grant.

        Object/owner/digest are untrusted candidates until the configured source's
        signature matches this exact scope. Tenant, issuer, source and policy are
        fixed configuration. No browser policy, token keys or caller nonce chooses
        the durable allocation ID. Cancellation after commit never frees capacity.
        """
        at = self.clock()
        _integer(at)
        scope = AllocationScope(self.store.issuer, self.store.tenant, object, owner,
                                self.source, self.policy_digest, ownership_digest)
        def guard():
            cancel()
            current = self.clock()
            _integer(current)
            if current != at or current >= self.policy_until:
                raise AllocationRejected('Allocation authority expired; reconcile')
            verify_allocation_request(raw_request, expected=scope, keys=self.request_keys, now=current)
        guard()
        candidate = Reservation(scope, str(uuid4()), at, min(at + 300, self.policy_until))
        held = await self.store.reserve(candidate, now=at, before_write=guard)
        guard()
        claims = {**held.scope.claims(), 'version': 1, 'allocation': held.allocation,
                  'issuedAt': held.issued_at, 'expiresAt': held.expires_at}
        signature = self.signing_key.sign(canonical_message(self.key_id, claims))
        raw = json.dumps({'keyId': self.key_id, 'claims': claims,
                          'signature': base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')},
                         sort_keys=True, separators=(',', ':')).encode('ascii')
        # An old reservation is not renewed merely because a new request is valid.
        verify_allocation_grant(raw, expected=scope, keys={self.key_id: self.signing_key.public_key()}, now=at)
        guard()
        return raw


# Preserve the original import while keeping the wire adapter independently consumable.
from .media_allocation_request import AllocationRequestSigner  # noqa: E402,F401
