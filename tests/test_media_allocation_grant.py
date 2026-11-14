"""Allocation proof unit contracts; fixtures confer no deployed authority."""
import base64
from dataclasses import FrozenInstanceError, replace
import json
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.domain.media_allocation_grant import (
    AllocationRejected, AllocationScope, MAX_GRANT_BYTES, canonical_message,
    verify_allocation_grant,
)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def grant():
    point = ec.derive_private_key(7, ec.SECP256R1()).public_key().public_numbers()
    x, y = point.x.to_bytes(32), point.y.to_bytes(32)
    scope = AllocationScope(str(UUID(int=1)), str(UUID(int=2)),
        "media_p_" + (x + y).hex() + "_" + "a" * 32,
        b64(x) + "." + b64(y), str(UUID(int=3)), "b" * 64, "c" * 64)
    key = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
    claims = {**scope.claims(), "version": 1, "allocation": str(UUID(int=4)), "issuedAt": 1000, "expiresAt": 1300}
    def encode(changes=None, *, key_id="fixture", signing_key=key, envelope_changes=None):
        value = {**claims, **(changes or {})}
        # Independent encoder for the specified signed wire bytes.
        message = b"spanopticon-media-allocation-v1\x00" + json.dumps(
            {"keyId": key_id, "claims": value}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        envelope = {"keyId": key_id, "claims": value, "signature": b64(signing_key.sign(message))}
        envelope.update(envelope_changes or {})
        return json.dumps(envelope).encode()
    def verify(raw=None, **changes):
        return verify_allocation_grant(encode() if raw is None else raw,
            **{"expected": scope, "keys": {"fixture": key.public_key()}, "now": 1100, **changes})
    return scope, key, claims, encode, verify


def test_exact_bound_proof_and_validity_boundaries(grant):
    scope, _, claims, encode, verify = grant
    proof = verify()
    assert proof.scope == scope and proof.allocation == claims["allocation"]
    assert (proof.issued_at, proof.expires_at) == (1000, 1300)
    assert verify(now=1000) == proof and verify(now=1299) == proof
    with pytest.raises(FrozenInstanceError):
        proof.allocation = str(UUID(int=5))
    message = canonical_message("fixture", claims)
    assert message == b"spanopticon-media-allocation-v1\x00" + json.dumps(
        {"claims": claims, "keyId": "fixture"}, sort_keys=True, separators=(",", ":")).encode()
    # Verification is deliberately stateless; only an atomic source can consume.
    assert verify(encode()) == verify(encode())


@pytest.mark.parametrize("field,value", [
    ("issuer", str(UUID(int=8))), ("tenant", str(UUID(int=8))), ("source", str(UUID(int=8))),
    ("object", "media_foreign"), ("owner", "foreign"), ("policyDigest", "d" * 64),
    ("ownershipDigest", "d" * 64), ("tenant", None),
])
def test_valid_signature_cannot_change_expected_scope(grant, field, value):
    with pytest.raises(AllocationRejected):
        grant[4](grant[3]({field: value}))


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"extra": "unknown"}, {"allocation": "bad"},
    {"allocation": str(UUID(int=0))}, {"allocation": "00000000-0000-0000-0000-00000000000A"},
    {"issuedAt": True}, {"issuedAt": -1}, {"issuedAt": 1101}, {"issuedAt": 2**53},
    {"expiresAt": 1100}, {"expiresAt": 1000}, {"expiresAt": 1301}, {"expiresAt": 1300.0},
])
def test_invalid_claims_and_time_are_refused(grant, changes):
    with pytest.raises(AllocationRejected):
        grant[4](grant[3](changes))


@pytest.mark.parametrize("now", [-1, True, 1100.0, 2**53, 999, 1300])
def test_invalid_or_outside_clock_is_refused(grant, now):
    with pytest.raises(AllocationRejected):
        grant[4](now=now)


@pytest.mark.parametrize("raw", [b"", "not bytes", b" " * (MAX_GRANT_BYTES + 1), b"\xff", b"{", b"[]", b"null",
    b'{"keyId":"a","keyId":"b"}', b"[" * 1500 + b"]" * 1500])
def test_malformed_oversized_and_duplicate_json_is_refused(grant, raw):
    with pytest.raises(AllocationRejected):
        grant[4](raw)


def test_duplicate_nested_claim_unknown_fields_and_missing_fields(grant):
    _, _, _, encode, verify = grant
    raw = encode()
    with pytest.raises(AllocationRejected):
        verify(raw.replace(b'"version": 1', b'"version": 1, "version": 1'))
    for key in ("signature", "keyId", "claims"):
        envelope = json.loads(raw); del envelope[key]
        with pytest.raises(AllocationRejected): verify(json.dumps(envelope).encode())
    with pytest.raises(AllocationRejected): verify(encode(envelope_changes={"extra": True}))
    with pytest.raises(AllocationRejected): verify(encode(envelope_changes={"claims": []}))
    envelope = json.loads(raw); del envelope["claims"]["source"]
    with pytest.raises(AllocationRejected): verify(json.dumps(envelope).encode())


def test_wrong_key_signature_domain_and_key_selection_are_refused(grant):
    _, key, claims, encode, verify = grant
    wrong = Ed25519PrivateKey.from_private_bytes(b"\x08" * 32)
    with pytest.raises(AllocationRejected): verify(encode(signing_key=wrong))
    with pytest.raises(AllocationRejected): verify(keys={})
    with pytest.raises(AllocationRejected): verify(keys={str(i): key.public_key() for i in range(17)})
    with pytest.raises(AllocationRejected): verify(keys={"fixture": object()})
    for key_id in ("unknown", "bad/key", "", "a" * 65, None):
        with pytest.raises(AllocationRejected): verify(encode(key_id=key_id))
    envelope = json.loads(encode()); envelope["keyId"] = "alias"
    with pytest.raises(AllocationRejected): verify(json.dumps(envelope).encode(), keys={"alias": key.public_key()})
    for message in (b"other-domain\x00" + canonical_message("fixture", claims), canonical_message("fixture", claims)[len(b"spanopticon-media-allocation-v1\x00"):]):
        with pytest.raises(AllocationRejected): verify(encode(envelope_changes={"signature": b64(key.sign(message))}))
    raw = json.loads(encode()); raw["claims"]["allocation"] = str(UUID(int=99))
    with pytest.raises(AllocationRejected): verify(json.dumps(raw).encode())


@pytest.mark.parametrize("signature", [None, "", "x" * 85, "x" * 87, "+" * 86, "A" * 85 + "B"])
def test_signature_requires_canonical_base64url(grant, signature):
    with pytest.raises(AllocationRejected): grant[4](grant[3](envelope_changes={"signature": signature}))


@pytest.mark.parametrize("field,value", [
    ("issuer", None), ("tenant", "bad"), ("source", str(UUID(int=0))),
    ("policy_digest", "B" * 64), ("ownership_digest", None), ("owner", None), ("owner", "short"),
    ("owner", "A" * 43 + "." + "A" * 43), ("owner", "A" * 42 + "B." + "A" * 43),
    ("object", None), ("object", "media_bad"),
])
def test_expected_configuration_must_be_canonical(grant, field, value):
    scope, _, _, _, verify = grant
    with pytest.raises(AllocationRejected): verify(expected=replace(scope, **{field: value}))
