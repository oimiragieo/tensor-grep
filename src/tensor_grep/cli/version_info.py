from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Sequence

_VERSION_FLAGS = frozenset({"--version", "-V"})
_VERBOSE_FLAGS = frozenset({"--verbose", "-v"})


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _module_importable(name: str) -> bool:
    if not _module_available(name):
        return False
    try:
        if name == "cudf":
            importlib.import_module("cudf")
        elif name == "pyarrow":
            importlib.import_module("pyarrow")
        elif name == "tensor_grep.rust_core":
            importlib.import_module("tensor_grep.rust_core")
        else:
            return False
        return True
    except (ImportError, OSError, AttributeError, RuntimeError, ValueError):
        return False


def _probe_torch() -> tuple[bool, bool]:
    """Return (has_gpu_torch, has_cpu_only_torch) without claiming GPU for CPU builds."""
    if not _module_available("torch"):
        return (False, False)
    try:
        torch_mod = importlib.import_module("torch")
        cuda = getattr(torch_mod, "cuda", None)
        is_available = getattr(cuda, "is_available", None)
        if callable(is_available) and bool(is_available()):
            return (True, False)
    except (ImportError, OSError, AttributeError, RuntimeError, ValueError):
        return (False, False)
    return (False, True)


def version_invocation_verbose(argv: Sequence[str]) -> bool | None:
    """Parse CLI args after argv[0] matching Rust ``version_invocation_verbose``."""
    tail = argv[1:] if argv and argv[0] == "search" else argv
    if len(tail) == 1 and tail[0] in _VERSION_FLAGS:
        return False
    if len(tail) == 2 and (
        (tail[0] in _VERSION_FLAGS and tail[1] in _VERBOSE_FLAGS)
        or (tail[0] in _VERBOSE_FLAGS and tail[1] in _VERSION_FLAGS)
    ):
        return True
    return None


def native_embeddings_ready() -> bool:
    """Return True only when rust_core is importable and compiled with native-embeddings."""
    if not _module_importable("tensor_grep.rust_core"):
        return False
    try:
        rust_core = importlib.import_module("tensor_grep.rust_core")
        probe = getattr(rust_core, "native_embeddings_ready", None)
        return bool(callable(probe) and probe())
    except (ImportError, OSError, AttributeError, RuntimeError, ValueError):
        return False


def version_detail_lines() -> tuple[str, ...]:
    """Return truthful verbose version lines based on dynamically probed modules."""
    has_cudf = _module_importable("cudf")
    has_gpu_torch, has_cpu_torch = _probe_torch()
    has_rust_core = _module_importable("tensor_grep.rust_core")
    has_pyarrow = _module_importable("pyarrow")
    has_native_embeddings = native_embeddings_ready()

    cudf_flag = "+gpu-cudf" if has_cudf else "-gpu-cudf"
    torch_flag = "+gpu-torch" if has_gpu_torch else "-gpu-torch"
    rust_flag = "+rust-core" if has_rust_core else "-rust-core"
    ipc_status = "available (cudf+pyarrow)" if (has_pyarrow and has_cudf) else "unavailable"
    embeddings_status = "ready" if has_native_embeddings else "unavailable"

    lines = [
        "",
        f"features:{cudf_flag},{torch_flag},{rust_flag}",
    ]
    if has_cpu_torch:
        lines.append("torch-cpu")
    lines.append(f"Arrow Zero-Copy IPC: {ipc_status}")
    lines.append(f"Native Embeddings: {embeddings_status}")
    return tuple(lines)
