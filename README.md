# frigate-vulkan

Frigate object detection on the GPU via **Vulkan**, not ROCm.

ROCm is a moving target: each release drops older cards, and a GPU that works
today can be unsupported after an upgrade. Vulkan does not work that way. ncnn's
Vulkan backend is vendor-neutral and targets any device with a conformant
driver, so hardware keeps working long after the vendor compute stack has moved
on. That is the whole point of this project: a detector that does not care which
GPU you have, or how old it is.

No ROCm, no `/dev/kfd`, no HSA. Just Mesa RADV (or any other Vulkan driver) and
`/dev/dri`.

> Previously named `rocm-legacy`; the ROCm implementation remains in Git history.

## What you get

- **Broad GPU support.** ncnn's Vulkan backend is vendor-neutral, so this is not
  AMD-specific by design. Validated here on Polaris 11 (RX 560) and Vega 20
  (Radeon VII) under Mesa RADV — both outside current ROCm support.
- **One GPU-neutral image** for every card, published as
  `cvandesande/frigate-vulkan`. Nothing in the build depends on the GPU; ncnn
  compiles its Vulkan shaders at runtime. Per-card settings are runtime only.
- **A Frigate `ncnn` detector plugin**, discovered dynamically by Frigate.
- **A parity gate.** The `vulkan-smoke` image enumerates devices, benchmarks,
  and fails if GPU output diverges from CPU by `>= 1e-2` with fp16 disabled.
- **Model tooling.** Convert Frigate+ ONNX models to ncnn, or export a free
  YOLOv9 checkpoint, with checksums for every artifact.

## Quick start

```bash
cp profiles/gfx906-vulkan.env .env      # or gfx803-vulkan.env
scripts/export_ncnn_model.sh            # IMGSZ=320 for the smaller input
docker compose run --rm vulkan-smoke    # parity + benchmark gate
docker compose up frigate-vulkan
```

Set `VIDEO_GID` and `RENDER_GID` in `.env` to the host's group IDs first — the
render node group differs across distributions (109 on Bookworm, 992 on Trixie).

To convert a Frigate+ model you already own, use
`scripts/convert_plus_onnx.sh <model_cache_dir> <model_id>`. For the free-model
path and how to adapt it to other YOLOv9 variants, see the
[free YOLOv9 model guide](docs/free-yolov9-model-guide.md). Do not assume an
arbitrary ONNX model works with this raw-YOLO detector.

Publish the shared image with `scripts/release_image.sh`, which pushes a dated
immutable tag plus a moving `latest`. Deployments should reference the dated tag.

## Frigate configuration

```yaml
detectors:
  ncnn:
    type: ncnn
model:
  path: /config/model_cache/yolov9t-640.ncnn.param
  model_type: yolo-generic
  width: 640
  height: 640
  input_tensor: nchw
  input_dtype: float
  input_pixel_format: rgb
  labelmap_path: /config/model_cache/yolov9t-640-labelmap.txt
```

`width`/`height` must match the exported model's input size.

**Labelmap format matters and fails silently.** Frigate picks its parser by
testing whether the first space-delimited token of line 1 is a digit, so it must
be `0 person`, not `0:person`. Get it wrong and every class is misnamed with no
parse error — the only symptom is a startup warning that your tracked objects
"are not supported by the current model". The scripts here emit the right format.

## Profiles

Profiles carry runtime settings only; every profile builds the same image.

| Profile | GPU | `RENDER_GID` | `RADV_PERFTEST` | Status |
|---|---|---|---|---|
| `gfx803-vulkan` | RX 560, Polaris 11 | 109 (Bookworm) | inert on Polaris | experimental |
| `gfx906-vulkan` | Radeon VII, Vega 20 | 992 (Trixie) | `transfer_queue`, required | experimental |

Vega 20 needs `RADV_PERFTEST=transfer_queue`. Without it RADV maps ncnn's
transfer queue to the graphics family, and `load_model()`'s weight upload
page-faults the gfx ring when VAAPI contexts are created concurrently — which
resets the GPU. RADV exposes no SDMA transfer queue on Polaris, so the flag does
nothing there.

## Benchmarks

Measured figures, methodology and hardware validation live in
[`docs/vulkan-notes.md`](docs/vulkan-notes.md).

One caveat worth carrying: benchmark with the GPU's performance level pinned.
On Vega 20 the SMU drops the clock under inference while still reporting 90–99%
busy, so an unpinned short run measures wherever it happened to sit — a ~2x
swing. `scripts/bench_steady.py` reports steady state separately from the fast
head so the effect is visible rather than averaged away.

## CI scope

CI validates every profile, asserts they resolve to identical image names,
builds both images, and verifies the plugin registers. It cannot test host GPU
access, driver enumeration, parity, or live-camera detection.

## License

MIT
