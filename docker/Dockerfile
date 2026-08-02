# syntax=docker/dockerfile:1.7
ARG FRIGATE_IMAGE=ghcr.io/blakeblackshear/frigate:0.17.2
ARG NCNN_TAG=20260526
# Base for the standalone smoke/soak image only. Frigate's own base is fixed by
# FRIGATE_IMAGE, so this is the one knob that lets the RADV userspace under test
# move without touching the detector image: debian:bookworm gives Mesa 22.3.6,
# debian:trixie gives Mesa 25.0.7.
ARG SMOKE_BASE=debian:bookworm

# Wheel for the Frigate image. Pinned to Bookworm because it has to match the
# Python 3.11 ABI of the Frigate base; REQUIRE_PY311 defaults to 1 and asserts it.
FROM debian:bookworm AS ncnn-builder
ARG NCNN_TAG
COPY docker/scripts/install_python.sh /usr/local/bin/install_python.sh
COPY docker/scripts/build_ncnn_wheel.sh /usr/local/bin/build_ncnn_wheel.sh
RUN chmod +x /usr/local/bin/install_python.sh /usr/local/bin/build_ncnn_wheel.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake git libvulkan-dev ninja-build pkg-config \
    && /usr/local/bin/install_python.sh \
    && /usr/local/bin/build_ncnn_wheel.sh "${NCNN_TAG}"

# Separate wheel for the smoke image, built against whatever Python SMOKE_BASE
# ships. Kept distinct from ncnn-builder so moving the smoke base cannot change
# the wheel Frigate receives.
FROM ${SMOKE_BASE} AS ncnn-builder-smoke
ARG NCNN_TAG
COPY docker/scripts/install_python.sh /usr/local/bin/install_python.sh
COPY docker/scripts/build_ncnn_wheel.sh /usr/local/bin/build_ncnn_wheel.sh
RUN chmod +x /usr/local/bin/install_python.sh /usr/local/bin/build_ncnn_wheel.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake git libvulkan-dev ninja-build pkg-config \
    && REQUIRE_PY311=0 /usr/local/bin/install_python.sh \
    && /usr/local/bin/build_ncnn_wheel.sh "${NCNN_TAG}"

FROM ${SMOKE_BASE} AS vulkan-smoke
COPY docker/scripts/install_python.sh /usr/local/bin/install_python.sh
COPY scripts/validate_vulkan.sh /usr/local/bin/validate_vulkan.sh
RUN chmod +x /usr/local/bin/install_python.sh /usr/local/bin/validate_vulkan.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libvulkan1 mesa-vulkan-drivers vulkan-tools \
    && REQUIRE_PY311=0 /usr/local/bin/install_python.sh
COPY --from=ncnn-builder-smoke /wheels/*.whl /tmp/
RUN python3 -m pip install --no-cache-dir --break-system-packages /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && python3 -c "import ncnn; print('ncnn Vulkan GPU API:', ncnn.get_gpu_count())"
CMD ["/usr/local/bin/validate_vulkan.sh"]

FROM ${FRIGATE_IMAGE} AS frigate-vulkan
USER root
# Frigate 0.17.2's base is Bookworm, whose mesa-vulkan-drivers is 22.3.6 (Dec
# 2022). Under that RADV, sustained ncnn compute on Vega 20 timed out the gfx
# ring and forced a BACO reset of the card (VRAM lost, VkDevice permanently
# DEVICE_LOST, every later vkQueueSubmit returning -4).
#
# bookworm-backports carries 25.0.7-2~bpo12+1 -- the same Mesa the Trixie host
# runs -- built against Bookworm's glibc 2.36, so it drops in without rebasing
# Frigate. Trixie's own package cannot be used here: it needs glibc 2.41.
#
# Backports is priority 100, below the release's 500, so -t is required or apt
# silently keeps 22.3.6. libllvm19 comes from backports too, as Mesa 25 needs it.
RUN echo "deb http://deb.debian.org/debian bookworm-backports main" \
        > /etc/apt/sources.list.d/backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libvulkan1 \
    && apt-get install -y --no-install-recommends -t bookworm-backports mesa-vulkan-drivers \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ncnn-builder /wheels/*.whl /tmp/
RUN python3 -m pip install --no-cache-dir --break-system-packages /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && python3 -c "import ncnn; assert hasattr(ncnn, 'get_gpu_count'); print('ncnn Vulkan wheel OK')"
COPY docker/frigate/ncnn.py /opt/frigate/frigate/detectors/plugins/ncnn.py
RUN python3 -c "from frigate.detectors.detector_types import api_types; assert 'ncnn' in api_types; print('detectors:', sorted(api_types))"
