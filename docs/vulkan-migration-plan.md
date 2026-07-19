# Vulkan implementation status

Status: implemented. Standalone gfx803/RADV validation has passed; the
corrected Frigate deployment is running, with live detector-inference
validation still pending.

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

1. For a new model, run `scripts/export_ncnn_model.sh` or convert the
   compatible Frigate+ YOLOv9 ONNX artifact with pnnx (`fp16=0`), then record
   its checksums.
2. On a new GPU/driver combination, run `docker compose run --rm vulkan-smoke`.
   Confirm RADV enumeration and fp32 parity below `1e-2`, and record mean and
   median milliseconds in `docs/vulkan-notes.md`. This gate has passed on the
   RX 560/RADV host for the benchmarked models.
3. Exercise Frigate with a real camera or clip. Confirm `vulkan=True`, live
   detections, and a stable detector worker; record Frigate's inference metric
   in the same notes file. This remains the outstanding gate for the current
   deployment.

CI proves only that the images and plugin registration build; it cannot satisfy
these hardware-dependent gates.
