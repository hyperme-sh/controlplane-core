# controlplane-core

Shared legacy API infrastructure under the Python import name `api`, version
0.1.0. Compatibility modules alias the independently installed API, identity and
tenancy implementations to preserve shared settings, session context and registries.

## Development

Use Python 3.13 and uv 0.11.26 with read access to the private hyperme-sh
controlplane-api, controlplane-tenancy, controlplane-identity, controlplane-db,
controlplane-events and user-service repositories. Full dependency commit pins
are included in ordinary distribution metadata and uv.lock. The operator ban
contract is installed from its owning user-service repository subdirectory.

```sh
uv sync --frozen
TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:PORT/postgres bash scripts/check-package.sh
```

Checks validate compatibility aliases and dependency boundaries from source,
a fresh wheel outside the checkout and a wheel rebuilt from the source archive.
Both wheel payloads must match runtime bytes. These checks do not establish full
service integration, provider behavior or deployment readiness.

Actions remains disabled pending scoped private dependency read authority and
hosted-worker policy. Do not copy deployment credentials or use shared runners.

Use only a disposable PostgreSQL server with database-creation permission. The
OIDC regression creates and drops a unique database. Metrics and tenant-host
regressions run in all package contexts. The deployment metrics check remains
under integration/ and needs PANOPTICON_CHECKOUT pointing to the owning monorepo.
