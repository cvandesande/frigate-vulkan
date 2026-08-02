#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <ncnn_tag>" >&2
  exit 2
fi

NCNN_TAG="$1"
export CMAKE_ARGS="-DNCNN_VULKAN=ON -DNCNN_BUILD_EXAMPLES=OFF -DNCNN_BUILD_TOOLS=OFF"

# --ignore-installed is required on Trixie: its pip refuses to uninstall the
# dpkg-provided wheel/setuptools ("no RECORD file was found"), which aborts the
# whole build. Installing alongside rather than over the distro copies keeps
# this working on both Bookworm and Trixie.
python3 -m pip install --break-system-packages --upgrade --ignore-installed pip setuptools wheel
mkdir -p /src /wheels
git clone --depth 1 --branch "${NCNN_TAG}" --recursive https://github.com/Tencent/ncnn /src/ncnn
cd /src/ncnn

# Current dated releases retain setup.py. Keep the pip-wheel fallback for a
# future pinned tag that changes the Python packaging entry point.
if [[ -f setup.py ]]; then
  python3 setup.py bdist_wheel --dist-dir /wheels
else
  python3 -m pip wheel --break-system-packages . --wheel-dir /wheels
fi

test -n "$(find /wheels -maxdepth 1 -name 'ncnn-*.whl' -print -quit)"
