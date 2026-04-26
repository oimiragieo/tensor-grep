from pathlib import Path

# Load pieces
run_code = Path("run_command.txt").read_text(encoding="utf-8")
scan_code = Path("scan_command.txt").read_text(encoding="utf-8")
main_code = Path("main_entry.txt").read_text(encoding="utf-8")

# Load source
source_path = Path("src/tensor_grep/cli/ast_workflows.py")
content = source_path.read_text(encoding="utf-8")

# Offsets (verified on HEAD state)
RUN_START = 16779
RUN_END = 24583
SCAN_START = 27381
SCAN_END = 32023
MAIN_START = 39479

# Helpers (improved to be class-aware for monkeypatching)
helpers_code = r'''def _get_cached_backend(name: str) -> Any:
    backend_class: Any
    if name == "AstBackend":
        from tensor_grep.backends.ast_backend import AstBackend

        backend_class = AstBackend
    elif name == "AstGrepWrapperBackend":
        from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

        backend_class = AstGrepWrapperBackend
    else:
        raise ValueError(f"Unknown AST backend: {name}")

    cache_key = (name, backend_class)
    if cache_key not in _CACHED_BACKENDS:
        _CACHED_BACKENDS[cache_key] = backend_class()
    return _CACHED_BACKENDS[cache_key]


def _check_backend_available(name: str) -> bool:
    """Check if a backend is available with class-aware caching to support monkeypatching."""
    backend = _get_cached_backend(name)
    backend_class = type(backend)
    cache_key = (name, backend_class, "availability")
    if cache_key not in _BACKEND_AVAILABILITY:
        _BACKEND_AVAILABILITY[cache_key] = backend.is_available()
    return _BACKEND_AVAILABILITY[cache_key]


def _select_ast_backend_for_pattern(
    base_config: SearchConfig,
    pattern: str,
    backend_cache: dict[tuple[str | None, str, bool], Any] | None = None,
) -> Any:
    global _SUPPORTED_NATIVE_PATTERN_RE, _BACKEND_AVAILABILITY, _CACHED_BACKENDS

    from dataclasses import replace

    if _SUPPORTED_NATIVE_PATTERN_RE is None:
        _SUPPORTED_NATIVE_PATTERN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    stripped_pattern = pattern.strip()
    supports_native_pattern = bool(
        stripped_pattern
        and (
            stripped_pattern.startswith("(")
            or _SUPPORTED_NATIVE_PATTERN_RE.fullmatch(stripped_pattern)
        )
    )
    pattern_kind = (
        "native" if base_config.ast_prefer_native and supports_native_pattern else "wrapper"
    )
    cache_key = (base_config.lang, pattern_kind, base_config.ast_prefer_native)
    if backend_cache is not None and cache_key in backend_cache:
        return backend_cache[cache_key]

    from tensor_grep.core.pipeline import Pipeline

    backend: Any
    if Pipeline.__module__ == "tensor_grep.core.pipeline":
        # Optimization: Prefer native AST backend if available, as it is much faster
        if pattern_kind == "native" and _check_backend_available("AstBackend"):
            backend = _get_cached_backend("AstBackend")
        elif _check_backend_available("AstGrepWrapperBackend"):
            backend = _get_cached_backend("AstGrepWrapperBackend")
        else:
            backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()
    else:
        backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()

    if backend_cache is not None:
        backend_cache[cache_key] = backend
    return backend
'''

# Assembly
new_content = (
    content[:RUN_START]
    + run_code
    + "\n\n"
    + helpers_code
    + "\n"
    + scan_code
    + "\n\n"
    + content[SCAN_END:MAIN_START]
    + main_code
    + "\n"
)

# Fix rule specs loader (severity/message)
new_content = new_content.replace(
    'language": str(\n                        item.get("language") or payload.get("language") or default_language\n                    ),',
    'language": str(\n                        item.get("language") or payload.get("language") or default_language\n                    ),\n                    "severity": str(item.get("severity") or payload.get("severity") or "warning"),\n                    "message": str(item.get("message") or payload.get("message") or ""),',
)
new_content = new_content.replace(
    '"language": str(payload.get("language") or default_language),',
    '"language": str(payload.get("language") or default_language),\n            "severity": str(payload.get("severity") or "warning"),\n            "message": str(payload.get("message") or ""),',
)

source_path.write_text(new_content, encoding="utf-8")
print("Master assembly complete with offsets.")
