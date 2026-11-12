# Conditional tenant records

The storage-only runtime serves named cache, recovery and fixture records at
`/api/v1/storage/records/{namespace}/{name}`. Namespaces are `cache`, `recovery`
and `fixtures`; names contain 1–128 ASCII letters, digits, underscores or hyphens.
The tenant comes only from the gateway's bounded API-key JWT. These routes use
`storage:records:read`, `storage:records:write` and `storage:records:delete`,
separate from the media object's `storage:objects:*` scopes. Administrator
configuration still belongs to the existing tenant administration API.

| Operation | Request | Result |
| --- | --- | --- |
| Read | `GET` | Opaque body and quoted revision in `ETag`; 404 when missing |
| Create | `PUT`, `If-None-Match: *` | 201 and new `ETag`; 412 if already present |
| Replace | `PUT`, `If-Match: "<revision>"` | 204 and new `ETag`; 412 if changed or missing |
| Delete | `DELETE`, `If-Match: "<revision>"` | 204; 412 if changed or missing |

Mutations without exactly one supported precondition return 428. Uploads require
`application/octet-stream`, a declared length of 1 byte through 8 MiB, and no
content encoding. Four operations may run concurrently; each has a 60-second
deadline. Responses use `private, no-store`. A product may expose its validated
public cache through its own public route; this service never exposes a provider
URL or credential. Invalid input is 400, provider failures are 503, and deadlines
are 504. Error messages omit provider details.

Azure and S3 store records under a distinct `tenant-records/v1/<tenant>/` prefix.
Reads are bounded and consistent with the provider revision; writes and deletes
use atomic provider preconditions. A stored envelope contains a format marker,
32 fresh random bytes and the opaque body. The external revision is SHA-256 of
that envelope. Fresh random bytes ensure repeated identical payloads change the
S3 content-derived ETag, preventing stale writes after replacement or recreation.
Raw provider ETags stay inside the adapter.

This is a byte-storage contract, not an end-user grant or encryption scheme.
Public feed snapshots may contain public data. Sensitive recovery or fixture
payloads must be encrypted by their owner before upload. The future SociACL
secret store retains its own encryption, grant, revocation and recovery rules;
this protocol does not implement or replace those rules.

The feed consumer must treat 412 as a competing update, reload the latest
snapshot and fetch fresh source windows before attempting another publication.
It must not blindly retry a stale source window over a newer snapshot. Successful
empty source windows remove prior rows; failed sources preserve their last
success. Those product rules belong above this backend-neutral record boundary.

This runtime addition requires the edge route and key-owner scope policies to be
deployed before consumers can use it. Unit contracts use memory and SDK/HTTP
doubles; the existing CI image check also verifies anonymous record rejection.
