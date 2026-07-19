#!/usr/bin/env bash
set -euo pipefail

# Pin the image rather than relying on a mutable latest tag. Override only when
# deliberately testing a different exporter release.
ULTRALYTICS_IMAGE="${ULTRALYTICS_IMAGE:-ultralytics/ultralytics:8.4.99}"
MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

docker run --rm --user "$(id -u):$(id -g)" -v "$MODELS_DIR:/models" "$ULTRALYTICS_IMAGE" \
  bash -lc 'cd /models && yolo export model=yolov9t.pt format=ncnn imgsz=320 && yolo export model=yolov9t.pt format=onnx imgsz=320'

NCNN_DIR="$MODELS_DIR/yolov9t_ncnn_model"
if [[ ! -r "$NCNN_DIR/model.ncnn.param" || ! -r "$NCNN_DIR/model.ncnn.bin" || ! -r "$MODELS_DIR/yolov9t.onnx" ]]; then
  echo "Ultralytics export did not produce the expected YOLOv9-t artifacts" >&2
  exit 1
fi

cp "$NCNN_DIR/model.ncnn.param" "$MODELS_DIR/yolov9t.ncnn.param"
cp "$NCNN_DIR/model.ncnn.bin" "$MODELS_DIR/yolov9t.ncnn.bin"
[[ -r "$NCNN_DIR/metadata.yaml" ]] && cp "$NCNN_DIR/metadata.yaml" "$MODELS_DIR/yolov9t.ncnn.metadata.yaml"
sha256sum "$MODELS_DIR/yolov9t.ncnn.param" "$MODELS_DIR/yolov9t.ncnn.bin" "$MODELS_DIR/yolov9t.onnx"
