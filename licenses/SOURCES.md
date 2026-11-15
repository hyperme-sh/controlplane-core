# Core source and replacement preparation

The Linux amd64 CPython 3.13 storage image includes certifi 2026.10.23 (MPL-2.0),
jwcrypto 1.6.1 (LGPL-3.0), orjson 3.13.0 (Apache-2.0 OR MIT with MPL-2.0 bundled
components), and psycopg/psycopg-binary 3.3.6 (LGPL-3.0). Existing legal texts and
notices remain unchanged. The separate base review adds CPython sources; Debian archives remain a follow-up.

`sources.json` records 17 exact upstream source/build materials, package versions,
URLs, sizes and SHA-256 hashes. They are bundled at
`/usr/share/doc/controlplane-core/sources`. CI verifies the archive bytes, installed
package versions, native member inventory/hashes and locked wheel SBOM before and
after image construction. A new declared copyleft dependency requires review.

The psycopg-binary 3.3.6 wheel matches the reviewed API wheel, including its bundled
libcrypt. The copied manylinux manifest/config binds the original libcrypt layer
to libxcrypt 4.5.2's source hash. CI compares the original and installed .text and
.rodata hashes. See controlplane-api PR #42 for the upstream build trace. This
uses the matching 3.3.6 records, not the different 3.3.7 wheel. Sources for libpq,
OpenSSL, OpenLDAP and the mapped system libraries include upstream archives and
source RPMs with their distribution patches. All recorded native mappings have
sources; a bit-identical rebuild is not claimed.

Recipient replacement is checked in CI with networking disabled and read-only
bind mounts. Modified certifi, jwcrypto and psycopg Python packages must import;
modified orjson/psycopg native extensions and libpq must load and answer their
basic serialization/version calls. These are loading contracts using synthetic
markers and ABI-preserving changes, not complete recompilation or service tests.
Recipients must maintain the platform ABI and compatible interfaces when replacing
components; the image does not enforce signatures on those mounted replacements.

Source scans preserve public upstream test vectors and example keys. Exceptions
require the exact archive hash, member hash, scanner rule and line coordinate;
changed sources and files outside those archives are refused. The scanner's
three-level archive-depth limit applies; source RPM payloads are not claimed to
have been recursively inspected. Original archives remain byte-identical.

Retained hosted content, older-image review, runtime every-layer scanning,
base-image source/legal review and public distribution clearance remain separate
under Infra #993. TEST continues following the moving GHCR storage:test channel.
No visibility changes, history rewrites or manual release operations occur here.

[Base review](BASE.md) records the existing Python image, Debian package/source
versions and legal-file checks separately from wheel-native provenance.
