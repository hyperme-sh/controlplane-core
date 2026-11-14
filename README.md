# controlplane-core

`controlplane-core` 0.1.0 is the shared Python library behind the Spanopticon
control-plane HTTP application. Its import name is `api`. It assembles the
FastAPI process (tenants, accounts, billing, operator console, and domain
events) and keeps the historical `api.*` modules pointed at the current
[controlplane-api](https://github.com/hyperme-sh/controlplane-api),
[controlplane-identity](https://github.com/hyperme-sh/controlplane-identity),
and [controlplane-tenancy](https://github.com/hyperme-sh/controlplane-tenancy)
packages.

## Why

Spanopticon services and Hypermesh callers share one settings object, one
request-scoped database session, and one tenant context. Auth, API keys, ACL,
and the edge each run their own process, and each of those processes has to
resolve the same Keycloak token, the same Postgres row-level security setting,
and the same domain-event envelope. This library is that shared surface. The
`api` import name is what those services already call, so a settings or session
change stays in one module object instead of drifting across copies.

## How it works

The wheel installs the `api` package. A compatibility module replaces itself
with the installed implementation, so `import api.core.config` and
`import controlplane_api.config` are the same module in one process. The same
pattern covers identity and tenancy:

| Legacy import | Implementation |
| --- | --- |
| `api.core.config`, logging, exceptions, health, version, metrics, permissions | `controlplane-api` 0.23.0 |
| `api.infrastructure.security.keycloak_jwt`, tenant-session JWT, API-key exchange JWT, internal tokens, portal controls | `controlplane-identity` 0.15.0 |
| `api.infrastructure.persistence.db_session`, tenant GUC, tenant membership | `controlplane-tenancy` 0.14.0 |

Code that still lives in this repository is the application around those
libraries:

- `api.app` is the FastAPI application. On startup it opens an async SQLAlchemy
  engine through `controlplane-db`, installs the request session, seeds platform
  roles, and starts the tenant event publisher.
- HTTP routes are mounted at `/api/v1`: health, tenants, features, branding,
  app configuration, account, security, dashboard, roles, permissions, storage,
  AI, products, prices, billing, Stripe, invoices, notifications, audit logs,
  and SSO. `GET /version` is registered on the application itself.
  Interactive docs are at `/docs`. Authenticated routes expect a Keycloak
  access token for the `controlplane` realm.
- Each request gets a database session and, when tenancy is enabled, the
  Postgres `app.current_tenant_id` setting. Health probes
  (`/api/v1/health` and `/api/v1/health/`) skip tenant resolution so a container
  IP in the `Host` header still returns readiness.
- The operator console domain (`api.domain.operator`) holds bans, claim checks,
  operator mail, peer-plane rows, and the operator population map. Console
  access requires an explicit `operator:console` permission. Host and renter
  product groups are recorded separately and do not open the console.
- Tenant domain events use the business session and the consuming service's
  Postgres `service_outbox`. `publish_in_session` never commits; async callers
  use `session.run_sync`. `publish` requires `bind_outbox_session`, and a stable
  business operation UUID is required as `correlation_id`. Retrying identical
  facts keeps one event ID; changing facts for that operation is refused.
  The consuming service installs the outbox through its own migration chain;
  Core does not create schema at startup. The shared log and cloud message bus
  are not publishing options.
- OIDC login projects an authenticated identity; it does not create tenant
  membership and does not emit `user.created.v1`. Auth delegates tenant enrollment
  and switching to the User owner. Only actual tenant-user creation emits that
  event. Identity lookup, existing network authority and tenant profile rules
  remain separate. See [the traced boundary](docs/tenant-event-boundary.md).
- Schema migrations stay in
  [controlplane-db](https://github.com/hyperme-sh/controlplane-db) 0.1.0.
  The operator ban contract is
  `operator-ban-store` 0.1.0 from
  [user-service](https://github.com/hyperme-sh/user-service)
  (`core/user-service/contracts`).
- When [controlplane-edge](https://github.com/hyperme-sh/controlplane-edge) is
  installed in the same environment, startup also runs its gRPC server and
  registers gateway middleware, aggregated OpenAPI, and GraphQL.
  `python -m controlplane_edge.main` starts the Prometheus metrics server and
  then serves this `api.app`. User, group, and organization HTTP is forwarded
  by that edge to user-service; those prefixes are left unmounted here.

`GET /api/v1/health/` returns `{"status":"Ok"}`. The edge process sets
`CPEDGE_HYPERMESH_SERVICE_URL`. With that variable set, the same route probes
`{url}/api/v1/hypermesh/catalog` and answers `503` with
`{"status":"hypermesh unreachable"}` when hypermesh-service does not answer.

## Practical use

### Tenant storage provider deployment

`api.storage.configure_provider` is an explicit deployment command, separate
from application startup and the tenant administrator API. It consumes a private
JSON file containing version 1, the declared tenant ID and network, provider
`azure`, account, container, and connection string. The invoking deployment must
provide the expected tenant and network independently and the restricted storage
runtime `DATABASE_URL`. The file must belong to the process user with mode 0400
or 0600. Provider credentials never appear in command arguments or output.

The command requires the existing `storage_owner` schema, forced tenant row
isolation, a non-owner runtime role, and an existing tenant. It does not create
tenants or change grants. It permits only HTTPS Azure public-cloud Blob endpoints
whose account matches the declaration, and verifies that the container is private
before writing settings. All writes use one tenant-locked database transaction.

Initial configuration refuses existing administrator-managed settings. Repeated
identical deployment is unchanged; credential rotation requires the SHA-256
returned by the previous successful configuration. Rotation cannot move the
account or container. Administrator changes, disabled providers, missing prior
configuration, and competing providers require explicit administrator resolution.
The result contains only `changed` and the configuration fingerprint. This command
does not grant product API keys or expose provider credentials to product apps.

Unit checks use provider and database doubles. A separate disposable PostgreSQL
contract runs only in CI, checking runtime privileges and tenant isolation.

Python 3.13 and [uv](https://docs.astral.sh/uv/) 0.11.26. Dependency revisions
are pinned in `pyproject.toml` and `uv.lock`. The pinned repositories are
public.

```sh
uv sync --frozen
```

Apply the controlplane-db migrations to the database before the process starts.
Startup seeds platform roles and expects those tables to exist. Settings load
from the environment and from `.env` (`api.core.config`, a
`pydantic-settings` model). Replace the development token secrets before any
shared deployment.

```sh
export DATABASE_URL='postgresql+asyncpg://controlplane:controlplane@127.0.0.1:5432/controlplane'
export REDIS_URL='redis://127.0.0.1:6379/0'
export SERVICE_NAME='controlplane'
export FASTAPI_ENV='development'
export KEYCLOAK_ISSUER='http://127.0.0.1:8081/realms/controlplane'
export KEYCLOAK_JWKS_URL='http://127.0.0.1:8081/realms/controlplane/protocol/openid-connect/certs'
uv run uvicorn api:app --host 127.0.0.1 --port 8000
```

`REDIS_URL` overrides the cache middleware (the built-in default is
`redis://localhost:6372`). `GET /api/v1/health/` and `GET /version` do not
require a bearer token. Account, tenant, and billing routes do.

```sh
curl -sS http://127.0.0.1:8000/api/v1/health/
curl -sS http://127.0.0.1:8000/version
```

`GET /version` reports `service` from `SERVICE_NAME`, plus `git_sha` (`GIT_SHA`,
then `SOURCE_SHA`, then `HYPERMESH_SOURCE_SHA`) and `built_at`.

With the edge package installed in the same environment:

```sh
export PORT=8000
uv run python -m controlplane_edge.main
```

That launcher binds Prometheus on `METRICS_PORT` (default `9100`) unless
`METRICS_ENABLED=0`. `uvicorn api:app` installs the in-process HTTP metrics
middleware and does not bind that port by itself.

## How Hypermesh uses it

Hypermesh lease, ticket, and tunnel HTTP is served by hypermesh-service.
This library is the shared control-plane code on that path: settings, identity
aliases, the operator ban book, and process metrics. Callers below are the
current public services that depend on `controlplane-core`.

**Renter keys and chat.** [api-keys-service](https://github.com/hyperme-sh/api-keys-service)
reads `HYPERMESH_INTERNAL_TOKEN` from the shared settings object
(`api.core.config.settings`). On mint it `POST`s the key's SHA-256 fingerprint
to `{HYPERMESH_CONTROL_PLANE_URL}/api/v1/hypermesh/internal/renter-key-fingerprints`
with `X-Internal-Token` and `X-Tenant-ID`. Revoke uses
`.../renter-key-fingerprints/revoke` (and the IR revoke path for an IR key).
Lookup of the tenant's active lease is
`GET .../api/v1/hypermesh/internal/mapped-lease`.
[hypermesh-gateway](https://github.com/hyperme-sh/hypermesh-gateway) pulls the
cross-tenant fingerprint index with the same token:

`GET {HYPERMESH_CONTROL_PLANE_URL}/api/v1/hypermesh/internal/renter-key-index`

The gateway default base URL is `http://hypermesh-service:8000`. Renter chat
on the gateway verifies a portal JWT or an API-key-exchange JWT locally
through `api.infrastructure.security.api_key_exchange_jwt`. After a ticket is
issued, that chat path uses the cached signed ticket and the standing
WireGuard peer.

**Gateway control-plane hop.** The gateway's other plane calls use a service
bearer (`HYPERMESH_GATEWAY_SERVICE_SECRET`, prefix `hm_gw_`) and
`X-Tenant-ID`, still against hypermesh-service:

- `GET /api/v1/hypermesh/gateway/tunnels`
- `GET /api/v1/hypermesh/gateway/tickets`
- `POST /api/v1/hypermesh/gateway/metrics` (byte counts and handshakes)
- `POST /api/v1/hypermesh/gateway/safety-flags`

**Host setup.** [auth-service](https://github.com/hyperme-sh/auth-service)
validates the host-setup Keycloak token through
`api.infrastructure.security.keycloak_jwt`, refuses a banned member with
`api.domain.operator.bans.ban_book().user_may_enter`, and mints a host-setup
tenant-session JWT (`mint_host_setup_session`, client id
`hypermesh-host-setup`) on `POST /host-setup/session`. The same ban check
runs on connect-session and enrollment. An active device ban makes
`device_may_take_lease`, `device_may_enroll`, and `device_may_schedule`
return false. Image and firmware actions stay on the host; this book records
posture verbs only.

**Catalog readiness and metrics.** When the edge sets
`CPEDGE_HYPERMESH_SERVICE_URL`, public readiness includes the hypermesh
catalog probe described above. Lease, host-selection, pricing-round,
heartbeat, and read-cache series are recorded through
`api.infrastructure.metrics` (`hypermesh_leases_total`,
`hypermesh_host_selection_total`, `hypermesh_pricing_rounds_total`,
`hypermesh_hosts_fresh`, `hypermesh_host_heartbeat_age_seconds`, and the
`hypermesh_read_cache_*` series). In the Spanopticon Compose layout,
hypermesh-service metrics are published on host port `9110` and
hypermesh-gateway on `9111`, both mapped to container port `9100`.

**Interactive clients.** The default Keycloak `azp` allow-list includes
`hypermesh-native` (visor and CLI) together with `controlplane-frontend`,
`controlplane-mobile`, `controlplane-device`, `controlplane-m2m`, and
`controlplane-api-key`. Interactive human tokens need an age-confirmation
timestamp. `controlplane-m2m` and tokens with `account_type=service` are
exempt. Remove `hypermesh-native` from `KEYCLOAK_ALLOWED_CLIENT_IDS` to
refuse that client.

**Other Spanopticon services on this library.** auth-service and
api-keys-service configure the shared async session
(`api.infrastructure.persistence.db_session`) and set the tenant GUC on their
own requests. auth-service publishes login and token events with
`install_tenant_event_publisher`. api-keys-service checks internal tokens and
portal-control allowlists through `api.common`. acl-service consumes the
shared event type constants (`USER_CREATED_V1`, `ACL_POLICY_UPDATED_V1`) and
the same publisher. The edge serves `api.app` as the aggregated control-plane
process and mounts this package's health and version routes.

## Develop and test

```sh
uv sync --frozen
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/postgres' \
  bash scripts/check-package.sh
```

Point `TEST_DATABASE_URL` at a disposable PostgreSQL server that can create
databases. The OIDC regression creates and drops a database of its own.
`scripts/check-package.sh` runs the suite from the source tree, from a fresh
wheel installed outside the checkout, and from a wheel rebuilt from the
source archive. The two wheel payloads must match. It also runs `tests_tools`
and exports the packaged `api` runtime contract.

```sh
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/postgres' \
  bash scripts/check-package.sh --fixtures
```

`--fixtures` installs the verified wheels in
`tests_artifacts/dependencies/manifest.json` (built from the pinned git
revisions) and does not fetch those dependencies from git. That is the
pull-request lane.

These checks cover this package. They do not decide live Hypermesh provider
behavior or a full plane deployment. `integration/test_metrics_deployment.py`
reads a Spanopticon deployment checkout from `SPANOPTICON_CHECKOUT` and is
outside the package suite.

Release channels and version tags are described in
[docs/releasing.md](docs/releasing.md). Compatibility rules for the shared
line are in [docs/release-policy.md](docs/release-policy.md).

## Release and versioning

The distribution version is **0.1.0** (`pyproject.toml`). Development uses the
exact git pins in that file and in `uv.lock`; a previous GitHub release is not
required to build or test. Pushes to `main` retain the tested wheel and sdist
and then discards temporary distribution files. Automatic package
prereleases are not published. A stable package, when used, is an explicit
`vMAJOR.MINOR.PATCH` tag, checked against an accepted baseline. Details
are in [docs/releasing.md](docs/releasing.md).

## License

This package is licensed under [Apache-2.0](LICENSE). Dependencies keep their
own licenses.

## Tenant storage configuration

Storage configuration reads, provider selection and resets require an explicit
authenticated tenant. The repository filters that tenant even when the database
role can see other tenants. Provider changes lock the tenant row for the request
transaction; disabling or configuring one tenant cannot select a global default
or modify another tenant's provider. Reset writes cleared credentials as NULL.
Configuration payloads and credentials are not logged.

This is the configuration boundary for #36 / FyberLabs/s3r.ch#300. The private
media object API, upload integrity, client-key encryption, quotas and s3r.ch
consumer remain pending. The historical FileUpload helper is not that API.

## Private tenant object API

`/api/v1/storage/objects/{object_id}` stores opaque client-encrypted bytes for a
service tenant. It accepts only the gateway's signed API-key session, with a
canonical tenant ID, a bounded five-minute lifetime and the exact operation
scope. Browser Keycloak tokens and s3r.ch agent JWTs are not storage credentials.
Tenant identity never comes from a query parameter, filename or caller header.

| Method | Required scope | Behavior |
| --- | --- | --- |
| PUT | `storage:objects:write` | Verify length and SHA-256, then conditionally create the whole object; 201 for new bytes, 204 for an identical retry. |
| HEAD | `storage:objects:read` | Return length and checksum ETag, or 404. |
| GET | `storage:objects:read` | Return bytes, with optional single byte range; invalid ranges return 416. A mismatched If-Range falls back to the complete object. |
| DELETE | `storage:objects:delete` | Idempotently remove the current object; return 204. |

The object ID is the lowercase SHA-256 of the ciphertext, not the plaintext or a
filename. It cannot be used to replace an existing ID with different bytes.
Uploads require `Content-Type: application/octet-stream` and an exact positive
`Content-Length`, at most 64 MiB. The API bounds upload processing to 120 seconds
and eight concurrent uploads per worker. It verifies the entire body in a
private temporary spool before sending it to storage; incomplete, oversized or
mismatched client bodies never reach the provider. Cancellation during a provider
write has an unknown commit outcome; retrying the same ID is safe. Temporary spools close on all exits.
There is no incomplete multipart object or upload session to clean up.

The backend is the tenant's explicitly selected AWS S3 or Azure Blob provider.
S3 requires all four bucket Public Access Block settings to be enabled and
readable by that provider credential. Azure requires an approved `*.blob.core.windows.net` HTTPS endpoint, disabled
redirects, and a
readable container policy with anonymous access disabled. Its existing tenant
connection string stays in Spanopticon; it is never a product-owned archive.
Azure uses a conditional single Block Blob upload with transport checksum
validation, bounded asynchronous reads and cancellation. No containers are
created by request handling. Missing configuration, unsupported providers and
unreadable privacy settings fail closed; global storage defaults are never used. Credentials and
provider paths stay inside Spanopticon. Responses are binary attachments with
`private, no-store` and `nosniff`; no public or signed download URL is issued.
The adapter uses [conditional checksum-bound PutObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)
and [single-range GetObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html).
The Azure adapter uses the [asynchronous Blob client](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.aio.blobclient?view=azure-python).

A tenant token permits that service's storage operation, not an end user's
SociACL see/share grant. s3r.ch must check current object access and encrypt with
client-held keys before enabling its consumer. The storage service has no
client decryption keys and cannot establish whether an arbitrary upload was
properly encrypted. Delete does not recall downloaded copies or override bucket
version/backup retention. Re-uploading a deleted ID can restore only identical
bytes. Provider retention and aggregate tenant budgets remain deployment-owned;
this API supplies per-object/request bounds, not aggregate billing quotas.

No s3r.ch caller credential is installed by this change. Declared tenant wiring,
client encryption/retrieval, aggregate quota policy and central nightly coverage
remain tracked by #36 and FyberLabs/s3r.ch#300 before media is enabled.

Tenant network administrators can use `GET /api/v1/storage/tenant`,
`POST /api/v1/storage/tenant/configure` and
`PATCH /api/v1/storage/tenant/{storage_id}/reset` with their signed network
session and matching `X-Tenant-ID`. Each call forwards the session to the
configured HTTPS `USER_SERVICE_URL` owner check; `USER_SERVICE_CA_FILE` may name
its trusted CA. No local role repair or substitute administrator is required.
The owner validates the current selection generation and network mapping and
checks current tenant-administrator authority, including inherited network-admin
authority and tenant-scoped actor bans. Unavailable, revoked or mismatched authority
fails closed. These requests bypass the HTTP cache.

The configuration body has the existing storage-settings shape, but its audit
actor and tenant always come from the verified session. This network-owner path
accepts public AWS regions/buckets and standard HTTPS public Azure account-key
connection strings only; custom Azure endpoints and development storage are
rejected. Azure connection strings must explicitly contain
`DefaultEndpointsProtocol=https`, `AccountName`, `AccountKey` and
`EndpointSuffix=core.windows.net`. Provider credentials remain masked in lists.
The older permission-based storage routes keep their existing contract.

## Tenant storage process

The runtime image also carries `/opt/storage-schema`, selected from the exact
qualified controlplane-db source archive recorded in
`tests_artifacts/storage-schema/manifest.json`. Context preparation verifies the
archive digest and copies only the three storage-owner bundle files. It refuses
links, duplicate members, missing files and oversized content. Historical
migrations and the rest of the source archive never enter the runtime image.

Deployment runs `/opt/storage/bin/python -I /opt/storage-schema/migrate.py`
with `--database-url-file`, `--schema` and `--runtime-role` using the distinct
restricted schema owner. The service itself uses the runtime role. Startup
does not apply migrations, create identities or provision provider credentials.
`/opt/storage-schema/source.json` records the selected source and digest; it
does not pin a running service channel.

`api.storage_app:app` is the storage-only ASGI entrypoint for Spanopticon. It
exposes `/health`, `/api/v1/storage/objects/{object_id}` and the tenant storage
administration routes. It does not start the historical monolith, seed roles,
start event workers, or expose its account/operator routes. Importing the package
still constructs the historical application for compatibility; only the selected
storage app's startup function runs.

The process uses `DATABASE_URL` with the storage owner's restricted database
role. Schema and role provisioning remain in controlplane-db/infra. Every request
is authenticated before opening its transaction. Object access selects the RLS
tenant from the bounded API-key JWT; administration selects it from the verified
current owner. A conflicting or duplicate tenant header is refused. Host and
query parameters cannot select database scope. Failed or cancelled requests roll
back and clear the task-local session. `/health` checks database connectivity
without returning connection details.

The existing API-key exchange secret and `USER_SERVICE_URL`/optional
`USER_SERVICE_CA_FILE` configure the authentication contracts described above.
The runtime needs the same private provider settings and outbound HTTPS access.
Deployment packaging, edge routing, tenant provisioning and quotas remain
separate work; adding this entrypoint does not activate a storage service.

The normal main package workflow prepares the storage image from its exact tested
wheel plus verified dependency wheels and a hashed public dependency lock. No Git
credential enters the build context. CI checks the non-root, read-only image
against its disposable job database and refuses anonymous storage access before
advancing `ghcr.io/hyperme-sh/controlplane-core/storage:test`. A superseded source
revision cannot advance the channel. The image records source and wheel digests
for diagnostics; deployments consume the moving TEST channel. This publication
does not provision the tenant database role, provider, edge setting or service.

Audit log readers and writers use the same private directory from the pinned
API package. Reads validate tenant names and use no-follow file descriptors;
public `/tmp` globs are no longer used. This changes only temporary audit
storage, without migrating historical files or changing package versions.
Normal PR and main package checks use offline contracts; the PostgreSQL subject
projection cases remain in the central nightly core suite. The existing scoped
runtime-image smoke remains in its deployment workflow.

## Experiment control policy

`api.domain.experiments.control` defines bounded tenant experiment configuration,
per-metric sample/window thresholds, and conditional disable decisions. Its
signed payload builder produces the existing strict v1 OpenFeature consumer
shape; tenant identifiers and private guardrail policy stay outside the public
payload. The deployment-owned signer must use Ed25519.

`disable_regressed` reads through an injected authoritative store and queries a
trusted server/provider metrics source. That source must authenticate upstream
telemetry and deduplicate stable action IDs before returning an aggregate. Do
not expose aggregate submission to browser callers. Scope/window mismatches
refuse the update; missing or undersampled data does not disable an experiment.
A conditional-write conflict or an evaluation exceeding 30 seconds requires fresh
configuration and metric evaluation.
The controller can only disable; re-enabling requires an operator change.

`ExperimentStore` persists through the tenant's existing conditional provider
backend under a separate `tenant-experiments/v1` prefix. General cache/recovery/
fixture credentials cannot address that prefix. Initial creation requires an
absent record; updates require the preceding sequence and provider ETag. It
preserves the provider's atomic conflict handling and does not retry writes.

The private `GET`/`PUT /api/v1/storage/tenant/experiments/{audience}` API
requires a verified tenant session and a current `tenant.admin` check against the
user authority on every request. Configuration uses that tenant's existing private
provider; cache credentials cannot read or mutate it. Responses are private and
non-cacheable. PUT accepts at most 32 KiB of JSON: creation requires
`If-None-Match: *` and sequence 1; updates require `If-Match: "N"` and sequence
N+1. A competing update returns 412 and requires reading current configuration
before retrying. The path audience and body tenant must match the authenticated
scope. Provider failures return static errors without private diagnostics.

The API saves policy only; it does not publish signed flags or start a worker.
Authenticated telemetry collection, signing-key custody, portal configuration and
delivery remain tracked in #49. This changes no release routing and enables no
experiment by itself.

The dedicated `python -m api.experiment_publisher` process reads
`EXPERIMENT_TENANT_ID`, `EXPERIMENT_AUDIENCE` and a private PKCS8 Ed25519 PEM file at
`EXPERIMENT_SIGNING_KEY_FILE`. Provision a different signing key per tenant and
application; consumers pin that binding's public key. No HTTP caller supplies a
key, policy or signing callback. The process uses the existing database tenant
scope and private provider selection, checks every 30 seconds with a 30-second
attempt deadline, and emits static failures without provider diagnostics.

A private `tenant-experiment-publications/v1` record allocates monotonic publication
sequences independently of operator policy sequences. Signed flags expire after
five minutes and renew with two minutes remaining. The publisher writes only the
public flag payload and detached signature to the tenant cache as
`experiments-v1-s3rch`, `experiments-v1-hypermesh` or `experiments-v1-portal`.
Existing record read credentials can retrieve that envelope; they cannot access
private policy, publication state or signing keys. Conditional writes prevent
older workers replacing newer signed cache data. Consumers must verify signatures,
audience, sequence and expiry before using it. Corrupt cache bytes are replaceable;
the cache never selects the authoritative publication sequence.

Policy edits and cache delivery are separate conditional operations. A concurrent
policy edit is picked up on the next successful publisher pass. Unavailable
storage leaves the last signed payload to expire, after which consumers use their
compiled control. Deployment key staging, consumer polling and the trusted metrics
worker are still required before activating experiments. Do not reset publication
state during restarts or key rotation: its monotonic counter must be preserved.

### Private media records

`/api/v1/storage/records/media/{name}` stores opaque private media grant metadata
with the existing bounded record and conditional-update contract. It requires
`storage:media:read`, `storage:media:write`, or `storage:media:delete` for the
corresponding method. General record credentials, including the public-feed
cache key, cannot access this namespace. Media credentials confer neither
object-byte access nor access to cache/recovery/fixture records. The cache-only
`/access` delegation endpoint refuses media records. These service scopes do not
replace application-level current reader grants or encryption.

### PostgreSQL media allocation adapter

`PostgresAllocationStore` consumes the additive storage-media schema owned by
controlplane-db. It preserves the original allocation on a same-scope retry,
serializes tenant quota decisions with the tenant row lock, and refuses any
object whose retirement has begun. Completed retirement stops charging allocation
quota while the object binding and allocation identifier remain permanent.

The adapter is not selected by runtime configuration yet. It creates no schema,
tenant, source binding, or credentials, and does not import the existing Blob
registry. Deployment must prepare/import the schema and coordinate the dedicated
media write/cleanup API before changing the issuer store. Provider deletion and
byte quota reclamation are separate consumers of the retirement state.

`RetirementRequestSigner` and `PostgresMediaRetirement` define a separate,
source-signed retirement command. The source must first verify the owner's
accepted deletion and its independent checkpoint. Storage fixes the trusted
issuer/tenant/source and key set, verifies the short-lived command, matches the
original allocation, and atomically moves it into retiring state. Retries retain
the same permanent command. Retiring objects still consume quota; this adapter
neither deletes provider bytes nor marks cleanup complete. No HTTP route or
runtime binding is enabled by these components.

### Media provider retirement

The dedicated `tenant-media/v1` Azure/S3 adapters replace the current payload
with a permanent empty marker when the storage coordinator determines that no
other live object references it. Create-only late uploads cannot overwrite that
marker. The adapter verifies the marker before reporting completion and refuses
hard deletion of this namespace. Generic object APIs remain separate.

These adapters are not activated or exposed by a route. Callers must authenticate
the source command and serialize references before using them. Removing current
payloads does not purge provider-retained versions, soft-deleted snapshots or
backups; those follow the declared retention policy. Never expire the current
retirement marker through a storage cleanup rule.

CI checks the Azure SDK against disposable Azurite, including a delayed block
commit, and checks S3 request/signing behavior with unit fixtures. The S3 fixture
does not qualify a live provider. The Azure behavior follows
[Put Blob](https://learn.microsoft.com/en-us/rest/api/storageservices/put-blob)
and [soft delete](https://learn.microsoft.com/en-us/azure/storage/blobs/soft-delete-blob-overview);
S3 create-only writes use [conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

`PostgresMediaObjects` coordinates the dedicated provider adapters with durable
references. A reference commits before uploading bytes; an unknown write result
keeps its cleanup target and quota. Writes then take the tenant lock again and
refuse retirement. Cleanup accepts only the command already stored in phase 1,
loads digests from protected metadata, preserves references shared with other
objects, confirms provider retirement, and commits reference removal plus phase 2
atomically. A repeated completed cleanup changes nothing. The object/allocation
binding remains permanent.

Ciphertext retains the product limits of eight objects and 64 MiB per owner,
256 MiB per tenant; wrap references are separately bounded to 128 per object
and 64 KiB each. Quota remains charged for failed uploads and pending cleanup.
This coordinator is not selected by runtime configuration and exposes no route.
It does not import existing Blob indexes or activate credentials.

Private allocation configuration version 2 adds `registryFile` to every issuer
binding. That owner-readable JSON has `version: 1`, the matching `tenant` and
`issuer`, `schema`, and an explicit `postgresql+psycopg` `databaseUrl`. It selects
the PostgreSQL media registry; version 1 retains its existing behavior. Loading
configuration opens no connections and creates no state. Each registry connection
checks that its role cannot bypass tenant isolation or own the three required
media tables. Missing schemas, tenants or grants refuse operation; installation
and reconciliation of an existing allocation index must precede selection.

The private payload upload route is
`PUT /api/v1/storage/media/payloads/{object}/{ciphertext|wrap}/{sha256}`.
It requires the separate `storage:media:payload:write` service permission,
`X-Media-Owner`, a bounded Content-Length, and opaque uncompressed bytes.
The durable PostgreSQL allocation must already belong to that owner and be
active. The registry binds the provider location and charges the immutable
reference before provider access; failed uploads remain charged and eligible
for the accepted deletion command's cleanup. Existing generic object and media
record permissions do not grant this route. This is a trusted service boundary;
the upstream protected source still authorizes owner operations.
No client cutover, credential staging, schema application or data import occurs.
