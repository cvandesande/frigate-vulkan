# syntax=docker/dockerfile:1.7
ARG FRIGATE_IMAGE=ghcr.io/blakeblackshear/frigate:0.17.2
ARG NCNN_TAG=20260526

FROM debian:bookworm AS ncnn-builder
ARG NCNN_TAG
COPY docker/scripts/install_python311.sh /usr/local/bin/install_python311.sh
COPY docker/scripts/build_ncnn_wheel.sh /usr/local/bin/build_ncnn_wheel.sh
RUN chmod +x /usr/local/bin/install_python311.sh /usr/local/bin/build_ncnn_wheel.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake git libvulkan-dev ninja-build pkg-config \
    && /usr/local/bin/install_python311.sh \
    && /usr/local/bin/build_ncnn_wheel.sh "${NCNN_TAG}"

FROM debian:bookworm AS vulkan-smoke
COPY docker/scripts/install_python311.sh /usr/local/bin/install_python311.sh
COPY scripts/validate_vulkan.sh /usr/local/bin/validate_vulkan.sh
RUN chmod +x /usr/local/bin/install_python311.sh /usr/local/bin/validate_vulkan.sh \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libvulkan1 mesa-vulkan-drivers vulkan-tools \
    && /usr/local/bin/install_python311.sh
COPY --from=ncnn-builder /wheels/*.whl /tmp/
RUN python3.11 -m pip install --no-cache-dir --break-system-packages /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && python3.11 -c "import ncnn; print('ncnn Vulkan GPU API:', ncnn.get_gpu_count())"
CMD ["/usr/local/bin/validate_vulkan.sh"]

FROM ${FRIGATE_IMAGE} AS frigate-vulkan
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 mesa-vulkan-drivers libvulkan1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ncnn-builder /wheels/*.whl /tmp/
RUN python3 -m pip install --no-cache-dir --break-system-packages /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && python3 -c "import ncnn; assert hasattr(ncnn, 'get_gpu_count'); print('ncnn Vulkan wheel OK')"
COPY docker/frigate/ncnn.py /opt/frigate/frigate/detectors/plugins/ncnn.py
RUN python3 -c "from frigate.detectors.detector_types import api_types; assert 'ncnn' in api_types; print('detectors:', sorted(api_types))"
