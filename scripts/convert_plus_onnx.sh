#!/usr/bin/env bash
set -euo pipefail

# Convert a cached Frigate+ YOLO model into the ncnn .param/.bin pair this
# detector loads, and derive its labelmap from the model's own metadata.
#
# Frigate stores Frigate+ downloads in <config>/model_cache as a hash-named
# ONNX file with a same-named .json beside it. Both are required: the ONNX
# carries the graph, the JSON carries the input geometry and the class names.
# Reading geometry from the JSON rather than assuming 320 or 640 keeps the
# exported model and the Frigate model: block from silently disagreeing.
#
# Unlike scripts/export_ncnn_model.sh, which pulls a public Ultralytics
# checkpoint, this converts a model you already own. Nothing is downloaded from
# Frigate+ and the PLUS_API_KEY is never needed here.
#
# Usage:
#   scripts/convert_plus_onnx.sh <model_cache_dir> <model_id> [output_prefix]
#
# Example:
#   scripts/convert_plus_onnx.sh ~/config/model_cache 0a4483a45c211eae1e8ba2daebff2b37

PNNX_IMAGE="${PNNX_IMAGE:-python:3.11-slim}"
# Keep this on the same dated release as NCNN_TAG in the active profile. pnnx
# emits the ncnn graph that the runtime wheel has to load, so converting with a
# newer pnnx than the installed ncnn can emit layer types ncnn does not know.
PNNX_VERSION="${PNNX_VERSION:-20260526}"

