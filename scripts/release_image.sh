#!/usr/bin/env bash
set -euo pipefail

# Build and publish the GPU-neutral Frigate image used by every deployment.
#
# Nothing in the image build depends on the card: the frigate-vulkan target
# takes only FRIGATE_IMAGE and NCNN_TAG, both identical across profiles, and
# ncnn compiles its Vulkan shaders at runtime after enumerating the device.
# Per-GPU settings are runtime only -- group_add/supplementalGroups for the
# render node, and RADV_PERFTEST. So one build serves gfx803 and gfx906 alike;
# the profile below is only supplying build args that happen to be common.
#
# Publishes two tags: a dated immutable one to deploy and roll back by, and a
# moving one for convenience. Deployments should reference the dated tag.
#
# Usage:
#   scripts/release_image.sh              # build, tag with today's date, push
#   IMAGE_DATE=20260802 scripts/release_image.sh --no-build   # retag/push existing

REPO="${FRIGATE_VULKAN_REPO:-docker.io/cvandesande/frigate-rocm-legacy}"
DATE="${IMAGE_DATE:-$(date +%Y%m%d)}"
PROFILE_FILE="${PROFILE_FILE:-profiles/gfx803-vulkan.env}"
DATED="$REPO:frigate-vulkan-$DATE"
MOVING="$REPO:frigate-vulkan"

cd "$(dirname "$0")/.."

if [[ "${1:-}" != "--no-build" ]]; then
  echo "Building $DATED from $PROFILE_FILE"
  FRIGATE_VULKAN_IMAGE="$DATED" \
    docker compose --env-file "$PROFILE_FILE" build frigate-vulkan
else
  echo "Skipping build; expecting $DATED to exist locally"
  docker image inspect "$DATED" >/dev/null
fi

docker tag "$DATED" "$MOVING"

# Fail early and legibly rather than mid-push if the daemon has no credentials.
if ! docker push "$DATED"; then
  echo "push failed -- run 'docker login ${REPO%%/*}' first" >&2
  exit 1
fi
docker push "$MOVING"

echo
echo "Published:"
docker image inspect "$DATED" --format '  {{index .RepoDigests 0}}'
cat <<EOF

Point deployments at the dated tag:
  k8s   ~/dockers/kubernetes/tirnanog/frigate.yaml -> image: $DATED
  noyan ~/docker-compose.yml                       -> image: $DATED
EOF
