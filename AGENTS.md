# Core development

Runtime code lives under src/api. Preserve legacy alias module identity, settings,
session context, public routes and owner trust boundaries. Database migrations
remain owned by controlplane-db. Read README.md and run bash scripts/check-package.sh.
Keep immutable dependency pins in standard metadata and uv.lock. Consumer service
and live-provider validation remain separate from package checks.

## Package publication permission boundary

Source/package validation keeps its existing runner policy and read-only contents.
The separate `.github/workflows/release.yml` publication job is the narrow exception:
it may use `contents: write` and `actions: read` on disposable `ubuntu-24.04` workers
solely through the exact-pinned shared package publisher. It must not check out or
execute this repository's code, install its packages, or receive private dependency
or deployment credentials. This hosted publication exception does not authorize
moving secret-bearing validation to another runner group. Stable publication must
retain any configured release-admission prerequisite for the exact tag/commit.
