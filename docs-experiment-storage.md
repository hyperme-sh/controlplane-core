# Scoped experiment records and expiry

The additive `experiments` record namespace separates application flag readers
and outbox writers from generic feed, recovery and media credentials. Existing
record paths retain their behavior for independently updated consumers.

For audience slugs `s3rch`, `hypermesh` or `portal`:

- `GET /api/v1/storage/records/experiments/{audience}-cache` requires
  `storage:experiments:{audience}:cache:read`. It reads the publisher's existing
  `cache/experiments-v1-{audience}` bytes. The alias never grants cache writes.
- `GET` of `{audience}-outbox-{64 lowercase hex characters}` requires
  `storage:experiments:{audience}:outbox:read`.
- Conditional `PUT` of that outbox requires
  `storage:experiments:{audience}:outbox:write`. The body is bounded to 64 KiB.
  No public DELETE is available: withdrawal writes a revocation marker, preventing
  an old request from recreating events before its consent token expires.

The owning application validates event meaning using the event contracts.
Storage validates the finite envelope: `{version:1, expires, revoked, rows}`,
with a millisecond expiry at most 24 hours ahead and at most 64 rows. Existing
expiry cannot be extended and revocation cannot be reversed. Cache definitions,
private publisher state, media grants and other tenants are outside this scope.

Run the private `python -m api.experiment_retention` process with the usual Core
provider/database configuration, `EXPERIMENT_TENANT_ID` and
`EXPERIMENT_RETENTION_AUDIENCE`. It requires no flag signing key or GrowthBook
credential. It resolves only that tenant's configured private Azure or S3
provider, lists only its exact audience outbox prefix, and deletes expired
records with the provider's conditional revision. A concurrent change is skipped
for a later scan. A malformed record fails visibly rather than being deleted.

The worker saves a private paging cursor so restarts do not repeatedly scan only
the first live records. It continues pages immediately and waits 60 seconds
between complete scans. Each page and provider operation is bounded. Provider
outages print a redacted failure and retry. This worker must accompany an enabled
consumer; expiry checks in a web process alone do not remove inactive records.
The provider's existing backup and deletion policies still apply.

The worker and its scoped route are prerequisites for Hypermesh #66, Portal #98,
s3r.ch #337 and Infra #923. Trusted guardrail telemetry remains Core #49.
