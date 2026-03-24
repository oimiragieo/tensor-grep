import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tensor_grep.cli.main import _build_rulesets_payload, _run_ast_scan_payload
from tensor_grep.cli.repo_map import (
    build_context_pack,
    build_context_render,
    build_repo_map,
    build_symbol_blast_radius,
    build_symbol_blast_radius_render,
    build_symbol_callers,
    build_symbol_defs,
    build_symbol_impact,
    build_symbol_refs,
    build_symbol_source,
)
from tensor_grep.cli.rule_packs import resolve_rule_pack
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_inventory import collect_device_inventory
from tensor_grep.core.pipeline import Pipeline
from tensor_grep.core.result import SearchResult
from tensor_grep.io.directory_scanner import DirectoryScanner

# Initialize the FastMCP server
mcp = FastMCP("tensor-grep")

_REWRITE_ROUTING_BACKEND = "AstBackend"
_REWRITE_ROUTING_REASON = "ast-native"
_INDEX_ROUTING_BACKEND = "TrigramIndex"
_INDEX_ROUTING_REASON = "index-accelerated"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _json_output_version() -> int:
    main_rs = _repo_root() / "rust_core" / "src" / "main.rs"
    try:
        match = re.search(
            r"const\s+JSON_OUTPUT_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;",
            main_rs.read_text(encoding="utf-8"),
        )
    except OSError:
        match = None
    return int(match.group(1)) if match else 1


def _rewrite_envelope() -> dict[str, Any]:
    return {
        "version": _json_output_version(),
        "routing_backend": _REWRITE_ROUTING_BACKEND,
        "routing_reason": _REWRITE_ROUTING_REASON,
        "sidecar_used": False,
    }


