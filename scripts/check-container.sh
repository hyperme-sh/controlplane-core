#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RELEASE_ARTIFACT_DIR:?Use the exact qualified package output}"
: "${QUALIFIED_IMAGE_REF:?Choose a job-owned local image name}"
: "${GITHUB_SHA:?Record the source revision}"
: "${STORAGE_TEST_DATABASE_URL:?Use only the CI job database}"
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]]
scratch=$(mktemp -d)
container=
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null; fi
  rm -rf -- "$scratch"
}
trap cleanup EXIT
python3 scripts/prepare-runtime-context.py "$RELEASE_ARTIFACT_DIR" "$scratch/context"
wheel=$(sha256sum "$scratch"/context/*.whl | cut -d ' ' -f 1)
docker build --build-arg "GIT_SHA=$GITHUB_SHA" --build-arg "WHEEL_SHA256=$wheel" -t "$QUALIFIED_IMAGE_REF" "$scratch/context"
image=$(docker image inspect "$QUALIFIED_IMAGE_REF" --format '{{.Id}}')
test "$(docker image inspect "$image" --format '{{.Config.User}}')" = '65532:65532'
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges "$image" python -I /opt/storage-schema/migrate.py --help >/dev/null
container=$(docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid --add-host host.docker.internal:host-gateway -e "DATABASE_URL=$STORAGE_TEST_DATABASE_URL" "$image")
for attempt in $(seq 1 60); do
  if docker exec "$container" python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)' >/dev/null 2>&1; then break; fi
  if [[ "$attempt" = 60 ]]; then docker logs "$container"; exit 1; fi
  sleep 1
done
docker exec "$container" python -c '
import urllib.error, urllib.request
for path, expected in [("/api/v1/storage/objects/" + "a" * 64, 401), ("/api/v1/storage/tenant", 401), ("/api/v1/users", 404)]:
    try:
        urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=2)
    except urllib.error.HTTPError as error:
        assert error.code == expected, error.code
    else:
        raise AssertionError("Unexpected anonymous route access")
'
cp "$scratch/context/inventory.json" "$RELEASE_ARTIFACT_DIR/runtime-context.json"
printf '%s\n' "$image" > "$RELEASE_ARTIFACT_DIR/runtime-image-id.txt"
