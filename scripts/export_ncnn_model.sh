#!/usr/bin/env bash
set -euo pipefail

# Pin the image rather than relying on a mutable latest tag. Override only when
# deliberately testing a different exporter release.
ULTRALYTICS_IMAGE="${ULTRALYTICS_IMAGE:-ultralytics/ultralytics:8.4.99}"

# Model input resolution. 640 is the recommended default: it measured the best
# resolution/latency balance on gfx803 (35.3 ms, see the benchmark table in the
# README). Export 320 when detection cadence matters more than small-object
# accuracy, at roughly half the latency:
#
#   IMGSZ=320 scripts/export_ncnn_model.sh
#
# Artifacts are named after the size, so exports at different resolutions sit
# side by side in models/ without overwriting each other.
IMGSZ="${IMGSZ:-640}"
if [[ ! "$IMGSZ" =~ ^[0-9]+$ ]] || (( IMGSZ % 32 != 0 )); then
  echo "IMGSZ must be a positive multiple of 32 (got '$IMGSZ')" >&2
  exit 1
fi

MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

PREFIX="yolov9t-${IMGSZ}"

# The export runs as root inside the container, deliberately.
#
# The pinned image does not ship the ncnn and pnnx Python packages; Ultralytics
# installs them on demand into /usr/local/lib/python3.12/dist-packages. Running
# with --user makes that directory unwritable, both installs fail with
# "Permission denied (os error 13)", and the ncnn export then dies with
# "ModuleNotFoundError: No module named 'ncnn'" -- several screens below the
# actual cause. Artifacts are chowned back to the invoking user afterwards.
docker run --rm -v "$MODELS_DIR:/models" "$ULTRALYTICS_IMAGE" \
  bash -lc "cd /models && yolo export model=yolov9t.pt format=ncnn imgsz=${IMGSZ} && yolo export model=yolov9t.pt format=onnx imgsz=${IMGSZ}"

# Reclaim ownership of anything the root-run export just wrote.
docker run --rm -v "$MODELS_DIR:/models" "$ULTRALYTICS_IMAGE" \
  chown -R "$(id -u):$(id -g)" /models

NCNN_DIR="$MODELS_DIR/yolov9t_ncnn_model"
if [[ ! -r "$NCNN_DIR/model.ncnn.param" || ! -r "$NCNN_DIR/model.ncnn.bin" || ! -r "$MODELS_DIR/yolov9t.onnx" ]]; then
  echo "Ultralytics export did not produce the expected YOLOv9-t artifacts" >&2
  exit 1
fi

cp "$NCNN_DIR/model.ncnn.param" "$MODELS_DIR/${PREFIX}.ncnn.param"
cp "$NCNN_DIR/model.ncnn.bin" "$MODELS_DIR/${PREFIX}.ncnn.bin"
mv "$MODELS_DIR/yolov9t.onnx" "$MODELS_DIR/${PREFIX}.onnx"
[[ -r "$NCNN_DIR/metadata.yaml" ]] && cp "$NCNN_DIR/metadata.yaml" "$MODELS_DIR/${PREFIX}.ncnn.metadata.yaml"

# Derive the Frigate labelmap from the exporter's own metadata rather than
# transcribing class names by hand.
#
# Format matters and fails silently if wrong. Frigate decides how to parse the
# file by testing whether the first SPACE-delimited token of line 1 is a digit
# (frigate/util/builtin.py, load_labels). "0 person" parses as index 0 ->
# "person". "0:person" does not: the whole line becomes the label name, so
# every class is misnamed, no tracked object ever matches, and the only symptom
# is a startup warning that your tracked objects "are not supported by the
# current model". Emit "<index> <name>", one class per line.
#
# Run inside the exporter image so this does not depend on host Python or PyYAML.
if [[ -r "$NCNN_DIR/metadata.yaml" ]]; then
  docker run --rm --user "$(id -u):$(id -g)" -v "$MODELS_DIR:/models" \
    -e PREFIX="$PREFIX" "$ULTRALYTICS_IMAGE" \
    python3 -c '
import os, yaml
prefix = os.environ["PREFIX"]
names = yaml.safe_load(open("/models/yolov9t_ncnn_model/metadata.yaml"))["names"]
with open(f"/models/{prefix}-labelmap.txt", "w") as f:
    for idx in sorted(names, key=int):
        f.write(f"{int(idx)} {str(names[idx]).strip()}\n")
print(f"wrote labelmap with {len(names)} classes")
'
else
  echo "metadata.yaml missing; cannot generate labelmap" >&2
  exit 1
fi

sha256sum "$MODELS_DIR/${PREFIX}.ncnn.param" "$MODELS_DIR/${PREFIX}.ncnn.bin" \
  "$MODELS_DIR/${PREFIX}.onnx" "$MODELS_DIR/${PREFIX}-labelmap.txt"
