# rocm-legacy

Vulkan/ncnn Frigate builds for legacy AMD GPUs. Despite the repository name,
the active implementation is Vulkan-only: it uses Mesa RADV and does not
install ROCm or expose `/dev/kfd`. The images are based on Debian Bookworm;
the former ROCm implementation remains in Git history.

## What you get

- a pinned ncnn Python wheel built with `NCNN_VULKAN=ON`
- a `vulkan-smoke` image to probe RADV, benchmark YOLOv9-t, and compare CPU and
  GPU output
- a `frigate-vulkan` image with a dynamically discovered `ncnn` detector
  plugin
- a pinned Frigate `0.17.2` base image and an experimental Polaris profile

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

## Profile matrix

| Profile | ncnn | Frigate | Driver | Status |
|---|---|---|---|---|
| `gfx803-vulkan` | `20260526` | `0.17.2` | Mesa RADV | experimental |

## Build

Copy the profile to `.env`:

```bash
cp profiles/gfx803-vulkan.env .env
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

CI builds both images and verifies the plugin registers. It cannot test host
GPU access, RADV enumeration, parity, or live-camera detections.

## License

MIT
