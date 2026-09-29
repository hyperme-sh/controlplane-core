# controlplane-core package context

## Ownership
Shared API infrastructure, authentication, tenant context, persistence helpers, and common controllers. Python import name: `api`.

## Public interface
The exact public Python modules are the `api` entries in [`scripts/import_boundaries.json`](../../../scripts/import_boundaries.json).
The contract is exact-module based, not permission to import every descendant.

## Source of truth
Start with [../../../Design.md](../../../Design.md) and the package's source/tests. Repository dependency policy: [../../../AGENTS.md](../../../AGENTS.md).

## Stop reading here
Start inside this package. Follow another package's context file only when the task crosses its interface; do not sweep unrelated product docs. Shared database migrations belong in `core/lib/controlplane-db`, shared API helpers in `core/lib/controlplane-core`, and event contracts in `core/events`. Cross-package edits must be justified by the requested change.
