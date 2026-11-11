#!/usr/bin/env bash
set -euo pipefail
: "${GITHUB_REPOSITORY:?}"
: "${GITHUB_SHA:?}"
: "${GH_TOKEN:?}"
: "${QUALIFIED_IMAGE_REF:?}"
current=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)
if [[ "$current" != "$GITHUB_SHA" ]]; then
  echo 'Superseded qualified build retained; TEST channel unchanged'
  exit 0
fi
image=$(docker image inspect "$QUALIFIED_IMAGE_REF" --format '{{.Id}}')
test "$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$GITHUB_SHA"
export DOCKER_CONFIG=$(mktemp -d "$RUNNER_TEMP/storage-channel-login.XXXXXX")
channel="ghcr.io/${GITHUB_REPOSITORY,,}/storage:test"
trap 'docker image rm "$channel" >/dev/null 2>&1 || true; rm -rf -- "$DOCKER_CONFIG"' EXIT
printf '%s' "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
docker tag "$image" "$channel"
docker push "$channel"
docker pull "$channel"
test "$(docker image inspect "$channel" --format '{{.Id}}')" = "$image"
test "$(docker image inspect "$channel" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$GITHUB_SHA"
echo "$channel tracks qualified main $GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
