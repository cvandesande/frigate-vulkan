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


class NcnnDeviceLost(RuntimeError):
    """Device lost to a GPU reset. ncnn's Vulkan device is a process-wide
    singleton, so only a new process can recover."""


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
    device: int | None = Field(
        default=None,
        title="Vulkan device index; defaults to ncnn's highest-scoring GPU",
    )


class NcnnDetector(DetectionApi):
    type_key = DETECTOR_KEY

    def __init__(self, detector_config: NcnnDetectorConfig):
        super().__init__(detector_config)
        param_path = detector_config.model.path
        if not param_path.endswith(".param"):
            raise ValueError("ncnn: model.path must name an ncnn .param file")
        bin_path = param_path.removesuffix(".param") + ".bin"

        self.net = ncnn.Net()
        gpu_count = ncnn.get_gpu_count()
        self.net.opt.use_vulkan_compute = gpu_count > 0
        if not self.net.opt.use_vulkan_compute:
            logger.warning("ncnn: no Vulkan GPU found, falling back to CPU")
        else:
            # mesa-vulkan-drivers installs lavapipe alongside RADV, so a
            # software rasterizer always enumerates next to the real GPU.
            # Selecting the device explicitly keeps a silent fall back onto it
            # -- which looks like a working Vulkan path at a fraction of the
            # speed -- from being possible. Must precede load_param/load_model.
            for index in range(gpu_count):
                logger.info(
                    "ncnn: vulkan device %d: %s",
                    index,
                    ncnn.get_gpu_info(index).device_name(),
                )
            device = detector_config.device
            if device is None:
                device = ncnn.get_default_gpu_index()
            if not 0 <= device < gpu_count:
                raise ValueError(
                    f"ncnn: device {device} out of range, {gpu_count} Vulkan device(s) present"
                )
            self.net.set_vulkan_device(device)
            logger.info(
                "ncnn: using vulkan device %d (%s)",
                device,
                ncnn.get_gpu_info(device).device_name(),
            )
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

        # The probe below must match the dtype Frigate will actually send.
        input_dtype = getattr(detector_config.model, "input_dtype", None)
        dtype_value = getattr(input_dtype, "value", input_dtype)
        self.input_dtype = np.uint8 if dtype_value == "int" else np.float32
        self._verify_device()

    def _verify_device(self) -> None:
        """Warm-up inference: load_model() reports success even when its weight
        upload just faulted the GPU, so this is the first point the lost device
        is visible. See docs/vulkan-notes.md."""
        probe = np.zeros((3, self.height, self.width), dtype=self.input_dtype)
        try:
            self._extract(probe)
        except NcnnDeviceLost:
            logger.error(
                "ncnn: device already lost at startup, likely a GPU reset during "
                "the weight upload. Check dmesg for 'ring gfx timeout'."
            )
            raise
        logger.info("ncnn: device verified with a warm-up inference")

    def _extract(self, input_array: np.ndarray) -> np.ndarray:
        # ncnn.Mat borrows NumPy storage, so input_array must stay referenced
        # through extract() -- an inline temporary can be freed too early.
        mat = ncnn.Mat(input_array)
        extractor = self.net.create_extractor()
        extractor.input(self.input_name, mat)
        result, output = extractor.extract(self.output_name)
        if result != 0:
            raise NcnnDeviceLost(f"ncnn: extract returned {result}")
        return np.array(output, dtype=np.float32)

    def detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        # No fallback return: an empty result would leave detection_fps ticking
        # normally while nothing is ever detected. Frigate does not catch this,
        # so the process exits and is restarted with a fresh device.
        output = self._extract(np.ascontiguousarray(tensor_input.squeeze(0)))
        return post_process_yolo([output[np.newaxis, ...]], self.width, self.height)
