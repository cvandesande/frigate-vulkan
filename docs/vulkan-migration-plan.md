# Vulkan implementation status

Status: implemented in the working tree; awaiting hardware validation.

The original migration plan selected ncnn with Mesa RADV for legacy AMD GPUs.
The repository has since adopted that design as its sole active path rather
than maintaining parallel ROCm targets. The removed ROCm implementation is
available in Git history.

## Delivered

- ncnn `20260526` is built from source with `NCNN_VULKAN=ON` in the
  `ncnn-builder` Docker stage.
- `vulkan-smoke` includes RADV, Vulkan tools, the ncnn wheel, and a benchmark
  and CPU/GPU parity test.
- `frigate-vulkan` includes the ncnn wheel and dynamically discovered Frigate
  `ncnn` detector plugin.
- The plugin parses ncnn `.param` graph blob names instead of assuming pnnx
  names, uses a fresh extractor per detection, and hands raw YOLO output to
  Frigate's `post_process_yolo` helper.
- The `gfx803-vulkan` profile, Compose services, build helper, model exporter,
  CI build checks, support matrix, and runtime instructions are Vulkan-only.

## Required operator validation

1. Run `scripts/export_ncnn_model.sh` to create the 320×320 YOLOv9-t ncnn and
   ONNX artifacts, recording the printed checksums.
2. On the gfx803 host, run `docker compose run --rm vulkan-smoke`. Confirm that
   RADV enumerates the GPU and that fp32 parity is below `1e-2`; record mean
   and median milliseconds in `docs/vulkan-notes.md`.
3. Run `frigate-vulkan` with a real camera or clip. Confirm `vulkan=True` in
   its logs and record Frigate's inference metric in the same notes file.

CI proves only that the images and plugin registration build; it cannot satisfy
these hardware-dependent gates.
