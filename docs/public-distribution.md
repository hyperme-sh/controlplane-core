# Core publication preparation

The existing GHCR storage image remains a moving TEST channel. PR and main
qualify real locked public dependencies and internal package forms; these forms
are build inputs, not separately published service archives.

The image carries the owner LICENSE/NOTICE and an index of every installed Python
distribution under `/usr/share/doc/controlplane-core`. The index records versions,
declared licenses, original legal-text paths and SHA-256 values. CI checks copied
bytes against both the index and installed distribution files. Missing texts,
changed supplemental versions/hashes or missing distributions fail the build.
Exact upstream supplements for Streamlit, jwcrypto and psycopg preserve additional
legal text; their origin records and verbatim bytes live under
`.github/workflows/third-party-notices`. This is a Python dependency inventory,
not a completed Debian/base-image or native-subcomponent source review.

History CI requires a full checkout and reads HEAD plus fetched origin branches
and tags without checking out or executing old code. It includes commit/tag bytes
and expands ZIP/wheel members. The initial scan inspected 1,290 objects and found
515 provider-related coordinates. Those retained old implementations and payloads
remain present; no source publication clearance is claimed. Future runs report
fresh counts and coordinates. Unreachable/deleted refs, hidden server refs, remote
LFS bytes and external archives are not covered.

The initial secret scan had 237 non-secret matches: 176 packaged-source SHA-256
values, 56 migration-member SHA-256 values and five Python key type annotations.
The hashes were verified against retained Git blobs or members of the same wheel.
The review file records those references. Scanner exceptions require the exact
exported file name, complete file hash, rule and start/end lines. New or changed
matches fail. Scanner output contains only counts and coordinates, never source
or credential values. This does not clear the separately reported hosted content.

Remaining work under FyberLabs/infra#993 includes historical-content disposition,
older-image and every-layer scans, and Debian source archive bundling. The scoped
Python dependency source/replacement work is described below. No repository/package
visibility, history rewrite, manual release or TEST pin is authorized here.
Integration/e2e remains in scheduled nightly CI.

## LGPL/MPL source follow-up

The storage image now bundles the five reviewed Python components' source/build
materials and verifies their pairing with installed versions/native bytes.
[Source and replacement preparation](../licenses/SOURCES.md) describes the exact
scope and offline replacement checks. This completes that dependency follow-up;
Debian source archive bundling, retained-content disposition and full image-layer
review remain separate. Existing notice texts are preserved.


## Base source/legal follow-up

[The Core base review](../licenses/BASE.md) records this image's own package/source
versions, legal files and retained layers, and adds matching CPython sources.
Debian source archive bundling remains a follow-up, along with full image-layer
and historical/older-image review. No base digest or TEST channel was changed.
