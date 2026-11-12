# GrowthBook policy import publisher

This is the Core owner prerequisite for FyberLabs/infra#845 and #923. Run
`python -m api.growthbook_publisher` as the **only publisher** for an imported
tenant/audience binding. Do not also run the static `api.experiment_publisher`
for that binding: the static publisher deliberately renews operator-held policy
without an external freshness dependency. Existing static bindings are unchanged.

Each import first authenticates to GrowthBook's REST API and checks its SDK
connection ID, organization, exact single project, environment and SDK key.
Only a plain SDK connection without drafts, visual/redirect experiments, proxy
or remote evaluation is accepted. A second request reads the scoped SDK payload.
It must contain exactly one unconditional JSON feature, `controlplane-policy`:

```json
{"version":1,"tenant":"<canonical UUID>","audience":"s3r.ch","experiments":[]}
```

Use the existing Core experiment schema for `experiments`: key, control,
variants, weights, metrics, guardrails and disabled. Raw identity and targeting
rules are refused. GrowthBook's SDK response is not a signature. The existing
Core private policy/store and dedicated Ed25519 key still own publication;
consumers receive the same bounded signed payload as before.

The worker only imports/publishes after a fresh successful source read. Source
errors, wrong scope and invalid policy cannot renew the cached payload. The
maximum stale interval is configurable from 1 to 300 seconds (default 300),
measured from publication; shorter limits produce shorter signatures. Polling
is every 30 seconds, so choose a limit greater than that interval for continuous
availability. Existing policy is conditionally updated; unchanged policy does
not allocate revisions. Imports preserve existing guardrail disables. Re-enabling
requires an explicit authorized operator action, never a refresh side effect.

Configure the existing `EXPERIMENT_TENANT_ID`, `EXPERIMENT_AUDIENCE`, signing key
and database/provider settings, plus `GROWTHBOOK_API_ORIGIN` (direct internal
HTTPS origin), `GROWTHBOOK_ORGANIZATION`, `GROWTHBOOK_PROJECT`,
`GROWTHBOOK_ENVIRONMENT`, `GROWTHBOOK_SDK_CONNECTION`,
`GROWTHBOOK_MAXIMUM_STALE_SECONDS`, `GROWTHBOOK_READ_TOKEN_FILE` and
`GROWTHBOOK_SDK_KEY_FILE`. Each credential file must be a single-link regular
file owned by the worker UID with mode 0600. Provision a separate read-only API
key restricted to that project/environment and its dedicated SDK key. Neither
operator/database credentials nor signing keys are sent to GrowthBook SDK
consumers. `GROWTHBOOK_CA_FILE` optionally supplies an operator-trusted CA bundle;
TLS verification is always on, redirects and ambient HTTP proxies are off.

The deployment owner must provide operator authentication/ingress and SDK scopes,
select this entry point exclusively for imported bindings, and register the real
GrowthBook fixture. This change does not activate any tenant. Unit/contract tests
use explicit mock HTTP and an in-memory conditional record backend. They cover
scope refusal, separate credentials, outage without renewal, bounded validity,
conditional policy updates and retained guardrail disables.
