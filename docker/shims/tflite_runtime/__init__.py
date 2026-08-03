"""Shim mapping tflite_runtime onto ai-edge-litert.

Frigate imports ``tflite_runtime.interpreter`` at module level in
``detectors/detector_utils.py`` and ``data_processing/real_time/bird.py``, the
latter reachable from ``frigate.app`` via the embeddings maintainer -- so it is
required even though a Vulkan deployment never runs a tflite model. Upstream
installs a cp311-only wheel that has no Python 3.13 build; ai-edge-litert is
Google's supported successor and exposes the same Interpreter/load_delegate
API in 48MB rather than TensorFlow's 1.1GB. See docker/Dockerfile.py313.
"""
