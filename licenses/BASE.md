# Core base-image source/legal review

This review covers the existing Linux amd64 `python:3.13-slim-bookworm` base
selected by index digest `281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f`.
The selected platform manifest and configuration digests are recorded in
`base-image.json`. The index, manifest, configuration and downloaded layer bytes
were checked against their registry digests during static inventory collection.
No base image was executed to collect the inventory, and the Dockerfile base is
unchanged.

The inventory records four original base-layer identities, the exact dpkg status
bytes, 97 installed binary/source-version pairs and 115 legal-file hashes.
Copyright symlinks were resolved within the filesystem. CI checks that the final
storage image retains the original layer prefix, dpkg status and legal bytes;
changed or missing records fail. The inherited copyright/common-license files
remain in their original locations.

Core's base differs from the API base: apt/libapt-pkg6.0 are 2.6.1 and tzdata is
2026e-0+deb12u1. Its own versions and layer identities are retained. All 115 legal
file contents match the previously reviewed API texts, but that comparison does
not substitute the API's different package inventory or grant public clearance.

CPython 3.13.18 source is bundled at `/usr/share/doc/controlplane-core/sources`.
Its archive SHA-256 matches the existing base configuration's PYTHON_SHA256.
CI verifies that source archive and the installed
`/usr/local/lib/python3.13/LICENSE.txt` against the archive's LICENSE. Source scans
retain the exact upstream ssl-documentation private-key placeholder; its exception
is bound to the archive/member hashes, rule and line, not runtime files.

The 97 Debian binary packages map to 69 exact source-version pairs, with source
references in the inventory. All 69 source descriptors and 153 referenced files are now bundled under
`/usr/share/doc/controlplane-core/debian-sources`. CI checks full source coverage,
descriptor Source/Version and Checksums-Sha256 fields, file sizes and actual bytes.
The bundle includes upstream signatures and Debian patches where listed. No Debian signer trust-chain validation or bit-identical
base/native rebuild is claimed. The wheel-bundled libcrypt is separately paired
with libxcrypt 4.5.2; Debian libcrypt1 is recorded as libxcrypt 1:4.4.33-2.

Full image-layer secret scanning, retained historical-content disposition,
older-image review and public distribution clearance remain separate under Infra
#993. This review changes no visibility, historical refs, service pins or manual
release settings. GHCR storage:test remains moving; integration/e2e remains nightly.


Sources use official Debian main/security mirrors. The exact tzdata
2026e-0+deb12u1 files have left those mirrors and are retrieved from Debian
Snapshot's content-addressed `/file/<hash>` paths. The manifest retains SHA-256
checksums from the original descriptor; snapshot names alone are not the byte
check. Redirects and other snapshot endpoints are refused.

Source scanning preserves public upstream cryptographic test/demo fixtures,
signing fingerprints, documentation and code identifiers. Exceptions require
whole-archive SHA-256 plus complete nested member/rule/start/end coordinates.
The apt 2.6.1 public test key is byte-identical to the previously reviewed apt 2.6.2
fixture; only its actual Core archive coordinates are allowed. Runtime files and
changed archives are never exempted. Scanning is bounded to three archive levels;
deliberately malformed compression fixtures cannot all be recursively decoded.
Their unchanged bytes remain bound to reviewed archive hashes.
