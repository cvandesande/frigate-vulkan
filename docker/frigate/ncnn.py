"""ncnn/Vulkan detector plugin for Frigate's YOLO generic model contract."""

import logging
from pathlib import Path
from typing import Literal

import ncnn
import numpy as np
from pydantic import ConfigDict, Field

from frigate.detectors.detection_api import DetectionApi
from frigate.detectors.detector_config import BaseDetectorConfig
from frigate.util.model import post_process_yolo

logger = logging.getLogger(__name__)

DETECTOR_KEY = "ncnn"


def _parse_blob_names(param_path: str) -> tuple[str, str]:
    """Return the first Input output blob and the final layer output blob.

    ncnn .param files begin with a magic/version line and a layer/blob count.
    Every following layer has ``type name inputs outputs ...``; therefore the
    output blob immediately follows the input blob names. Parsing the graph
    avoids assuming pnnx's usual ``in0``/``out0`` naming.
    """
    layers: list[list[str]] = []
    with Path(param_path).open(encoding="utf-8") as param_file:
        for raw_line in param_file:
            fields = raw_line.split()
            if len(fields) < 4 or fields[0].startswith("#"):
                continue
            try:
                input_count, output_count = int(fields[2]), int(fields[3])
            except ValueError:
                continue
            blob_start = 4
            blob_end = blob_start + input_count + output_count
            if output_count and len(fields) >= blob_end:
                layers.append(fields[:blob_end])

    if not layers:
        raise ValueError(f"ncnn: no layers found in parameter file {param_path}")

    input_layers = [layer for layer in layers if layer[0] == "Input"]
    if not input_layers:
        raise ValueError(f"ncnn: no Input layer found in {param_path}")

    input_layer = input_layers[0]
    input_name = input_layer[4 + int(input_layer[2])]
    last_layer = layers[-1]
    output_name = last_layer[4 + int(last_layer[2])]
    return input_name, output_name


class NcnnDetectorConfig(BaseDetectorConfig):
    """ncnn detector running YOLO models on the Vulkan GPU backend."""

    model_config = ConfigDict(title="ncnn")
    type: Literal["ncnn"]
    use_fp16: bool = Field(default=False, title="Enable ncnn fp16 paths")


class NcnnDetector(DetectionApi):
    type_key = DETECTOR_KEY

    def __init__(self, detector_config: NcnnDetectorConfig):
        super().__init__(detector_config)
        param_path = detector_config.model.path
        if not param_path.endswith(".param"):
            raise ValueError("ncnn: model.path must name an ncnn .param file")
        bin_path = param_path.removesuffix(".param") + ".bin"

        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = ncnn.get_gpu_count() > 0
        if not self.net.opt.use_vulkan_compute:
            logger.warning("ncnn: no Vulkan GPU found, falling back to CPU")
        # ncnn may select fp16-capable paths by default on a Vulkan device.
        # Set every flag explicitly so use_fp16=False is a real fp32 path,
        # matching the validated CPU/GPU parity configuration on Polaris.
        self.net.opt.use_fp16_packed = detector_config.use_fp16
        self.net.opt.use_fp16_storage = detector_config.use_fp16
        self.net.opt.use_fp16_arithmetic = detector_config.use_fp16
        self.net.load_param(param_path)
        self.net.load_model(bin_path)
        self.input_name, self.output_name = _parse_blob_names(param_path)
        logger.info("ncnn: loaded %s (vulkan=%s)", param_path, self.net.opt.use_vulkan_compute)

    def detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        # ncnn.Mat borrows NumPy storage. Keep this array referenced through
        # extract(): passing ascontiguousarray() inline can release its buffer
        # before native Vulkan execution has consumed it.
        input_array = np.ascontiguousarray(tensor_input.squeeze(0))
        mat = ncnn.Mat(input_array)
        extractor = self.net.create_extractor()
        extractor.input(self.input_name, mat)
        result, output = extractor.extract(self.output_name)
        if result != 0:
            logger.error("ncnn: extract failed with %s", result)
            return np.zeros((20, 6), dtype=np.float32)
        return post_process_yolo(
            [np.array(output, dtype=np.float32)[np.newaxis, ...]], self.width, self.height
        )
