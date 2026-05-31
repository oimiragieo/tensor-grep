from importlib import import_module
from typing import Any

_EXPORTS = {
    "AstBackend": "tensor_grep.backends.ast_backend",
    "AstGrepWrapperBackend": "tensor_grep.backends.ast_wrapper_backend",
    "CPUBackend": "tensor_grep.backends.cpu_backend",
    "ComputeBackend": "tensor_grep.backends.base",
    "CuDFBackend": "tensor_grep.backends.cudf_backend",
    "CybertBackend": "tensor_grep.backends.cybert_backend",
    "HAVE_RUST": "tensor_grep.backends.rust_backend",
    "InvalidRegexError": "tensor_grep.backends.cpu_backend",
    "RipgrepBackend": "tensor_grep.backends.ripgrep_backend",
    "RustCoreBackend": "tensor_grep.backends.rust_backend",
    "StringZillaBackend": "tensor_grep.backends.stringzilla_backend",
    "TorchBackend": "tensor_grep.backends.torch_backend",
    "get_supported_languages": "tensor_grep.backends.ast_backend",
    "huggingface_cache_status": "tensor_grep.backends.cybert_backend",
    "is_native_ast_language": "tensor_grep.backends.ast_backend",
    "normalize_ast_language": "tensor_grep.backends.ast_backend",
}

__all__ = [
    "HAVE_RUST",
    "AstBackend",
    "AstGrepWrapperBackend",
    "CPUBackend",
    "ComputeBackend",
    "CuDFBackend",
    "CybertBackend",
    "InvalidRegexError",
    "RipgrepBackend",
    "RustCoreBackend",
    "StringZillaBackend",
    "TorchBackend",
    "get_supported_languages",
    "huggingface_cache_status",
    "is_native_ast_language",
    "normalize_ast_language",
]


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    value = getattr(module, name)
    globals()[name] = value
    return value
