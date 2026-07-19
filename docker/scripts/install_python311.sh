#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Debian Bookworm ships Python 3.11, the ABI used by the Frigate 0.17 base
# image. Do not replace this with a newer interpreter: ncnn's extension wheel
# is copied into that image.
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  python3 \
  python3-dev \
  python3-pip \
  python3-setuptools \
  python3-wheel

python3 --version
python3 - <<'PY'
import sys
assert sys.version_info[:2] == (3, 11), sys.version
PY

# Existing build scripts use the explicit interpreter name. Bookworm's
# package does not provide a python3.11 executable symlink in every minimal
# image, so expose it consistently.
ln -sf "$(command -v python3)" /usr/local/bin/python3.11
rm -rf /var/lib/apt/lists/*
