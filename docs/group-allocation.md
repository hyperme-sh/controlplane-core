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

This module only verifies signatures and scope. Durable quota reservation,
atomic one-use consumption, ciphertext writes, current-cohort delivery and
provider configuration remain required before activation. The issuer chooses
the durable allocation ID independently of the request nonce. Missing storage
or authorization must refuse service, without fallback to a media identifier.
No route, credential, schema installation or runtime activation is added here.
