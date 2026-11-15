# Group allocation contract

Group ciphertext capacity uses `spanopticon-group-allocation-v1` and the
separate `spanopticon-group-allocation-request-v1` source-attestation domain.
The exact scope binds issuer, tenant, canonical owner-controlled `set_p_` ID,
owner, protected source, policy digest, original creation digest and predicate
(`same-group` or `named-circle`). Media proofs cannot allocate a group.

The creation digest is permanent. It must not be replaced by a current cohort
or epoch digest: membership and key rotation do not create another reservation.
The protected source must check current ownership before issuing a request.
Requests expire within 30 seconds; issuer grants expire within 300 seconds.
Configured source and issuer keys are separate. Neither proof grants reads.

The source signer accepts only protected, current owner authorization. The
issuer verifies that request before and after a conditional private reservation.
The registry permits at most 64 permanent groups per tenant, retains expired
reservations, refuses scope replacement and reconciles retries to the original
allocation. Source and issuer keys must differ. Missing registry state refuses
service; bootstrap is an explicit operator operation.

Atomic source consumption, ciphertext byte quotas, writes, current-cohort
delivery and provider configuration remain required before activation. The issuer chooses
the durable allocation ID independently of the request nonce. Missing storage
or authorization must refuse service, without fallback to a media identifier.
No route, credential, schema installation or runtime activation is added here.

## Issuance transport

`POST /api/v1/storage/group-allocations/issue` requires authenticated tenant
scope `storage:group-allocations:issue` and a valid protected-source request.
The body contains only `set`, `predicate`, `owner`, `creationDigest` and the
canonical base64 `request`. No caller supplies tenant, issuer, policy or keys.

`STORAGE_GROUP_ALLOCATION_FILE` is an explicit private version-1 issuer file
with the same bounded binding fields as the media version-1 issuer format.
Absence leaves group issuance unavailable. The private group registry must
already exist; loading settings and serving requests never initialize it.
Media allocation scope grants no group issuance permission. The browser never
receives provider credentials. No deployment configuration is activated here.
