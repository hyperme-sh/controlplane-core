# Tenant events and authenticated identity

Core #53 follows the existing owner boundaries:

- [Architecture decisions 5 and 6](https://github.com/hyperme-sh/hypermesh-docs/blob/main/design/hypermesh-architecture-v0.md): state commits in its owner; events are derivative facts in the same transaction.
- [User mutation authority](https://github.com/hyperme-sh/hypermesh-docs/blob/main/design/user-service-mutation-authority-2026-11-05.md): creating a tenant user requires current subject-bound tenant administration. An email or coincident local UUID does not establish membership.
- [User scoped authority](https://github.com/hyperme-sh/user-service/blob/main/docs/scoped-authority.md): network administrators need not have a tenant profile; login cannot manufacture organizations, roles or memberships.

Auth `grant_held_roles` and `authorize_tenant_switch` call the User identity
owner. The owner selects and checks the tenant and current membership in its
transaction. Its directory creation path publishes a tenant event. Core's legacy
`ensure_user_from_keycloak_claims` instead projects the validated issuer subject
into an unscoped identity record. That operation is not tenant enrollment, so its
former tenant-user event call is removed. No platform event namespace or synthetic
tenant is introduced. Legacy unscoped user creation likewise emits no tenant fact.

Core tenant creation and tenant-user creation write through the same SQLAlchemy
session as their business row. Savepoints roll both writes back if publication
fails, even when a caller catches the error. Outer rollback still removes both.
The tenant creator temporarily selects the newly created tenant for its outbox
insert and restores the caller context; failed SQL rolls back the savepoint.
Existing tenant-user writes retain their established tenant context and RLS.

The shared publisher accepts old transport configuration arguments during rolling
adoption but selects only the owner outbox. Unbound or unconfigured publication
fails closed. No helper opens or commits a separate event transaction. Outbox
schema and HTTP relay deployment remain responsibilities of the consuming owner.

PR checks cover mocked transaction failure, stable retry, identity projection,
wrong-scope refusal and retired-transport selection. PostgreSQL commit, rollback,
insert failure and RLS cases run only in Infra's scheduled Core lane.
