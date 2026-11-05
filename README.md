# controlplane-core

## Current version authority

Current development version: `0.1.0`. The services remain before the unreleased
0.24.0 milestone. Earlier 0.24/1.0 preparation and publication notes below are
historical and do not authorize those versions. Existing tags and historical
artifacts remain untouched. Development uses exact source pins and rebuilt
artifacts; no prior release is required. Version changes, tags and publication
require the owner's explicit authorization to the agent.

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

The enabled main lane uses scoped private dependency read access on the
shared-local runner group. The hosted PR lane uses verified fixtures without
private or deployment credentials.

Use only a disposable PostgreSQL server with database-creation permission. The
OIDC regression creates and drops a unique database. Metrics and tenant-host
regressions run in all package contexts. The deployment metrics check remains
under integration/ and needs SPANOPTICON_CHECKOUT pointing to the owning monorepo.

## Release channels

Main validation retains its exact qualified files in Actions artifacts. It does
not create GitHub releases or tags. Historical `test-<commit>` prereleases remain
qualification evidence and do not establish an accepted stable release.

Stable publication requires an explicit `vMAJOR.MINOR.PATCH` tag, the exact
successful main validation artifact, compatible declared contracts and an actual
immutable stable baseline. The publisher independently verifies that evidence;
successful caller jobs alone cannot establish acceptance. Missing evidence and
first-stable bootstrap remain closed. Package qualification, coordinated consumer
acceptance and runtime deployment remain separate.

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

This development candidate preserves closure audit attribution and uses the
corrected exact-source API and identity dependencies. Compatibility differences
are qualification findings, not authorization for a major release.

DB and tenancy closure revisions are still pending owner PRs. Their exact Git
archive builds are explicitly marked as unpublished fixtures in the dependency
manifest; API, identity, events and operator-ban fixtures are published artifacts.
Fixture qualification does not claim release or deployment acceptance for those
pending owners. They must merge and be requalified before coordinated cutover.

### Actions qualification and stable publication

Main merges and workflow completion do not publish GitHub test releases. Exact
qualification files remain in Actions artifacts with finite retention. The pinned
publisher independently verifies a real immutable stable baseline and refuses
qualification snapshots, even if a caller job reports success. Existing valid
stable records are retained; no release, version change, deployment or human
approval prerequisite is introduced by this correction.

## License

This package is licensed under [Apache-2.0](LICENSE). Dependencies retain their
own licenses; this declaration does not relicense bundled third-party material.

## Coordinated test bundles

This service opts into the `next: 0.24.0` train through
[.github/test-channel.json](.github/test-channel.json). Successful main validation
publishes its exact retained `tested-dist` files as immutable `0.24.0-test.N`
prerelease bundles. The pinned publisher verifies the run, source revision,
configuration and original Actions artifact digest without executing caller code
or receiving private dependency credentials. PRs validate without publication.

This test-channel authorization supersedes the older main-publication restriction
above. Package versions, dependency pins and accepted package baselines stay
unchanged; existing stable package admission remains separate. Coordinated
consumer/deployment selection must bind exact component commits and artifact
digests, not matching repository-local counters. No stable promotion is automatic.
Private jobs use debrunners and public jobs use hosted Ubuntu; no approval gates
are introduced. Missing original evidence or publication collisions fail closed.

## Interactive identity policy adoption

The exact identity and API pins include ordinary JWT age-confirmation enforcement.
Seven consumer regressions exercise core legacy aliases in source, wheel and
rebuilt-sdist contexts, including machine and service exemptions. This qualification
does not establish live provider or complete self-hosted acceptance.
