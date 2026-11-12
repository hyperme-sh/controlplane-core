# Tenant replay consumption

`POST /api/v1/storage/replays/{namespace}/{nonce}` consumes one SHA-256 hex
nonce for a tenant-bound issuer namespace. The caller sends no body,
`Content-Length: 0`, and `X-Replay-Expires-At` as Unix seconds. Expiry must be
in the future and at most 60 seconds away. The issuer must derive namespace
and nonce from its authenticated challenge, never arbitrary browser input.

The gateway-issued API-key session needs `storage:replays:consume`. Object
and record scopes cannot invoke this operation. Tenant selection uses the
signed session and existing private provider configuration. No GET, reset,
PUT or DELETE operation is exposed. Mutable cache, recovery, fixture and
media paths cannot address the `tenant-replays/v1/` prefix.

A successful consume returns 204; reuse returns 409. Invalid or expired
requests return 400; unavailable/corrupt/full/contended state returns 503;
a deadline returns 504. Every failure must deny challenge exchange. If a
write completed before a timeout or disconnect, a retry can return 409;
the caller must obtain a new challenge instead of treating reuse as success.

Each tenant/issuer namespace keeps at most 4096 unexpired entries in one
conditional record. Atomic provider revisions prevent two consumers from
accepting the same nonce. Updates discard only expired entries, never live
ones. Random revision bytes prevent content-derived ETag ABA. Expiry is
checked again after provider waits. State is bounded below 400 KB per
namespace; namespaces are allocated by trusted issuers, not public users.
There is no list or provider cleanup requirement on the request path.

Do not delete or restore this prefix while any issued challenge remains
valid. Operational resets must disable issuance and wait past the maximum
challenge window first. Provider backup/restore and administrative storage
credentials remain trusted operations. Corrupt state fails closed; it is
never silently replaced.

This additive API does not activate s3r.ch storage. Gateway routing/scope
support, a dedicated issuer credential and consumer configuration are
separate changes. Feed-cache credentials must never receive this scope.
