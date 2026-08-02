# frigate-vulkan

Run Frigate's object detector on an AMD GPU through Vulkan, using ncnn and Mesa
RADV. No ROCm, no `/dev/kfd` — which is the point: it works on cards ROCm has
dropped. Previously named `rocm-legacy`; the ROCm implementation remains in Git
history.

## What you get

- a pinned ncnn Python wheel built with `NCNN_VULKAN=ON`
- a `vulkan-smoke` image to probe RADV, benchmark, and check CPU/GPU parity
- a `frigate-vulkan` image with a dynamically discovered `ncnn` detector plugin
- one GPU-neutral image serving every card, published as
  `cvandesande/frigate-vulkan`
- scripts to convert Frigate+ ONNX models to ncnn and to benchmark them honestly

Nothing in the image build depends on the GPU: the `frigate-vulkan` target takes
only `FRIGATE_IMAGE` and `NCNN_TAG`, and ncnn compiles its Vulkan shaders at
runtime. Per-card settings are runtime only — `RENDER_GID` and `RADV_PERFTEST`.

## Measured gfx803 results

Hardware testing on an RX 560 (RADV Polaris, Mesa 22.3.6) confirmed fp32
CPU/GPU parity for the ncnn Vulkan backend. The following Frigate+ YOLOv9 base
models were converted from ONNX with pnnx and measured on the GPU:

| Model | Resolution | Mean ms | Raw FPS |
|---|---:|---:|---:|
| YOLOv9-t | 320 | 14.371 | 69.6 |
| YOLOv9-s | 320 | 22.278 | 44.9 |
| **YOLOv9-t** | **640** | **35.289** | **28.3** |
| YOLOv9-s | 640 | 63.571 | 15.7 |

**Recommendation:** start with YOLOv9-t 640 for the best measured
resolution/latency balance on gfx803. Use t-320 when detection cadence or
camera count matters more than small-object resolution. This is a performance
recommendation; validate detection accuracy against real camera footage before
making it permanent. YOLO-NAS is not yet supported by this ncnn path because
its ONNX graph cannot be faithfully converted by pnnx.

> **Treat these numbers with caution.** They come from 50-iteration runs on
> default power management. On Vega 20 that regime proved unreliable — the SMU
> drops the clock under inference while reporting 90–99% busy, so a short run
> measures wherever it happened to sit. Use `scripts/bench_steady.py` with the
> performance level pinned for numbers that compare across cards; the gfx906
> table in [`docs/vulkan-notes.md`](docs/vulkan-notes.md) was measured that way.

## Profile matrix

Profiles carry runtime settings only; every profile builds the same image.

| Profile | GPU | `RENDER_GID` | `RADV_PERFTEST` | Status |
|---|---|---|---|---|
| `gfx803-vulkan` | RX 560, Polaris 11 | 109 (Bookworm) | no-op on Polaris | experimental |
| `gfx906-vulkan` | Radeon VII, Vega 20 | 992 (Trixie) | `transfer_queue`, required | experimental |

Vega 20 needs `RADV_PERFTEST=transfer_queue`: without it RADV maps ncnn's
transfer queue to the graphics family, and `load_model()`'s weight upload
page-faults the gfx ring when VAAPI contexts are created concurrently, resetting
the GPU. RADV exposes no SDMA transfer queue on Polaris, so the flag does nothing
there. See [`docs/vulkan-notes.md`](docs/vulkan-notes.md).

## Publish the image

```bash
scripts/release_image.sh
```

Builds and pushes a dated immutable tag plus a moving `:latest`. Deployments
should reference the dated tag so rollback is a one-line edit.

## Build

Copy the profile to `.env`:

```bash
cp profiles/gfx906-vulkan.env .env   # or gfx803-vulkan.env
```

Then build both images:

```bash
docker compose build vulkan-smoke frigate-vulkan
```

Or use the helper:

```bash
scripts/build.sh
```

## Export a free YOLOv9 model

Create the YOLOv9-t ncnn model files before running the smoke test or Frigate.
The exporter image is pinned and the script prints checksums for all artifacts.

```bash
scripts/export_ncnn_model.sh
```

This writes `models/yolov9t-640.ncnn.param`, `models/yolov9t-640.ncnn.bin`,
`models/yolov9t-640-labelmap.txt`, and an ONNX reference model.

The input resolution defaults to 640. Override it with `IMGSZ`, which must be a
multiple of 32:

```bash
IMGSZ=320 scripts/export_ncnn_model.sh
```

Artifacts are named after the size (`yolov9t-320.*`, `yolov9t-640.*`), so
exports at different resolutions coexist in `models/`. Whichever you pick, the
`width` and `height` in the Frigate `model:` block below must match it.

For prerequisites, validation, model files, labels, and adapting the exporter
to another public YOLOv9 variant, see the
[free YOLOv9 model guide](docs/free-yolov9-model-guide.md). Do not assume that
an arbitrary ONNX model is compatible with this raw-YOLO ncnn detector.

## Validate on a GPU host

Set `VIDEO_GID` and `RENDER_GID` in `.env` to the host group IDs, then run:

```bash
docker compose run --rm vulkan-smoke
```

The smoke target needs only `/dev/dri`. It requires GPU enumeration and, with
`NCNN_FP16=0`, fails if maximum CPU/GPU output difference is `>= 1e-2`.
Record the GPU, Mesa version, checksums, and benchmark results in
[`docs/vulkan-notes.md`](docs/vulkan-notes.md).

## Run Frigate

Configure Frigate to use the ncnn detector and the exported model:

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

### Labelmap format

`scripts/export_ncnn_model.sh` writes `models/yolov9t-640-labelmap.txt` for
you. Copy it alongside the model. If you write one by hand, the format is
**space-delimited**, one class per line:

```text
0 person
1 bicycle
2 car
```

Frigate chooses how to parse the file by testing whether the first
space-delimited token of the first line is a digit. `0 person` parses as index
`0` -> `person`. A colon-delimited file (`0:person`) does **not**: the parser
falls through to its unindexed branch and uses the entire line as the label
name, so class 0 is called `0:person` and never matches `person`.

This fails silently. There is no parse error — detection runs, motion still
works, and the only symptom is a startup warning:

```text
WARNING : front is configured to track ['person', ...] objects,
          which are not supported by the current model.
```

If you see that warning naming ordinary classes like `person`, check the
labelmap delimiter before suspecting the model. The line count must also equal
the model's class count.

Then start it:

```bash
docker compose up frigate-vulkan
```

Confirm that Frigate logs `vulkan=True`, receives live detections without
native crashes, and record sustained inference metrics in
`docs/vulkan-notes.md`. The standalone smoke gate has passed, but Frigate
integration remains experimental until that live check is complete.

## CI scope

CI validates every profile, asserts they resolve to identical image names,
builds both images, and verifies the plugin registers. It cannot test host
GPU access, RADV enumeration, parity, or live-camera detections.

## License

MIT
