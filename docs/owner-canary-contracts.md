# Storage and embedded archive rollout contracts

Core #105 supplies inactive owner checks. Production billing stays disabled.
No deployment binding, synthetic tenant, credential, scheduler, rollout threshold,
or production promotion is created by this package.

## Storage

`api.storage.canary` checks a declared synthetic tenant and one bounded record in
the `fixtures` namespace. The run descriptor expires within five minutes. Infra
must persist its nonsecret descriptor before the first write, serialize access to
that run ID, and register independent cleanup before enabling either target.
Credentials remain in managed private inputs, never in the descriptor.

Both targets default disabled. Enabling requires private, deployment-owned routes
that reliably select baseline and candidate builds, separate scoped credentials,
and the expected source revisions. `/version` uses the existing service build
metadata; it is a diagnostic cross-check, not an authentication mechanism. A
load-balanced route that cannot select a build is not a valid binding. Revisions
identify the checked binaries; they do not pin moving TEST services.

The probe refuses unauthenticated reads, creates through baseline, reads and
updates through candidate, then reads through baseline and refuses stale writes
and deletes. Each operation checks target identity and run expiry. Cleanup uses
only the exact run bytes and a conditional revision; it also works after expiry.
Conflicting bytes, unknown revisions, transport errors and cleanup failures remain
failures. The owning scheduler must retain the descriptor and retry independent
cleanup after interruption; expiration alone does not delete a record.

The PR codec check runs baseline and candidate source against one persistent file
provider double in separate processes. It checks both-direction reads, writes,
rollback and stale revisions. It requires identical dependency metadata; a changed
graph needs separate environments. This is an offline codec contract, not a claim
that deployed routes or a storage provider have been qualified.

## Embedded archive relay

`queue_status` reads only the owner's outbox and declared tenant. It reports pending
count and oldest age using the database clock, refuses invalid/future timestamps,
and represents an empty queue with a null age. No payloads or receiver database
credentials are returned or requested. Thresholds and telemetry bindings remain
with the deployment owner under Infra #1957 and #1967.

The scheduled `controlplane-core-installed` adapter uses the actual locked Core
and Events packages with the Infra-owned disposable PostgreSQL fixture. Existing
cases cover business commit, rollback, insert failure and stable operation retries.
The archive case commits an inbox entry, loses the acknowledgement, loads the previous Core wheel in a fresh process and retries the same event ID without duplicating the inbox.
Queue age and empty-queue recovery are asserted.

The transport is a callback double and receiver/owner use separate transactions in
one disposable schema. This case does not qualify HTTP authentication, production
receiver isolation, or two deployed service versions. Infra builds the exact candidate first-parent
wheel and requires identical dependency graphs for this baseline resume check.
The baseline/current codec and archive restart checks remain isolated checks. Host services retain
ownership of their own archive adapters; live version-window and receiver routing
qualification remains with Infra #1967. Do not dispatch shared validation manually.
