# Private experiment guardrails

`python -I -m api.experiment_guardrails` runs the existing conditional disable
controller against a dedicated Azure Logs table, `ExperimentGuardrail_CL`.
There is no browser report endpoint. Ordinary Application Insights events,
exposure counts and conversion counts are never queried as disable authority.

The deployment binds `EXPERIMENT_TENANT_ID`, `EXPERIMENT_AUDIENCE`,
`EXPERIMENT_METRICS_WORKSPACE`, `EXPERIMENT_METRICS_DIRECTORY`,
`EXPERIMENT_METRICS_CLIENT_ID` and `EXPERIMENT_METRICS_SECRET_FILE`. The last is
an absolute, owner-only regular file containing the query principal's client
secret. The worker uses the existing private tenant provider/DB configuration.
It receives no payload-signing or analytics-publishing credential. The query
principal has read access only to this dedicated table. It cannot ingest rows.

Two separate ingestion principals publish independently observed `server` and
`platform` measurements through separate data collection rules (DCRs). Each DCR
must fix tenant, audience and source in its transformation; these fields are
never accepted from the ingestion body. Publisher roles are scoped to their
own DCR and withheld from browsers, ordinary analytics sinks and the query
worker. The table must not admit unauthenticated/legacy ingestion. Product
servers measure actual work; platform collectors independently measure the
corresponding service operations. Neither accepts client-provided failure
counts, durations, sample IDs or source labels as measurements.

Rows contain only a server-generated UUIDv4 action ID, deployment scope,
compiled experiment/variant/metric labels, observation epoch seconds, numeric
value and source. Failure metrics use one zero-or-one observation per action;
latency uses elapsed milliseconds. No raw identity, assignment hash, cookie,
request content, address or user agent is included. Source-specific duplicate
action IDs count once. Conflicting duplicates fail the query.

Both sources must meet the configured minimum sample count and exceed the
threshold in the same window. The controller receives the smaller source mean
and sample count, so one observer cannot independently disable an experiment.
A source's latest observation must be within five minutes (or the whole window
when shorter). Queries are capped at 10,000 rows; overflow, partial responses,
wrong scope, unavailable credentials, redirects and provider errors cannot
produce a disable. A complete controller tick has a 30-second deadline. Large
configurations that cannot finish within that limit remain unchanged and log a
redacted unavailable status; size deployment windows/sample volume accordingly.

A successful conditional update increments the configuration sequence. The
existing publisher picks it up and emits a signed payload; GrowthBook imports
preserve disabled tombstones. Only an explicit operator update can re-enable.
The worker runs every 30 seconds and never changes a service release channel.

Owner configuration for DCRs, publishers and platform measurement adapters is
still required before activation. This contract does not claim that ordinary
browser analytics provides trusted guardrail measurements. The scheduled
nightly batch owns provider and browser scenarios under Infra #923.

References: [Logs query requests](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/request-format),
[authenticated DCR ingestion](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-ingestion-api-overview).

## Measurement producer preparation (Core #61)

`api.domain.experiments.producers` supplies an independently timed server
observer and a private platform HTTP relay. `python -I -m api.experiment_measurements`
exits without opening sockets or reading credentials unless
`EXPERIMENT_MEASUREMENTS_ENABLED=1`. It is not mounted on the public Core app.
Activation remains disabled. Native consumer injection is still required;
these primitives alone do not complete #61.

The native owner must verify current assignment, identity, consent and retained
variant before calling `ServerObserver.request`. It passes compiled experiment,
variant and operation names. The observer creates a fresh UUIDv4; a browser event
ID is never reused. Initial/coalesced cache refreshes happen before assignment
and cannot be labeled with a newly selected variant. The first coordinated
consumer is the existing s3r.ch outbox GET after native verification. It measures
**telemetry-delivery availability**, not identity/session readiness or the health
of unrelated product operations. Its runtime wiring is owned by the native
consumer after s3r.ch #552.

The dedicated relay accepts POST `/v1/observe`, an exact private bearer secret,
`Content-Type: application/octet-stream`, and `X-Experiment-Attribution` JSON:
`action`, `experiment`, `variant`, `operation`, `issued`, optional `target`.
Only deployment-configured operation names select HTTPS destination, method,
upstream credential headers, timeout and response bound. For the outbox GET,
`target` must be exactly 64 lowercase hexadecimal characters appended to the
fixed consent-record prefix; the native server derives it from its own verified
consent-scoped record name. No arbitrary path, query or incoming credential
header is forwarded. GET requires an empty body. ETag, status and bounded body
are preserved; no cookies, redirects or unrelated upstream headers propagate.
Native consumers must enforce their existing tighter timeout/size constraints.

Server and platform independently time their own HTTP exchange. HTTP 5xx,
transport errors, oversize replies and refused redirects count as unavailable;
ordinary client refusals (including 401/403) do not. The operation kind selects
`dependency_failure_rate` or `session_failure_rate`, alongside `error_rate` and
`latency_ms`. Session metrics describe session-service HTTP availability only,
not whether an identity assertion is valid. Neither observer accepts reported
counts or duration. Tenant, audience and source never enter the submitted DCR
rows: the separate existing DCRs fix those constants.

Attribution expires after 60 seconds. SQLite admission retains action IDs across
restart, refuses replays, caps admission at 60/minute, and has no raw payload or
identity columns. Sampling selects UUID modulo the deployment's `sample_every`;
unsampled/default-disabled calls use the existing native path. A sampled relay
failure is explicit: no hidden retry or second upstream operation. The native
caller owns its existing operation-failure behavior. DCR failure does not alter
a completed operation. `BoundedPublisher` provides a separate 64-item in-memory
queue, three-second delivery budget and 60-second expiry; full/stopped queues
drop samples. Never send these measurements through the measured analytics
outbox. Missing source observations cannot authorize a disable.

The runner binds loopback port 8097, disables access logs and trusts no proxy
headers. Deployment must provide a private TLS listener inaccessible to browsers,
a private durable state directory, and a **platform-only** DCR publisher identity.
It reads `EXPERIMENT_MEASUREMENTS_BINDING_FILE` (compact, owner-only JSON),
`EXPERIMENT_MEASUREMENTS_RELAY_SECRET_FILE` (64 lowercase hex),
`EXPERIMENT_MEASUREMENTS_STATE_DIRECTORY`, `EXPERIMENT_MEASUREMENTS_DIRECTORY`,
`EXPERIMENT_MEASUREMENTS_CLIENT_ID`, `EXPERIMENT_MEASUREMENTS_SECRET_FILE`,
`EXPERIMENT_MEASUREMENTS_INGESTION_ENDPOINT` and `EXPERIMENT_MEASUREMENTS_DCR_ID`.
Binding JSON contains `tenant`, `audience`, `experiments` (compiled variant lists),
`operations` and optional `sample_every` (default 100). Each operation contains
`url`, `method`, `kind`, optional fixed `headers`, `suffix` (`none` or `sha256`),
`timeout_seconds` (default 3) and `response_limit` (default 65536).

Server ingestion, relay authentication, upstream operation access, platform
ingestion and the existing table reader use separate credentials. Deployment
must bind operation access and its fixed destination to the same tenant as the
DCR. No credentials or provisioning are supplied here. Local tests use explicit
HTTP transports and SQLite in temporary directories, not provider services.
