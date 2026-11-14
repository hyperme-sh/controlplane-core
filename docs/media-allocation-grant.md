# Media allocation proof contract

`api.domain.media_allocation_grant` prepares the bounded signed wire format for
[ACL-service #100](https://github.com/hyperme-sh/acl-service/issues/100). It exposes
verification only. No HTTP endpoint, issuer, signing key, database, policy grant,
credential or deployment is created or enabled.

The verifier takes an independently configured Ed25519 public-key map, a trusted
expected scope and the current integer second. Request JSON cannot select the
expected scope or supply trust keys. At most 16 configured keys are allowed;
key discovery and token-supplied keys are not supported.

The JSON envelope contains exactly `keyId`, `claims` and `signature`. Claims are
exactly version 1 plus `issuer`, `tenant`, `object`, `owner`, `source`,
`policyDigest`, `ownershipDigest`, `allocation`, `issuedAt` and `expiresAt`.
Issuer, tenant, protected source and allocation IDs are nonzero canonical UUIDs.
The owner is a canonical valid SEA P-256 public key; the `media_p_...` object must
embed that same principal. Both digests are lowercase SHA-256 hex. The ownership
digest refers to the independently verified canonical signed ownership record;
the policy digest refers to the configured trusted policy, never browser policy.

The signature is canonical unpadded base64url over:

```
UTF8("spanopticon-media-allocation-v1") || 0x00 ||
ASCII(JSON({"claims": claims, "keyId": keyId}, sorted keys, compact separators,
           ASCII escaping, no NaN))
```

The algorithm is fixed Ed25519, with no algorithm field or downgrade. Key ID is
covered by the signature. Envelopes exceed neither 4,096 bytes nor a five-minute
validity window. Future, expired, non-integer, Boolean or out-of-range times
refuse. Duplicate JSON properties, unknown fields, untrusted keys, noncanonical
encodings and every expected-scope mismatch refuse.

A verified signature is not sufficient to provision an object. The future issuer
must first authenticate the allocation requester and establish an exclusive,
durable tenant/object/owner/source allocation under its approved policy. It must
preserve that ownership across retries, expiry and restart; a signing key or
successful ciphertext upload does not establish exclusivity.

The protected ACL consumer must independently verify the current SEA key and
signed ownership record, compare trusted policy and source bindings, and
atomically consume the allocation while initializing empty media state and its
independent checkpoint. Replay, replacement of an existing object, rollback,
cancellation and quota checks remain mandatory. An uncertain response requires
reconciliation, not a second allocation or implicit enrollment. This stateless
verifier deliberately does not claim to implement those transactions.

Issuer ledger, requester authentication, atomic consumer initialization and
runtime configuration remain separate work before #100 can close. The current
Storage owner is controlplane-core; this preparation does not select a deployed
issuer identity or change the pending production database binding.

Unit contracts use generated/fixture keys only. CI measures line and branch
coverage and runs the standard source, wheel and rebuilt-package checks. No
operator key or provider credential is needed for these checks.

## Private allocation registry

`media_allocation_store.AllocationStore` adds the provider-conditional registry
primitive. Its tenant-wide private prefix is outside public named-record and
ciphertext namespaces. Source IDs do not partition it: one tenant/object cannot
be allocated independently to multiple sources. It accepts at most 64 objects,
matching the current protected source bound.

Bootstrap is an explicit operator-only method, conditional on absence. Runtime
reservation fails if the registry is missing. Never bootstrap automatically after
loss; restore through controlled recovery. Concurrent writes use provider ETags
and propagate conflicts. Each write carries a fresh nonce to prevent content-ETag
ABA. All persisted entries are structurally and scope validated before use.

A retry with the same complete scope returns the original allocation and validity
window, including after expiry. It cannot renew or change the source, policy or
ownership digest. Reusing an allocation ID for another object refuses. Entries
remain charged against quota; there is no deletion or cancellation reclamation.
An uncertain write can be reconciled by repeating the same authorized request.

The caller must verify current requester/SEA ownership and configured policy,
and supply a mandatory authority recheck immediately before dispatch or returning
a prior reservation. This primitive neither authenticates callers nor signs
proofs. A provider snapshot rollback still needs independent source checkpoints
and controlled recovery; provider CAS alone does not prevent it. No route,
operator bootstrap, credential, signer, or runtime binding is enabled here.
