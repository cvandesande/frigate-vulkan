# Vulkan validation notes

The `gfx803-vulkan` profile builds an ncnn wheel with Vulkan enabled and uses
Mesa RADV at runtime. Standalone smoke validation has passed on the target
host; live Frigate detector inference remains under validation.

## Hardware validation

### 2026-07-19 — gfx803 Vulkan smoke test: fp32 parity passed

- GPU: AMD Radeon RX 560 Series (RADV POLARIS11), PCI `0000:08:00.0`
- Runtime: Mesa RADV `22.3.6`, Vulkan API `1.3.230`
- Smoke image: `registry.gitlab.com/cvandesande/dockers/frigate-rocm-legacy@sha256:9011b0fa6dd5e71b5655325c4584aa479f2a242242923c1f3a8fcbad79885b75`
- Model checksums: param `663dbd68837d88fb84cf9061fdeb506f51ec1ed00324d2971011a7761ff6919a`, bin `d6f06e8019a08cfb08e6e792e9962c96c94e48372b64a48ba6981cea01402c39`
- First run: mean `12.371 ms`, median `12.335 ms`, maximum error `1.2725677`
  (**failed**). ncnn's Vulkan defaults had not explicitly disabled fp16 paths.
- Corrected run (all three ncnn fp16 options explicitly `False`): mean
  `13.113 ms`, median `13.117 ms`, maximum error `0.00068664551` (**passed**;
  required `< 1e-2`). CPU and GPU repeated outputs each had zero difference.
- Worst CPU/GPU value: output `(2, 2069)`, GPU `190.33032`, CPU `190.32964`.

The temporary Pod was removed and the existing ROCm Frigate StatefulSet was
restored after each test. The smoke gate is now satisfied; Frigate integration
remains a separate pending check.

### 2026-07-19 — Frigate+ YOLOv9 base-model comparison on gfx803

All models below were downloaded from the existing Frigate+ cache, converted
from ONNX with pnnx `fp16=0`, and benchmarked on the RX 560/RADV host with
ncnn fp16 paths explicitly disabled. Every CPU/GPU parity result passed the
`< 1e-2` gate.

| Model | Resolution | Mean ms | Median ms | Max CPU/GPU error | Approx. raw FPS |
|---|---:|---:|---:|---:|---:|
| YOLOv9-t | 320 | 14.371 | 14.229 | 0.0020752 | 69.6 |
| YOLOv9-s | 320 | 22.278 | 22.261 | 0.0008850 | 44.9 |
| YOLOv9-t | 640 | 35.289 | 35.235 | 0.0023193 | 28.3 |
| YOLOv9-s | 640 | 63.571 | 63.546 | 0.0065918 | 15.7 |

For this GPU, YOLOv9-t 640 is the recommended quality/latency starting point:
it remains below 36 ms while providing higher input resolution than the 320
models. Use YOLOv9-t 320 when camera count or detection cadence needs maximum
headroom. YOLOv9-s 640 is not recommended without a camera-accuracy benefit
that justifies its 64 ms cost.

Frigate+ YOLO-NAS 320/640 base models were not benchmarked as ncnn candidates:
pnnx reported unsupported `TopK`, `Gather`, and `NonMaxSuppression` operations
when converting their ONNX graphs. They also use uint8 input and require a
YOLO-NAS-specific post-processing implementation, unlike the current
raw-YOLO ncnn plugin.

## Frigate integration

### 2026-07-19 — initial live inference segfault and corrective deployment

The initial Vulkan Frigate image loaded the latest Frigate+ YOLOv9-t 640 model
(`ea3c8aba575339b962315e9e24102e09`) with `vulkan=True`, but its first live
detector inference segfaulted in `NcnnDetector.detect_raw` at
`extractor.extract`. The same model and GPU passed standalone ncnn smoke
testing, which ruled out basic model conversion and RADV availability.

The plugin was corrected to retain the contiguous NumPy input array for the
lifetime of `ncnn.Mat` and `extractor.extract`; the previous code passed an
inline temporary array to native ncnn. The corrected image is now the running
Frigate StatefulSet image, and at 2026-07-19 22:09 local time it successfully
loaded `/config/model_cache/yolov9t-640-20260715.ncnn.param` with
`vulkan=True`. No post-fix live inference appears in the recorded logs yet, so
this is **not** evidence that the segfault is resolved.

Next: trigger/observe real detections, confirm the detector worker remains
stable, and record its inference metric. Do not promote this profile beyond
experimental until that check is complete.

Do not claim the profile is hardware-validated until both entries include the
host GPU, Mesa version, model checksum, and measurements.
