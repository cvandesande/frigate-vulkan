#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Installs the base image's own Python. Two different consumers need different
# things from this:
#
#   * The wheel that gets copied into the Frigate image MUST be built against
#     Python 3.11, the ABI Frigate 0.17's Bookworm base ships. REQUIRE_PY311=1
#     (the default) asserts that, so a base image change can never silently
#     produce a wheel Frigate cannot import.
#   * The standalone smoke/soak image has no such constraint -- it only has to
#     be self-consistent -- so it can run on Trixie's Python 3.13 with
#     REQUIRE_PY311=0. That is what lets the smoke image move to a newer Mesa
#     independently of Frigate's base.
REQUIRE_PY311="${REQUIRE_PY311:-1}"

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  python3 \
  python3-dev \
  python3-pip \
  python3-setuptools \
  python3-wheel

python3 --version
if [[ "$REQUIRE_PY311" == "1" ]]; then
  python3 - <<'PY'
import sys
assert sys.version_info[:2] == (3, 11), sys.version
PY
fi

rm -rf /var/lib/apt/lists/*
