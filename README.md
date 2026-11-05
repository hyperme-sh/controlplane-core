# controlplane-core

Shared legacy API infrastructure under the Python import name `api`, version
1.0.0. Compatibility modules alias the independently installed API, identity and
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

The enabled main lane uses scoped private dependency read access on the
shared-local runner group. The hosted PR lane uses verified fixtures without
private or deployment credentials.

Use only a disposable PostgreSQL server with database-creation permission. The
OIDC regression creates and drops a unique database. Metrics and tenant-host
regressions run in all package contexts. The deployment metrics check remains
under integration/ and needs SPANOPTICON_CHECKOUT pointing to the owning monorepo.

## Release channels

Successful main-push validation retains the validated Python wheel and source distribution and publishes
an immutable `test-<commit>` GitHub prerelease. Push a `vMAJOR.MINOR.PATCH`
tag at that tested commit to promote the exact files to a stable release,
without rebuilding. Repository visibility applies to all package assets.
Package metadata versions are preserved; these are GitHub distribution snapshots,
not automatic PyPI publication or service deployment.

Promotion requires the original Actions artifact (90-day requested retention;
organization policy may shorten it). Expired evidence fails closed: validate a
new main revision. The publisher verifies the run, source, workflow and artifact
digest independently of release metadata. Repository immutable releases must be
enabled. See the [shared release contract](https://github.com/hyperme-sh/.github/blob/7749591fccd30d46ba9b0adabe82114001c9d0de/actions/release-channel/README.md).

## Release qualification

The package and its direct private dependency graph follow the 0.24 line. Main
uses ordinary immutable Git installs; hosted PR consumers use published dependency
wheels verified against full source revisions, lock identities, versions and
digests, including the user-service-owned contract subdirectory. Fixtures are
excluded from distributions. Run `bash scripts/check-package.sh --fixtures` with
a disposable `TEST_DATABASE_URL` to reproduce the isolated consumer lane.

The release candidate binds the exact wheel digest and complete packaged `api`
runtime inventory. Source, installed-wheel and rebuilt-sdist consumers must pass;
real database tests remain mandatory. This does not establish live service/provider
acceptance or installer/dependency metadata patch compatibility.

Development snapshot evidence is retained in `releases/qualification`. A test
prerelease does not establish an accepted stable baseline or reserve a version.
Stable promotion retains the shared live provenance checks.

## Closure dependency adoption

This 1.0.0 development qualification preserves the closure audit attribution fix while adopting
merged API 0.24.1 and identity 1.0.0 revisions. The conservative full-runtime
contract differs from the historical 0.24.0 test artifact. That test snapshot
is qualification evidence, not an accepted stable baseline. The development
version is preserved; first stable publication remains separately qualified.

DB and tenancy closure revisions are still pending owner PRs. Their exact Git
archive builds are explicitly marked as unpublished fixtures in the dependency
manifest; API, identity, events and operator-ban fixtures are published artifacts.
Fixture qualification does not claim release or deployment acceptance for those
pending owners. They must merge and be requalified before coordinated cutover.

## Test qualification and stable acceptance

GitHub `test-<commit>` prereleases are development snapshots. Their embedded
package version does not reserve a stable version. Artifact and consumer evidence
lives under `releases/qualification`; the current wheel binding is still checked
by package CI, alongside any future explicit stable candidate. Earlier test
snapshot receipts are preserved there with an explicit non-stable classification.

`releases/accepted` and `SPANOPTICON_RELEASE_BASELINE_SHA` are reserved for an
actual immutable stable GitHub release. The pinned shared guard verifies the live
release channel, stable tag source and asset digests; a test snapshot cannot
become accepted through a provenance label. No stable baseline is recorded until
that release exists. Stable publication remains closed without a valid baseline;
first stable bootstrap and coordinated release qualification remain separate.
No stable version, approval prerequisite or deployment is introduced here.