def _rewrite_error(message: str, *, code: str) -> str:
    payload = _rewrite_envelope()
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _audit_manifest_error(message: str, *, code: str) -> str:
    payload = {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-verify",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }
    return json.dumps(payload, indent=2)


def _ruleset_scan_error(message: str, *, code: str, ruleset: str, path: str) -> str:
    payload = {
        "version": _json_output_version(),
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-ruleset-scan",
        "sidecar_used": False,
        "ruleset": ruleset,
        "path": str(Path(path).expanduser()),
        "error": {"code": code, "message": message},
    }
    return json.dumps(payload, indent=2)


def _index_search_envelope() -> dict[str, Any]:
    return {
        "version": _json_output_version(),
        "routing_backend": _INDEX_ROUTING_BACKEND,
        "routing_reason": _INDEX_ROUTING_REASON,
        "sidecar_used": False,
    }


def _index_search_error(message: str, *, code: str, pattern: str, path: str) -> str:
    payload = _index_search_envelope()
    payload["query"] = pattern
    payload["path"] = path
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _normalize_rewrite_json_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return _rewrite_error("Rewrite command returned non-object JSON.", code="invalid_output")
    normalized = dict(payload)
    for key, value in _rewrite_envelope().items():
        normalized.setdefault(key, value)
    return json.dumps(normalized, indent=2)


def _normalize_index_search_json_payload(payload: object, *, pattern: str, path: str) -> str:
    if not isinstance(payload, dict):
        return _index_search_error(
            "Index search command returned non-object JSON.",
            code="invalid_output",
            pattern=pattern,
            path=path,
        )
    normalized = dict(payload)
    for key, value in _index_search_envelope().items():
        normalized.setdefault(key, value)
    return json.dumps(normalized, indent=2)


def _extract_rewrite_error_message(stderr: str, fallback: str) -> str:
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Traceback"):
            continue
        return line
    return fallback


@lru_cache(maxsize=1)
def _resolve_native_tg_binary() -> Path:
    repo_root = _repo_root()
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    env_override = os.environ.get("TG_MCP_TG_BINARY")

    candidates = []
    if env_override:
        candidates.append(Path(env_override).expanduser())
    candidates.extend(
        [
            repo_root / "rust_core" / "target" / "release" / binary_name,
            repo_root / "benchmarks" / binary_name,
            repo_root / "benchmarks" / "tg_rust.exe",
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "Native tg binary not found. Build rust_core/target/release/tg with cargo build --release."
    )


def _validate_rewrite_inputs(pattern: str, lang: str, path: str) -> str | None:
    if not pattern.strip():
        return "Pattern must not be empty."
    if not lang.strip():
        return "Language must not be empty."
    if not path.strip():
        return "Path must not be empty."
    if not Path(path).expanduser().exists():
        return f"Path not found: {path}"
    return None


def _validate_index_search_inputs(pattern: str, path: str) -> str | None:
    if not pattern.strip():
        return "Pattern must not be empty."
    if not path.strip():
        return "Path must not be empty."
    if not Path(path).expanduser().exists():
        return f"Path not found: {path}"
    return None


def _build_rewrite_command(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str,
    mode: str,
    verify: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
) -> list[str]:
    command = [
        str(_resolve_native_tg_binary()),
        "run",
        "--lang",
        lang,
        "--rewrite",
        replacement,
    ]

    if mode == "plan":
        command.append("--json")
    elif mode == "apply":
        command.append("--apply")
        if verify:
            command.append("--verify")
        if checkpoint:
            command.append("--checkpoint")
        if audit_manifest:
            command.extend(["--audit-manifest", audit_manifest])
        if audit_signing_key:
            command.extend(["--audit-signing-key", audit_signing_key])
        if lint_cmd:
            command.extend(["--lint-cmd", lint_cmd])
        if test_cmd:
            command.extend(["--test-cmd", test_cmd])
        command.append("--json")
    elif mode == "diff":
        command.append("--diff")
    else:
        raise ValueError(f"Unsupported rewrite mode: {mode}")

    command.extend([pattern, path])
    return command


def _build_index_search_command(*, pattern: str, path: str) -> list[str]:
    return [
        str(_resolve_native_tg_binary()),
        "search",
        "--index",
        "--json",
        pattern,
        path,
    ]


def _run_rewrite_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _execute_rewrite_json_command(command: list[str]) -> str:
    try:
        completed = _run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        return _rewrite_error(str(exc), code="unavailable")
    except OSError as exc:
        return _rewrite_error(f"Failed to execute rewrite command: {exc}", code="execution_failed")

    if completed.returncode != 0:
        return _rewrite_error(
            _extract_rewrite_error_message(
                completed.stderr or "",
                f"Rewrite command failed with exit code {completed.returncode}.",
            ),
            code="invalid_input",
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return _rewrite_error("Rewrite command produced no JSON output.", code="invalid_output")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _rewrite_error(
            "Rewrite command produced invalid JSON output.", code="invalid_output"
        )

    return _normalize_rewrite_json_payload(payload)


def _execute_rewrite_diff_command(command: list[str]) -> str:
    try:
        completed = _run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        return _rewrite_error(str(exc), code="unavailable")
    except OSError as exc:
        return _rewrite_error(
            f"Failed to execute rewrite diff command: {exc}", code="execution_failed"
        )

    if completed.returncode != 0:
        return _rewrite_error(
            _extract_rewrite_error_message(
                completed.stderr or "",
                f"Rewrite diff command failed with exit code {completed.returncode}.",
            ),
            code="invalid_input",
        )

    diff_preview = completed.stdout or ""
    if not diff_preview.strip():
        return _rewrite_error(
            "Rewrite diff command produced no diff output.", code="invalid_output"
        )

    payload = _rewrite_envelope()
    payload["diff"] = diff_preview
    return json.dumps(payload, indent=2)


def _execute_index_search_command(command: list[str], *, pattern: str, path: str) -> str:
    try:
        completed = _run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        return _index_search_error(str(exc), code="unavailable", pattern=pattern, path=path)
    except OSError as exc:
        return _index_search_error(
            f"Failed to execute index search command: {exc}",
            code="execution_failed",
            pattern=pattern,
            path=path,
        )

    if completed.returncode != 0:
        return _index_search_error(
            _extract_rewrite_error_message(
                completed.stderr or "",
                f"Index search command failed with exit code {completed.returncode}.",
            ),
            code="invalid_input",
            pattern=pattern,
            path=path,
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return _index_search_error(
            "Index search command produced no JSON output.",
            code="invalid_output",
            pattern=pattern,
            path=path,
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _index_search_error(
            "Index search command produced invalid JSON output.",
            code="invalid_output",
            pattern=pattern,
            path=path,
        )

    return _normalize_index_search_json_payload(payload, pattern=pattern, path=path)


def _routing_summary(result: SearchResult) -> str:
    return (
        "Routing: "
        f"backend={result.routing_backend or 'unknown'} "
        f"reason={result.routing_reason or 'unknown'} "
        f"gpu_device_ids={result.routing_gpu_device_ids} "
        f"gpu_chunk_plan_mb={result.routing_gpu_chunk_plan_mb} "
        f"distributed={result.routing_distributed} "
        f"workers={result.routing_worker_count}"
    )


def _selected_gpu_execution_defaults(
    gpu_device_ids: list[int], gpu_chunk_plan_mb: list[tuple[int, int]]
) -> tuple[bool, int]:
    if gpu_device_ids:
        worker_count = len(dict.fromkeys(gpu_device_ids))
    else:
        worker_count = len(dict.fromkeys(device_id for device_id, _ in gpu_chunk_plan_mb))
    if worker_count <= 0:
        return False, 0
    return worker_count > 1, worker_count


def _merge_runtime_routing(all_results: SearchResult, result: SearchResult) -> None:
    if result.routing_backend:
        all_results.routing_backend = result.routing_backend
        all_results.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
        all_results.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
    elif result.routing_gpu_device_ids or result.routing_gpu_chunk_plan_mb:
        all_results.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
        all_results.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
    if result.routing_reason:
        all_results.routing_reason = result.routing_reason
    all_results.routing_distributed = all_results.routing_distributed or result.routing_distributed
    all_results.routing_worker_count = max(
        all_results.routing_worker_count, result.routing_worker_count
    )


def _merge_count_metadata(all_results: SearchResult, result: SearchResult) -> None:
    for file_path, count in result.match_counts_by_file.items():
        all_results.match_counts_by_file[file_path] = (
            all_results.match_counts_by_file.get(file_path, 0) + count
        )


def _apply_selected_gpu_defaults(
    *,
    all_results: SearchResult,
    selected_backend_name: str,
    selected_backend_reason: str,
) -> None:
    runtime_override_active = (
        all_results.routing_backend is not None
        and all_results.routing_backend != selected_backend_name
    ) or (
        all_results.routing_reason is not None
        and all_results.routing_reason != selected_backend_reason
    )
    if runtime_override_active:
        return
    if all_results.routing_worker_count != 0:
        return
    if not (all_results.routing_gpu_device_ids or all_results.routing_gpu_chunk_plan_mb):
        return
    (
        all_results.routing_distributed,
        all_results.routing_worker_count,
    ) = _selected_gpu_execution_defaults(
        list(all_results.routing_gpu_device_ids),
        list(all_results.routing_gpu_chunk_plan_mb),
    )


def _finalize_aggregate_result(all_results: SearchResult) -> None:
    all_results.matched_file_paths = sorted(dict.fromkeys(all_results.matched_file_paths))
    if not all_results.match_counts_by_file and all_results.matches:
        for match in all_results.matches:
            all_results.match_counts_by_file[match.file] = (
                all_results.match_counts_by_file.get(match.file, 0) + 1
            )


@mcp.tool()  # type: ignore
def tg_rulesets() -> str:
    """Return metadata for built-in security and compliance rulesets."""
    return json.dumps(_build_rulesets_payload(), indent=2)


@mcp.tool()  # type: ignore
def tg_ruleset_scan(ruleset: str, path: str = ".", language: str | None = None) -> str:
    """
    Execute a built-in ruleset scan and return structured findings.

    Args:
        ruleset: Built-in ruleset name to execute.
        path: Root path to scan.
        language: Optional language override for the ruleset.
    """
    try:
        ruleset_meta, rules = resolve_rule_pack(ruleset, language)
    except ValueError as exc:
        return _ruleset_scan_error(
            str(exc),
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )

    project_cfg: dict[str, object] = {
        "config_path": f"builtin:{ruleset_meta['name']}",
        "root_dir": Path(path).expanduser().resolve(),
        "rule_dirs": [],
        "test_dirs": [],
        "language": ruleset_meta["language"],
    }
    return json.dumps(
        _run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason="builtin-ruleset-scan",
            ruleset_name=ruleset_meta["name"],
        ),
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_repo_map(path: str = ".") -> str:
    """
    Return a deterministic repository inventory for agent context selection.

    Args:
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_repo_map(path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "repo-map",
            "sidecar_used": False,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_context_pack(query: str, path: str = ".") -> str:
    """
    Return a ranked repository context pack for edit planning.

    Args:
        query: Query text used to rank relevant files, symbols, and tests.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_context_pack(query, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "context-pack",
            "sidecar_used": False,
            "query": query,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_context_render(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    """
    Return a prompt-ready repository context bundle for edit planning.

    Args:
        query: Query text used to rank and render repo context.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(
            build_context_render(
                query,
                path,
                max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=optimize_context,
            render_profile=render_profile,
        ),
        indent=2,
    )
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "context-render",
            "sidecar_used": False,
            "query": query,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    """
    Return a prompt-ready repository context bundle derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank and render repo context.
        path: File or directory rooted at the session scope.
        max_files: Maximum files to include in the render bundle.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, or llm.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_context_render

    try:
        return json.dumps(
            session_context_render(
                session_id,
                query,
                path,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                optimize_context=optimize_context,
                render_profile=render_profile,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        return json.dumps(payload, indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
) -> str:
    """
    Return a cached-session blast radius for a symbol.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_blast_radius

    try:
        return json.dumps(
            session_blast_radius(session_id, symbol, path, max_depth=max_depth),
            indent=2,
        )
    except SessionStaleError as exc:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        return json.dumps(payload, indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_session_blast_radius_render(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    """
    Return a prompt-ready cached-session blast radius bundle for a symbol.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the render bundle.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, or llm.
    """
    from tensor_grep.cli.session_store import (
        SessionStaleError,
        session_blast_radius_render,
    )

    try:
        return json.dumps(
            session_blast_radius_render(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                optimize_context=optimize_context,
                render_profile=render_profile,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        return json.dumps(payload, indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "session_id": session_id,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_defs(symbol: str, path: str = ".") -> str:
    """
    Return exact definition locations for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_symbol_defs(symbol, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-defs",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_source(symbol: str, path: str = ".") -> str:
    """
    Return exact source blocks for a symbol definition.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_symbol_source(symbol, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-source",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_impact(symbol: str, path: str = ".") -> str:
    """
    Return likely impacted files and tests for a symbol change.

    Args:
        symbol: Exact symbol name to evaluate.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_symbol_impact(symbol, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-impact",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_refs(symbol: str, path: str = ".") -> str:
    """
    Return Python-first symbol references across the inventory root.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_symbol_refs(symbol, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-refs",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_callers(symbol: str, path: str = ".") -> str:
    """
    Return Python-first symbol call sites and likely impacted tests.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
    """
    try:
        return json.dumps(build_symbol_callers(symbol, path), indent=2)
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-callers",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()
def tg_symbol_blast_radius(symbol: str, path: str = ".", max_depth: int = 3) -> str:
    """
    Return exact callers plus a transitive file/test blast radius for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
    """
    try:
        return json.dumps(
            build_symbol_blast_radius(symbol, path, max_depth=max_depth),
            indent=2,
        )
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-blast-radius",
            "sidecar_used": False,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()
def tg_symbol_blast_radius_render(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    """
    Return a prompt-ready blast-radius bundle for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the render bundle.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, or llm.
    """
    try:
        return json.dumps(
            build_symbol_blast_radius_render(
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                optimize_context=optimize_context,
                render_profile=render_profile,
            ),
            indent=2,
        )
    except FileNotFoundError:
        payload = {
            "version": _json_output_version(),
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-blast-radius-render",
            "sidecar_used": False,
            "symbol": symbol,
            "max_depth": max(0, int(max_depth)),
            "path": str(Path(path).expanduser()),
            "error": {
                "code": "invalid_input",
                "message": f"Path not found: {Path(path).expanduser().resolve()}",
            },
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_search(
    pattern: str,
    path: str = ".",
    case_sensitive: bool = False,
    ignore_case: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    context: int | None = None,
    max_count: int | None = None,
    count_matches: bool = False,
    glob: str | None = None,
    type_filter: str | None = None,
) -> str:
    """
    Search files for a regex pattern using tensor-grep's high-speed GPU or CPU engine.

    Args:
        pattern: A regular expression or exact string used for searching.
        path: A file or directory to search. Defaults to current directory.
        case_sensitive: Execute the search case sensitively.
        ignore_case: Search case insensitively (-i).
        fixed_strings: Treat pattern as a literal string instead of regex (-F).
        word_regexp: Only show matches surrounded by word boundaries (-w).
        context: Show NUM lines before and after each match (-C).
        max_count: Limit the number of matching lines per file (-m).
        count_matches: Just count the matches using ultra-fast Rust backend (-c).
        glob: Include/exclude files matching glob (e.g. '*.py').
        type_filter: Only search files matching TYPE (e.g. 'py', 'js').
    """
    config = SearchConfig(
        case_sensitive=case_sensitive,
        ignore_case=ignore_case,
        fixed_strings=fixed_strings,
        word_regexp=word_regexp,
        context=context,
        max_count=max_count,
        count=count_matches,
        glob=[glob] if glob else None,
        file_type=[type_filter] if type_filter else None,
        no_messages=True,
    )

    pipeline = Pipeline(config=config)
    backend = pipeline.get_backend()
    scanner = DirectoryScanner(config)

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = getattr(
        pipeline, "selected_backend_name", backend.__class__.__name__
    )
    all_results.routing_reason = getattr(pipeline, "selected_backend_reason", "unknown")
    all_results.routing_gpu_device_ids = list(
        getattr(pipeline, "selected_gpu_device_ids", []) or []
    )
    all_results.routing_gpu_chunk_plan_mb = list(
        getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or []
    )
    try:
        for current_file in scanner.walk(path):
            result = backend.search(current_file, pattern, config=config)
            all_results.matches.extend(result.matches)
            all_results.matched_file_paths.extend(result.matched_file_paths)
            _merge_count_metadata(all_results, result)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
            _merge_runtime_routing(all_results, result)

        _apply_selected_gpu_defaults(
            all_results=all_results,
            selected_backend_name=getattr(
                pipeline, "selected_backend_name", backend.__class__.__name__
            ),
            selected_backend_reason=getattr(pipeline, "selected_backend_reason", "unknown"),
        )
        _finalize_aggregate_result(all_results)

        if all_results.is_empty:
            return f"No matches found for '{pattern}' in {path}.\n{_routing_summary(all_results)}"

        if count_matches:
            return (
                f"Found a total of {all_results.total_matches} matches across {all_results.total_files} files in {path}.\n"
                f"{_routing_summary(all_results)}"
            )

        # Format the results into a readable string for the LLM
        output = [
            f"Found {all_results.total_matches} matches across {all_results.total_files} files:",
            _routing_summary(all_results),
        ]

        # Group by file
        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        if by_file:
            for filepath, matches in list(by_file.items())[
                :15
            ]:  # Limit to first 15 files to prevent context explosion
                output.append(f"\n{filepath}:")
                for m in matches[:10]:  # Limit to 10 matches per file
                    output.append(f"  {m.line_number}: {m.text.strip()}")

            if len(by_file) > 15:
                output.append(f"\n... and {len(by_file) - 15} more files.")
        elif all_results.match_counts_by_file:
            for filepath, count in list(all_results.match_counts_by_file.items())[:15]:
                output.append(f"\n{filepath}:")
                output.append(f"  count={count}")
            if len(all_results.match_counts_by_file) > 15:
                output.append(f"\n... and {len(all_results.match_counts_by_file) - 15} more files.")
        elif all_results.matched_file_paths:
            for filepath in all_results.matched_file_paths[:15]:
                output.append(f"\n{filepath}:")
            if len(all_results.matched_file_paths) > 15:
                output.append(f"\n... and {len(all_results.matched_file_paths) - 15} more files.")

        return "\n".join(output)

    except Exception as e:
        import traceback

        return f"Search failed: {e!s}\n{traceback.format_exc()}"


@mcp.tool()  # type: ignore
def tg_ast_search(pattern: str, lang: str, path: str = ".") -> str:
    """
    Search source code structurally using PyTorch Geometric Graph Neural Networks.
    Ignores whitespace and formatting, searching the true AST structure.

    Args:
        pattern: AST pattern to search for (e.g. 'if ($A) { return $B; }').
        lang: Language to parse (e.g. 'python', 'javascript').
        path: Directory or file to search.
    """
    config = SearchConfig(ast=True, lang=lang, no_messages=True)
    pipeline = Pipeline(config=config)
    backend = pipeline.get_backend()

    backend_name = type(backend).__name__
    if backend_name not in {"AstBackend", "AstGrepWrapperBackend"}:
        return "Error: AstBackend is not available on this system. Requires torch_geometric and tree_sitter."

    scanner = DirectoryScanner(config)
    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = getattr(
        pipeline, "selected_backend_name", backend.__class__.__name__
    )
    all_results.routing_reason = getattr(pipeline, "selected_backend_reason", "unknown")
    all_results.routing_gpu_device_ids = list(
        getattr(pipeline, "selected_gpu_device_ids", []) or []
    )
    all_results.routing_gpu_chunk_plan_mb = list(
        getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or []
    )
    try:
        for current_file in scanner.walk(path):
            result = backend.search(current_file, pattern, config=config)
            all_results.matches.extend(result.matches)
            all_results.matched_file_paths.extend(result.matched_file_paths)
            _merge_count_metadata(all_results, result)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
            _merge_runtime_routing(all_results, result)

        _apply_selected_gpu_defaults(
            all_results=all_results,
            selected_backend_name=getattr(
                pipeline, "selected_backend_name", backend.__class__.__name__
            ),
            selected_backend_reason=getattr(pipeline, "selected_backend_reason", "unknown"),
        )
        _finalize_aggregate_result(all_results)

        if all_results.is_empty:
            return f"No AST matches found for pattern in {path}.\n{_routing_summary(all_results)}"

        output = [
            f"Found {all_results.total_matches} structural AST matches across {all_results.total_files} files:",
            _routing_summary(all_results),
        ]

        # Group by file
        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        if by_file:
            for filepath, matches in list(by_file.items())[:15]:
                output.append(f"\n{filepath}:")
                for m in matches[:10]:
                    output.append(f"  {m.line_number}: {m.text.strip()}")
            if len(by_file) > 15:
                output.append(f"\n... and {len(by_file) - 15} more files.")
        elif all_results.match_counts_by_file:
            for filepath, count in list(all_results.match_counts_by_file.items())[:15]:
                output.append(f"\n{filepath}:")
                output.append(f"  count={count}")
            if len(all_results.match_counts_by_file) > 15:
                output.append(f"\n... and {len(all_results.match_counts_by_file) - 15} more files.")
        elif all_results.matched_file_paths:
            for filepath in all_results.matched_file_paths[:15]:
                output.append(f"\n{filepath}:")
            if len(all_results.matched_file_paths) > 15:
                output.append(f"\n... and {len(all_results.matched_file_paths) - 15} more files.")

        return "\n".join(output)

    except Exception as e:
        import traceback

        return f"AST Search failed: {e!s}\n{traceback.format_exc()}"


@mcp.tool()  # type: ignore
def tg_classify_logs(file_path: str) -> str:
    """
    Analyze a system log file using the CyBERT NLP model to automatically
    detect warnings, errors, and malicious payloads contextually.

    Args:
        file_path: The absolute path to the log file to classify.
    """
    try:
        from tensor_grep.backends.cybert_backend import CybertBackend
        from tensor_grep.io.reader_fallback import FallbackReader

        reader = FallbackReader()
        lines = list(reader.read_lines(file_path))
        if not lines:
            return f"Error: File {file_path} is empty or unreadable."

        backend = CybertBackend()
        results = backend.classify(lines)

        output = [f"Semantic Classification for {file_path} (Sample of {len(lines)} lines):"]

        warnings_or_errors = []
        for i, r in enumerate(results):
            if r["label"] in ("warn", "error") and r["confidence"] > 0.8:
                warnings_or_errors.append((lines[i].strip(), r["label"], r["confidence"]))

        if not warnings_or_errors:
            return f"No severe anomalies detected in {file_path}. All logs appear nominal."

        output.append(f"\nDetected {len(warnings_or_errors)} High-Confidence Anomalies:")
        for text, label, conf in warnings_or_errors[:20]:  # Limit output
            output.append(f"[{label.upper()}] ({conf:.2f}) {text}")

        return "\n".join(output)

    except Exception as e:
        import traceback

        return f"Log Classification failed: {e!s}\n{traceback.format_exc()}"


@mcp.tool()  # type: ignore
def tg_devices(json_output: bool = False) -> str:
    """
    Return routable GPU inventory for scheduling and diagnostics.

    Args:
        json_output: Emit machine-readable JSON output when true.
    """
    import json

    inventory = collect_device_inventory()
    payload = inventory.to_dict()
    if json_output:
        return json.dumps(payload)

    if not inventory.devices:
        return "No routable GPUs detected."

    lines = [f"Detected {inventory.device_count} routable GPU(s):"]
    for device in inventory.devices:
        lines.append(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")
    return "\n".join(lines)


@mcp.tool()  # type: ignore
def tg_index_search(pattern: str, path: str = ".") -> str:
    """
    Search files via the native trigram index path and return machine-readable JSON.

    Args:
        pattern: Regex or literal search pattern.
        path: File or directory to search.
    """
    validation_error = _validate_index_search_inputs(pattern, path)
    if validation_error:
        return _index_search_error(
            validation_error,
            code="invalid_input",
            pattern=pattern,
            path=path,
        )

    command = _build_index_search_command(pattern=pattern, path=path)
    return _execute_index_search_command(command, pattern=pattern, path=path)


@mcp.tool()  # type: ignore
def tg_rewrite_plan(pattern: str, replacement: str, lang: str, path: str = ".") -> str:
    """
    Return the native AST rewrite plan JSON for the requested pattern and replacement.

    Args:
        pattern: AST pattern to rewrite.
        replacement: Rewrite template.
        lang: Tree-sitter language name.
        path: File or directory to scan.
    """
    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    command = _build_rewrite_command(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        mode="plan",
    )
    return _execute_rewrite_json_command(command)


@mcp.tool()  # type: ignore
def tg_rewrite_apply(
    pattern: str,
    replacement: str,
    lang: str,
    path: str = ".",
    verify: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
) -> str:
    """
    Apply native AST rewrites and optionally verify the written bytes.

    Args:
        pattern: AST pattern to rewrite.
        replacement: Rewrite template.
        lang: Tree-sitter language name.
        path: File or directory to scan.
        verify: When true, request post-apply verification from the native CLI.
        checkpoint: When true, create a rollback checkpoint before applying edits.
        audit_manifest: Optional path for a deterministic rewrite audit manifest.
        audit_signing_key: Optional path to an HMAC signing key for the audit manifest.
        lint_cmd: Optional command to run after apply/verify for structured lint validation.
        test_cmd: Optional command to run after apply/verify for structured test validation.
    """
    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    command = _build_rewrite_command(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        mode="apply",
        verify=verify,
        checkpoint=checkpoint,
        audit_manifest=audit_manifest,
        audit_signing_key=audit_signing_key,
        lint_cmd=lint_cmd,
        test_cmd=test_cmd,
    )
    return _execute_rewrite_json_command(command)


@mcp.tool()  # type: ignore
def tg_audit_manifest_verify(
    manifest_path: str,
    signing_key: str | None = None,
    previous_manifest: str | None = None,
) -> str:
    """
    Verify a rewrite audit manifest digest, chain, and optional signature.

    Args:
        manifest_path: Path to the rewrite audit manifest JSON file.
        signing_key: Optional HMAC signing key path for signed manifests.
        previous_manifest: Optional previous manifest path for validating manifest chaining.
    """
    from tensor_grep.cli.audit_manifest import verify_audit_manifest_json

    if not manifest_path.strip():
        return _audit_manifest_error("manifest_path must not be empty.", code="invalid_input")

    try:
        return verify_audit_manifest_json(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        )
    except FileNotFoundError as exc:
        return _audit_manifest_error(str(exc), code="not_found")
    except ValueError as exc:
        return _audit_manifest_error(str(exc), code="invalid_input")
    except Exception as exc:
        return _audit_manifest_error(str(exc), code="internal_error")


@mcp.tool()  # type: ignore
def tg_checkpoint_create(path: str = ".") -> str:
    """
    Create an edit checkpoint rooted at the given path.

    Args:
        path: File or directory rooted at the checkpoint scope.
    """
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    try:
        payload = create_checkpoint(path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps(payload.__dict__, indent=2)


@mcp.tool()  # type: ignore
def tg_checkpoint_list(path: str = ".") -> str:
    """
    List checkpoints rooted at the given path.

    Args:
        path: File or directory rooted at the checkpoint scope.
    """
    from tensor_grep.cli.checkpoint_store import list_checkpoints

    try:
        checkpoints = [record.__dict__ for record in list_checkpoints(path)]
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps({"version": _json_output_version(), "checkpoints": checkpoints}, indent=2)


@mcp.tool()  # type: ignore
def tg_checkpoint_undo(checkpoint_id: str, path: str = ".") -> str:
    """
    Undo an edit checkpoint rooted at the given path.

    Args:
        checkpoint_id: Checkpoint ID to restore.
        path: File or directory rooted at the checkpoint scope.
    """
    from tensor_grep.cli.checkpoint_store import undo_checkpoint

    try:
        payload = undo_checkpoint(checkpoint_id, path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
                "checkpoint_id": checkpoint_id,
            },
            indent=2,
        )

    return json.dumps(payload.__dict__, indent=2)


@mcp.tool()  # type: ignore
def tg_session_open(path: str = ".") -> str:
    """
    Create a cached repository-map session for repeated edit loops.

    Args:
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import open_session

    try:
        payload = open_session(path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps(payload.__dict__, indent=2)


@mcp.tool()  # type: ignore
def tg_session_list(path: str = ".") -> str:
    """
    List cached sessions for the current root.

    Args:
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import list_sessions

    try:
        sessions = [record.__dict__ for record in list_sessions(path)]
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps({"version": _json_output_version(), "sessions": sessions}, indent=2)


@mcp.tool()  # type: ignore
def tg_session_show(session_id: str, path: str = ".") -> str:
    """
    Return the cached repository-map payload for a session.

    Args:
        session_id: Session ID to inspect.
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import get_session

    try:
        payload = get_session(session_id, path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
                "session_id": session_id,
            },
            indent=2,
        )

    return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_session_refresh(session_id: str, path: str = ".") -> str:
    """
    Refresh a cached repository-map session after file changes.

    Args:
        session_id: Session ID to refresh.
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import refresh_session

    try:
        payload = refresh_session(session_id, path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
                "session_id": session_id,
            },
            indent=2,
        )

    return json.dumps(payload.__dict__, indent=2)


@mcp.tool()  # type: ignore
def tg_session_context(session_id: str, query: str, path: str = ".") -> str:
    """
    Return a context pack derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank relevant repo context.
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import session_context

    try:
        payload = session_context(session_id, query, path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
                "session_id": session_id,
                "query": query,
            },
            indent=2,
        )

    return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_rewrite_diff(pattern: str, replacement: str, lang: str, path: str = ".") -> str:
    """
    Return a unified diff preview for native AST rewrites without modifying files.

    Args:
        pattern: AST pattern to rewrite.
        replacement: Rewrite template.
        lang: Tree-sitter language name.
        path: File or directory to scan.
    """
    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    command = _build_rewrite_command(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        mode="diff",
    )
    return _execute_rewrite_diff_command(command)


def run_mcp_server() -> None:
    """Entry point for the MCP server."""
    mcp.run()
