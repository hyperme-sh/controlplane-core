# Direct cache reads

`GET /api/v1/storage/records/cache/{name}/access` authorizes a direct read of
one cache record using the same signed tenant session and
`storage:records:read` scope as the existing byte endpoint. The active tenant
provider is selected by Spanopticon. The caller cannot supply an account,
container, tenant path, credential or expiry.

Azure returns a version-1 JSON descriptor with `provider: azure_blob`,
`method: GET`, `url`, `expires_at` (Unix seconds), `headers.If-Match`, `size`
and `encoding: spanrec1`. The URL includes a Blob-specific, HTTPS-only,
read-only SAS valid for at most 60 seconds from issuance. Account keys remain
inside Spanopticon. No list, write or delete permission is delegated. Recovery
and fixture namespaces are rejected. Unsupported providers return 501 and
continue to support the existing proxied byte endpoint.

The descriptor and URL are bearer credentials: keep them server-side, do not
log them or persist them in a public feed. Responses prohibit caching. A grant
can remain usable until its expiry after the calling service key is revoked.

The consumer must use the supplied exact `If-Match` header, reject redirects
and compressed responses, enforce the declared length and the existing
8 MiB plus 40-byte envelope limit, and validate `SPANREC1` before exposing the
payload after its 40-byte header. SHA-256 of the complete stored envelope is
the opaque record revision used by existing conditional writes. A provider
412 means the record changed; obtain fresh access before reading again. Never
interpret a failed direct read as an absent record. A missing record is
reported as 404 by the authenticated access endpoint.

The service only reads Blob metadata to create this descriptor. Cache bytes
travel directly from Azure to the consumer. Existing conditional writes,
deletes, proxied reads and non-Azure providers remain compatible.

This API does not migrate the historical s3r.ch recovery archive or publish its
contents. The archive contains both the public snapshot and Gun graph data;
only the public snapshot belongs in the feed cache.
