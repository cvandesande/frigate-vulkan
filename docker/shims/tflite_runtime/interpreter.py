"""Re-export the ai-edge-litert interpreter under the tflite_runtime name.

Only the names ai_edge_litert.interpreter actually defines are re-exported;
tflite_runtime's experimental_load_delegate has no successor and is not used
by Frigate.
"""

from ai_edge_litert.interpreter import (  # noqa: F401
    Delegate,
    Interpreter,
    InterpreterWithCustomOps,
    OpResolverType,
    SignatureRunner,
    load_delegate,
)

__all__ = [
    "Delegate",
    "Interpreter",
    "InterpreterWithCustomOps",
    "OpResolverType",
    "SignatureRunner",
    "load_delegate",
]
