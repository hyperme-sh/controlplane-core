# Retained Core distributions

History classifications are bound to exact Git blob and archived member hashes.
All 515 observed coordinates remain retained: 458 database payment/schema entries,
18 database storage implementations, 23 provider-related owner code entries,
eight provider event contracts, four settings entries and four provider-name
comments. The classifications describe retained content, not public clearance.
New findings or changed bytes refuse CI. No history or tags are removed.

The initial GHCR package inventory exposed 43 retained storage image versions,
including 42 untagged versions and the moving test image. CI independently lists
current tags and all retained package versions, resolving Linux amd64 manifests
by verified digest. It then statically inspects every retained layer without
executing old containers. Digests identify reviewed bytes, not deployment pins.

Per-image logs report observed user, legal/source-bundle paths and hosted content
paths. A separate secret scan checks all regular layer bytes and image metadata.
Exceptions cover the exact public Python signing fingerprint, reviewed historical
non-secret file bytes and checksum-bound upstream source fixtures. Unexpected
matches fail and print coordinates only. Deleted registry versions, other
platforms and other registries remain outside this review. API denial is not proof
of absence. Retained hosted modules and missing notices/sources remain explicit
publication limitations even when a secret scan succeeds.

GHCR settings, visibility and historical refs are unchanged. TEST remains on the
moving storage:test channel; integration/e2e remains in scheduled nightly CI.
