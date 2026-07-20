#!/usr/bin/env bash
set -euo pipefail

# Matches the default export size in scripts/export_ncnn_model.sh (IMGSZ=640).
# Point MODEL_PARAM at another file to smoke test a different resolution, e.g.
# MODEL_PARAM=/models/yolov9t-320.ncnn.param
MODEL_PARAM="${MODEL_PARAM:-/models/yolov9t-640.ncnn.param}"
MODEL_BIN="${MODEL_BIN:-${MODEL_PARAM%.param}.bin}"
export MODEL_PARAM MODEL_BIN

if [[ ! -r "$MODEL_PARAM" || ! -r "$MODEL_BIN" ]]; then
  echo "ncnn model files are required: $MODEL_PARAM and $MODEL_BIN" >&2
  echo "Create them with scripts/export_ncnn_model.sh before running this smoke test." >&2
  exit 2
fi

echo '=== Vulkan summary ==='
vulkaninfo --summary
echo '=== ncnn benchmark and CPU/GPU parity ==='
python3.11 - <<'PY'
import os
import statistics
import time
from pathlib import Path

import ncnn
import numpy as np


def blob_names(path: str) -> tuple[str, str]:
    layers = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            inputs, outputs = int(fields[2]), int(fields[3])
        except ValueError:
            continue
        end = 4 + inputs + outputs
        if outputs and len(fields) >= end:
            layers.append(fields[:end])
    input_layer = next((layer for layer in layers if layer[0] == "Input"), None)
    if input_layer is None or not layers:
        raise RuntimeError(f"Could not parse Input/final blobs in {path}")
    return input_layer[4 + int(input_layer[2])], layers[-1][4 + int(layers[-1][2])]


param_path = os.environ["MODEL_PARAM"]
bin_path = os.environ["MODEL_BIN"]
use_fp16 = os.getenv("NCNN_FP16", "0") == "1"
width = int(os.getenv("MODEL_WIDTH", "320"))
height = int(os.getenv("MODEL_HEIGHT", "320"))
input_dtype = os.getenv("MODEL_INPUT_DTYPE", "float32")
input_name, output_name = blob_names(param_path)
gpu_count = ncnn.get_gpu_count()
print(f"ncnn GPU count: {gpu_count}")
for index in range(gpu_count):
    print(f"ncnn GPU {index}: {ncnn.get_gpu_info(index).device_name()}")
if not gpu_count:
    raise SystemExit("No Vulkan GPU reported by ncnn")

rng = np.random.default_rng(0)
if input_dtype == "float32":
    data = rng.random((3, height, width), dtype=np.float32)
elif input_dtype == "uint8":
    data = rng.integers(0, 256, size=(3, height, width), dtype=np.uint8)
else:
    raise ValueError(f"Unsupported MODEL_INPUT_DTYPE={input_dtype}")

def load(vulkan: bool):
    net = ncnn.Net()
    net.opt.use_vulkan_compute = vulkan
    # Set all paths explicitly: relying on ncnn defaults makes the fp32 parity
    # check ambiguous when a driver exposes storage/packing support.
    net.opt.use_fp16_packed = use_fp16
    net.opt.use_fp16_storage = use_fp16
    net.opt.use_fp16_arithmetic = use_fp16
    if net.load_param(param_path) != 0 or net.load_model(bin_path) != 0:
        raise RuntimeError("ncnn could not load model")
    return net

def infer(net):
    extractor = net.create_extractor()
    # Keep both the NumPy array and the Mat wrapper alive through extraction.
    # ncnn may retain the array-backed buffer rather than copying it.
    input_mat = ncnn.Mat(data)
    if extractor.input(input_name, input_mat) != 0:
        raise RuntimeError("ncnn input failed")
    result, output = extractor.extract(output_name)
    if result != 0:
        raise RuntimeError(f"ncnn extract failed: {result}")
    return np.array(output, dtype=np.float32)

gpu_net = load(True)
for _ in range(5):
    infer(gpu_net)
samples = []
for _ in range(50):
    started = time.perf_counter()
    gpu_output = infer(gpu_net)
    samples.append((time.perf_counter() - started) * 1000)
cpu_net = load(False)
cpu_output = infer(cpu_net)
cpu_repeat = infer(cpu_net)
gpu_repeat = infer(gpu_net)
print(f"NCNN_FP16={int(use_fp16)}")
print(f"model={Path(param_path).name} input={width}x{height} dtype={input_dtype}")
print(f"mean_ms={statistics.fmean(samples):.3f}")
print(f"median_ms={statistics.median(samples):.3f}")
print(f"output_shape={gpu_output.shape}")
print(f"gpu_range=[{gpu_output.min():.8g}, {gpu_output.max():.8g}]")
print(f"cpu_range=[{cpu_output.min():.8g}, {cpu_output.max():.8g}]")
print(f"max_abs_cpu_repeat_error={np.max(np.abs(cpu_repeat - cpu_output)):.8g}")
print(f"max_abs_gpu_repeat_error={np.max(np.abs(gpu_repeat - gpu_output)):.8g}")
max_error = float(np.max(np.abs(gpu_output - cpu_output)))
print(f"max_abs_gpu_cpu_error={max_error:.8g}")
worst_index = np.unravel_index(np.argmax(np.abs(gpu_output - cpu_output)), gpu_output.shape)
print(
    "worst_gpu_cpu_index="
    f"{worst_index} gpu={gpu_output[worst_index]:.8g} cpu={cpu_output[worst_index]:.8g}"
)
if not use_fp16 and max_error >= 1e-2:
    raise SystemExit("Parity check failed: max error must be < 1e-2 with fp16 disabled")
PY
