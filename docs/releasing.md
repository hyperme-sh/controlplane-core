# Releasing controlplane-core

The installed distribution version is `0.1.0`, recorded in `pyproject.toml`
and in `release-policy.json` (`line` `0.1`). The coordinated test channel in
[`.github/test-channel.json`](../.github/test-channel.json) is `next: 0.24.0`.
A test-bundle tag carries that channel version. It leaves the distribution
version in `pyproject.toml` unchanged.

Direct dependencies are exact git revisions in `pyproject.toml` and `uv.lock`:

| Distribution | Version | Revision |
| --- | --- | --- |
| controlplane-api | 0.23.0 | `732256a5207ee502d7bf70cb171cf2eea1803b87` |
| controlplane-tenancy | 0.14.0 | `f252909af772b2c820555af36de456d46f17cfcb` |
| controlplane-identity | 0.15.0 | `5aeff3d2c00568f39ca6e8b75c7e63883d3a510b` |
| controlplane-db | 0.1.0 | `95ef61433374274f4a4fc287e15982be6357e1cc` |
| controlplane-events | 0.1.0 | `2a49f9af5f6c4dc9d6c4ddebc83c41d6331e2c87` |
| operator-ban-store | 0.1.0 | `17b11625766e6ceb2c631f58d02211d776f94bae` (user-service, `core/user-service/contracts`) |

Building and testing from those revisions does not require a previously
published GitHub release of this package. Fixture wheels under
`tests_artifacts/dependencies/` are local builds of those same revisions.
Their manifest records digests and source provenance. A fixture is a
consumer-lane input, and it is not a release of the dependency.

## Main validation

[`.github/workflows/package.yml`](../.github/workflows/package.yml) runs on a
push to `main`. It executes `bash scripts/check-package.sh` against a
disposable PostgreSQL 17 service and uploads:

- `tested-dist` — the exact wheel and sdist that passed, retained 90 days,
  stored uncompressed
- `package-test-results` — the source, wheel, and rebuilt-sdist JUnit files

That workflow does not create a GitHub release or a git tag.

Pull requests run
[`.github/workflows/consumer-qualification.yml`](../.github/workflows/consumer-qualification.yml),
which calls `bash scripts/check-package.sh --fixtures`. That job validates
the same three package contexts from the fixture wheels and does not publish.

Private repositories select the runner labels
`[self-hosted, linux, x64, debrunner]`. Public repositories use
`ubuntu-24.04`.

Historical `test-<commit>` prereleases remain qualification evidence. They
are not an accepted stable release, and the version string embedded in a
snapshot does not reserve a stable version. Current qualification files live
under `releases/qualification`.

## Test bundles

After a successful `main` package run whose event was a push,
[`.github/workflows/test-bundles.yml`](../.github/workflows/test-bundles.yml)
publishes the retained `tested-dist` bytes as an immutable
`0.24.0-test.N` prerelease bundle. The pinned publisher checks the workflow
run, the source revision, the channel configuration, and the original Actions
artifact digest. It does not execute this repository and does not receive
dependency credentials.

Pull requests validate and do not publish. The bundle counter belongs to this
repository and the `0.24.0` target. A deployment that consumes the bundle
selects the component commit and the artifact digest. A matching local counter
is a different value.

A test bundle does not change `pyproject.toml`, the dependency pins, or the
accepted stable package. Stable promotion is a separate tag.

## Stable publication

[`.github/workflows/release.yml`](../.github/workflows/release.yml) runs on a
`v*` tag. It calls the Spanopticon component-release workflow with release
line `0.1`, then the pinned release-channel action publishes the exact tested
artifacts from the package workflow. The publication job has `contents: write`
and `actions: read` so it can upload those bytes. It does not install this
package in order to rebuild it.

A stable publication requires all of the following:

1. An explicit `vMAJOR.MINOR.PATCH` tag.
2. The exact `tested-dist` artifact from a successful main validation of that
   source revision.
3. Declared contracts that match the packaged `api` runtime inventory
   (`scripts/export_release_contract.py`).
4. An immutable stable baseline: `releases/accepted/release.json` plus the
   repository variable `SPANOPTICON_RELEASE_BASELINE_SHA` set to that
   baseline's full 40-character commit SHA.

The publisher checks that evidence itself. A successful caller job does not
stand in for the baseline. A missing baseline fails closed, and a
qualification snapshot is refused even when a job reports success. Changing a
published artifact at the same version is rejected. Patch contracts stay
compatible; see [release compatibility](release-policy.md) and the
[Spanopticon release policy](https://github.com/hyperme-sh/spanopticon/blob/main/docs/releases.md)
for the manifest format.

Package qualification, coordinated consumer acceptance, and runtime deployment
are separate steps. These workflows do not deploy a plane.

## Identity compatibility on this pin

The pinned `controlplane-identity` revision requires an age-confirmation
timestamp (`terms_and_conditions`) on interactive Keycloak access tokens.
The default client allow-list includes `controlplane-frontend`,
`controlplane-mobile`, `controlplane-device`, and `hypermesh-native`.
Machine clients (`controlplane-m2m`) and tokens with `account_type=service`
are exempt. `tests/test_identity_age_adoption.py` collects seven cases and
runs them from source, from the installed wheel, and from a wheel rebuilt
from the source archive.

Passing those cases qualifies this package against that identity pin. It does
not by itself accept a live identity provider or a self-hosted plane.
