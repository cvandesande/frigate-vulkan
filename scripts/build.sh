#!/usr/bin/env bash
set -euo pipefail

PROFILE_FILE="${1:-profiles/gfx803-vulkan.env}"
if [[ ! -f "$PROFILE_FILE" ]]; then
  echo "Profile file not found: $PROFILE_FILE" >&2
  exit 1
fi

cp "$PROFILE_FILE" .env
echo "Using profile from $PROFILE_FILE"
docker compose build vulkan-smoke frigate-vulkan
