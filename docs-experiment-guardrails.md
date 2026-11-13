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
