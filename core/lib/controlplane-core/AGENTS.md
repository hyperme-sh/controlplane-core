# controlplane-core package context

## Ownership
Shared legacy API infrastructure and common controllers. Python import name: `api`. Identity and tenancy implementations now live in controlplane-identity and controlplane-tenancy; the listed legacy identity/persistence modules alias their implementations.

## Public interface
The exact public Python modules are the `api` entries in [`scripts/import_boundaries.json`](../../../scripts/import_boundaries.json).
The contract is exact-module based, not permission to import every descendant.
Declared modules: `api.common.event_types`, `api.common.exceptions`, `api.common.internal_token`,
`api.common.logging`, `api.common.person_bind`, `api.common.portal_controls`, `api.common.utils`,
`api.core`, `api.core.config`, `api.domain.enum.permission`, `api.domain.operator.audit`,
`api.domain.operator.bans`, `api.domain.operator.claims`, `api.domain.operator.doors`,
`api.domain.operator.mail`, `api.domain.operator.plugins`,
`api.infrastructure.externals.api_keys_client`, `api.infrastructure.messaging`,
`api.infrastructure.messaging.tenant_event_publisher`, `api.infrastructure.metrics`,
`api.infrastructure.persistence.db_session`, `api.infrastructure.persistence.tenant_guc`,
`api.infrastructure.persistence.tenant_membership`, `api.infrastructure.security`,
`api.infrastructure.security.api_key_exchange_jwt`, `api.infrastructure.security.keycloak_jwt`,
`api.infrastructure.security.tenant_session_jwt`, `api.infrastructure.service_cors`,
`api.interfaces.api_controllers.health_endpoint`, `api.interfaces.api_controllers.version_endpoint`.

## Source of truth
Start with [../../../Design.md](../../../Design.md) and the package's source/tests. Repository dependency policy: [../../../AGENTS.md](../../../AGENTS.md).

## Stop reading here
Start inside this package. Follow another package's context file only when the task crosses its interface; do not sweep unrelated product docs. Shared database migrations belong in `core/lib/controlplane-db`, shared API helpers in `core/lib/controlplane-core`, and event contracts in `core/events`. Cross-package edits must be justified by the requested change.
