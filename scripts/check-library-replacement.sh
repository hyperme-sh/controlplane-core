#!/usr/bin/env bash
# CI-only loading contracts, without networking or a service/database process.
set -euo pipefail
image=${1:?Prepared image required}
scratch=$(mktemp -d "${RUNNER_TEMP:-/tmp}/core-replacement.XXXXXX")
container=
cleanup() { if [[ -n "$container" ]]; then docker rm "$container" >/dev/null; fi; rm -rf -- "$scratch"; }
trap cleanup EXIT
container=$(docker create "$image")
site=/opt/storage/lib/python3.13/site-packages
mounts=()
for package in certifi jwcrypto psycopg orjson psycopg_binary psycopg_binary.libs; do
  docker cp "$container:$site/$package" "$scratch/$package"
  mounts+=(--mount "type=bind,src=$scratch/$package,dst=$site/$package,readonly")
done
for package in certifi jwcrypto psycopg; do
  printf '\nreplacement_contract_marker = True\n' >> "$scratch/$package/__init__.py"
done
python3 - "$scratch" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
# Added unused trailing bytes preserve ABI while demonstrating replaced bytes load.
for package, pattern in [('orjson', '*.so'), ('psycopg_binary', '*.so'), ('psycopg_binary.libs', 'libpq-*.so.*')]:
    libraries = list((root / package).glob(pattern))
    if not libraries:
        raise ValueError('Missing replacement library: ' + package)
    for library in libraries:
        with library.open('ab') as stream:
            stream.write(b'\0recipient-replacement-contract\0')
PY
chmod -R a+rX "$scratch"
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  "${mounts[@]}" --entrypoint /opt/storage/bin/python "$image" -I -c \
  'import certifi, jwcrypto, orjson, psycopg; assert all(p.replacement_contract_marker for p in (certifi,jwcrypto,psycopg)); assert orjson.loads(orjson.dumps({"modified": True})) == {"modified": True}; assert psycopg.pq.__impl__ == "binary"; assert psycopg.pq.version() >= 180000; print("Modified Python packages and ABI-compatible native extensions/libraries loaded")'
