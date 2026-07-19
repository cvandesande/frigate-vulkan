# How it works

This repository builds a Frigate detector path using ncnn's Vulkan backend.
Its builder and smoke images use Debian Bookworm, matching Frigate 0.17's
Bookworm base and Python 3.11 ABI. It relies on the distro Mesa RADV driver
rather than ROCm, so containers need only `/dev/dri` and the host's video/render
group IDs.

The multi-stage Dockerfile first builds a Python 3.11 ncnn wheel with
`NCNN_VULKAN=ON`, then layers that wheel into two images:

1. `vulkan-smoke` probes Vulkan, benchmarks a YOLOv9-t ncnn export, and checks
   CPU/GPU output parity.
2. `frigate-vulkan` installs a dynamically discovered Frigate `ncnn` detector
   plugin that feeds ncnn output through Frigate's YOLO post-processor.

The active profile pins the Frigate base image and ncnn release. Model export
is deliberately separate, so image builds do not download model weights.