if (( $# < 2 )); then
  echo "usage: $0 <model_cache_dir> <model_id> [output_prefix]" >&2
  exit 2
fi

CACHE_DIR="$(cd "$1" && pwd)"
MODEL_ID="$2"
MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

ONNX_SRC="$CACHE_DIR/$MODEL_ID"
JSON_SRC="$CACHE_DIR/$MODEL_ID.json"
for required in "$ONNX_SRC" "$JSON_SRC"; do
  if [[ ! -r "$required" ]]; then
    echo "not readable: $required" >&2
    echo "Frigate writes model_cache as root; copy the pair somewhere readable first." >&2
    exit 1
  fi
done

# Pull name/width/height out of the sidecar so the artifacts are self-describing
# and the caller does not have to restate geometry that is already recorded.
read -r MODEL_NAME WIDTH HEIGHT INPUT_DTYPE < <(python3 -c '
import json, sys
meta = json.load(open(sys.argv[1]))
print(meta.get("name", "plus"), meta["width"], meta["height"], meta.get("inputDataType", "float"))
' "$JSON_SRC")

if [[ "$INPUT_DTYPE" != "float" ]]; then
  echo "model input dtype is '$INPUT_DTYPE'; this ncnn detector path expects float" >&2
  exit 1
fi

PREFIX="${3:-${MODEL_NAME}-${WIDTH}}"
echo "Converting $MODEL_ID ($MODEL_NAME, ${WIDTH}x${HEIGHT}) -> $PREFIX"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
cp "$ONNX_SRC" "$WORK_DIR/$PREFIX.onnx"

# pnnx infers the input format from the file extension, which is why the
# hash-named cache file is copied to a .onnx name first. fp16=0 keeps fp32
# weights so the CPU/GPU parity gate in validate_vulkan.sh stays meaningful.
#
# Runs as root inside the container so pip can install into site-packages;
# artifacts are chowned back afterwards, same as the Ultralytics exporter.
docker run --rm -v "$WORK_DIR:/work" -w /work \
  -e PNNX_VERSION="$PNNX_VERSION" -e PREFIX="$PREFIX" \
  -e WIDTH="$WIDTH" -e HEIGHT="$HEIGHT" \
  "$PNNX_IMAGE" bash -lc '
    set -euo pipefail
    # --no-deps is deliberate and load-bearing. The pnnx wheel declares torch as
    # a dependency and pip otherwise pulls torch plus its CUDA wheels -- roughly
    # 3 GB -- before doing anything. torch is only needed to load TorchScript
    # input; an ONNX file goes through the bundled binary alone.
    pip install --no-cache-dir --no-deps "pnnx==${PNNX_VERSION}"

    # Invoke that binary rather than the "pnnx" console script. The script is a
    # Python wrapper whose package __init__ imports torch unconditionally, so
    # with --no-deps it dies on ModuleNotFoundError before it ever looks at the
    # input file. The binary itself has no Python dependencies.
    # Locate it through sysconfig rather than "import pnnx", which would hit
    # the same torch import this is working around.
    PNNX_BIN="$(python3 -c "import os, sysconfig; print(os.path.join(sysconfig.get_paths()[\"purelib\"], \"pnnx\", \"pnnx\"))")"
    test -x "$PNNX_BIN" || { echo "pnnx binary not found at $PNNX_BIN" >&2; exit 1; }
    "$PNNX_BIN" "${PREFIX}.onnx" "inputshape=[1,3,${HEIGHT},${WIDTH}]" fp16=0
  '

docker run --rm -v "$WORK_DIR:/work" "$PNNX_IMAGE" \
  chown -R "$(id -u):$(id -g)" /work

# pnnx names its outputs after a sanitised stem, not the input filename: it
# also emits <stem>_pnnx.py, so the stem has to be a valid Python identifier
# and every "-" becomes "_" ("yolov9s-320" -> "yolov9s_320"). Rather than
# reimplement that rule, take whatever single .ncnn.param it produced and
# rename it to the canonical prefix on the way out.
mapfile -t PRODUCED < <(find "$WORK_DIR" -maxdepth 1 -name '*.ncnn.param')
if (( ${#PRODUCED[@]} != 1 )); then
  echo "expected exactly one .ncnn.param from pnnx, found ${#PRODUCED[@]}" >&2
  ls -la "$WORK_DIR" >&2
  exit 1
fi
PARAM_SRC="${PRODUCED[0]}"
BIN_SRC="${PARAM_SRC%.param}.bin"
if [[ ! -r "$BIN_SRC" ]]; then
  echo "pnnx produced $(basename "$PARAM_SRC") without a matching .bin" >&2
  exit 1
fi

cp "$PARAM_SRC" "$MODELS_DIR/$PREFIX.ncnn.param"
cp "$BIN_SRC" "$MODELS_DIR/$PREFIX.ncnn.bin"
cp "$ONNX_SRC" "$MODELS_DIR/$PREFIX.onnx"

# Frigate picks its labelmap parser by testing whether the first
# space-delimited token of line 1 is a digit (frigate/util/builtin.py,
# load_labels). "0 person" parses as index 0 -> "person"; "0:person" does not,
# and every class ends up misnamed with no parse error -- only a startup
# warning that your tracked objects are unsupported. Emit "<index> <name>".
python3 -c '
import json, sys
meta = json.load(open(sys.argv[1]))
labels = meta["labelMap"]
with open(sys.argv[2], "w") as handle:
    for index in sorted(labels, key=int):
        handle.write(f"{int(index)} {str(labels[index]).strip()}\n")
print(f"wrote labelmap with {len(labels)} classes")
' "$JSON_SRC" "$MODELS_DIR/$PREFIX-labelmap.txt"

sha256sum "$MODELS_DIR/$PREFIX.ncnn.param" "$MODELS_DIR/$PREFIX.ncnn.bin" \
  "$MODELS_DIR/$PREFIX.onnx" "$MODELS_DIR/$PREFIX-labelmap.txt"

cat <<EOF

Frigate model block for this export:

model:
  path: /config/model_cache/$PREFIX.ncnn.param
  model_type: yolo-generic
  width: $WIDTH
  height: $HEIGHT
  input_tensor: nchw
  input_dtype: float
  input_pixel_format: rgb
  labelmap_path: /config/model_cache/$PREFIX-labelmap.txt
EOF
