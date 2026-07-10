import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from io import TextIOWrapper
from pathlib import Path
from typing import Any, cast

import anyio
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

from tensor_grep.backends.ast_backend import normalize_ast_language
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import (
    compute_native_walk_deadline,
    native_walk_deadline_exceeded,
)
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli.main import (
    _LARGE_ROOT_SCAN_FILE_CEILING,
    _apply_semantic_rerank,
    _build_doctor_payload,
    _build_rulesets_payload,
    _format_unbounded_large_root_scan_error,
    _format_unbounded_vendored_root_scan_error,
    _load_inline_rule_specs,
    _run_ast_scan_payload,
    _search_with_cpu_fallback,
    _set_semantic_rank_fallback_reason,
    _should_refuse_unbounded_large_root_scan,
    _should_refuse_unbounded_vendored_root_scan,
)
from tensor_grep.cli.orient_capsule import build_orient_capsule_json
from tensor_grep.cli.repo_map import (
    _apply_context_token_budget,
    build_context_pack,
    build_context_render,
    build_file_importers,
    build_file_imports,
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
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary
from tensor_grep.cli.scan_guardrails import BroadScanRefusedError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_inventory import collect_device_inventory
from tensor_grep.core.pipeline import ConfigurationError, Pipeline
from tensor_grep.core.result import SearchResult, merge_runtime_routing
from tensor_grep.io.directory_scanner import DirectoryScanner


def _mcp_server_version() -> str:
    try:
        return package_version("tensor-grep")
    except PackageNotFoundError:
        return "0+unknown"


# Stable contract version for the tg MCP server surface.
# Bump on intentional breaking changes to the MCP tool/resource shape, OR (round-9) on a
# tool-SET shape change (new tools / new params) worth flagging to a version-pinning client.
# CLI version is exposed separately via `tg_mcp_capabilities` -> `cli_version`.
# 1.0.0 -> 1.1.0 (round-8, audit #95 Part 1): every tool's PRIMARY path/root param is now
# confined to _mcp_root() (default cwd, override via TG_MCP_ROOT) -- a caller that
# previously relied on an out-of-cwd path succeeding (e.g. a monorepo fleet pointing an
# MCP tool at a sibling repo) now gets a structured invalid_input refusal instead of a
# result, unless TG_MCP_ROOT is set to widen the anchor. Breaking-behavior change, not a
# breaking shape change -- bump per the gate's should-fix.
# 1.1.0 -> 1.2.0 (round-9, audit #95 Part 2): additive tool-set shape change, not a breaking
# one -- 2 new tools (tg_orient, tg_doctor) and new optional params on existing tools
# (tg_search rank/semantic, the 5 symbol/file-dependency tools' deadline, tg_ruleset_scan's
# inline_rules + ruleset now optional). Every existing caller's behavior is unchanged when
# the new params are simply not passed; bumped anyway because `tg_mcp_capabilities()`'s
# `tools[]` array itself grew, which a version-pinning client may reasonably want to detect.
_TG_MCP_SERVER_CONTRACT_VERSION = "1.2.0"


def _apply_mcp_server_metadata(server: FastMCP) -> None:
    server._mcp_server.version = _TG_MCP_SERVER_CONTRACT_VERSION


# Initialize the FastMCP server
mcp = FastMCP("tensor-grep")
_apply_mcp_server_metadata(mcp)

_REWRITE_ROUTING_BACKEND = "AstBackend"
_REWRITE_ROUTING_REASON = "ast-native"
_INDEX_ROUTING_BACKEND = "TrigramIndex"
_INDEX_ROUTING_REASON = "index-accelerated"
_AGENT_ROUTING_REASON = "agent-context-capsule"
_WINDOWS_VARIADIC_METAVAR_RE = re.compile(r"(?<!\$)\$\$([A-Z][A-Z0-9_]*)")
_NATIVE_TG_REMEDIATION = (
    "Install a standalone native tg binary, put it on PATH, or set TG_NATIVE_TG_BINARY."
)
# Raised 512 -> 2000 (Fable completeness review) to match the post-cap-fix CLI default
# (repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT) so MCP routing-family tools (defs/context/etc.)
# get the same routing accuracy as the CLI. Safe: the caller-scan cost stays independently
# bounded at 512 by CALLER_SCAN_FILE_CEILING (repo_map.py) regardless of this value -- see
# the rationale comment at repo_map.py's CALLER_SCAN_FILE_CEILING definition.
_DEFAULT_MCP_REPO_SCAN_LIMIT = 2000
# Bound the context payload on the AGENT surface by default (round-6 rank-4). The #359 cap only
# reached the CLI (typer default 16000); the MCP tools an agent actually calls defaulted to None
# (unbounded), so a context pack/render could balloon to ~800KB straight into a model's prompt.
# Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS; 0/None = explicit unbounded opt-out (a guard test
# pins them equal). Literal keeps the heavy repo_map import lazy.
_DEFAULT_MCP_CONTEXT_MAX_TOKENS = 16000

_PYTHON_LOCAL_MCP_TOOLS = (
    "tg_rulesets",
    "tg_ruleset_scan",
    "tg_repo_map",
    "tg_orient",
    "tg_doctor",
    "tg_context_pack",
    "tg_edit_plan",
    "tg_context_render",
    "tg_agent_capsule",
    "tg_session_edit_plan",
    "tg_session_context_render",
    "tg_session_blast_radius",
    "tg_symbol_blast_radius_plan",
    "tg_session_blast_radius_render",
    "tg_session_blast_radius_plan",
    "tg_symbol_defs",
    "tg_symbol_source",
    "tg_symbol_impact",
    "tg_symbol_refs",
    "tg_symbol_callers",
    "tg_file_imports",
    "tg_file_importers",
    "tg_session_file_importers",
    "tg_symbol_blast_radius",
    "tg_symbol_blast_radius_render",
    "tg_search",
    "tg_ast_search",
    "tg_classify_logs",
    "tg_devices",
    "tg_audit_manifest_verify",
    "tg_audit_history",
    "tg_audit_diff",
    "tg_review_bundle_create",
    "tg_review_bundle_verify",
    "tg_checkpoint_create",
    "tg_checkpoint_list",
    "tg_checkpoint_undo",
    "tg_session_open",
    "tg_session_list",
    "tg_session_show",
    "tg_session_refresh",
    "tg_session_context",
    "tg_mcp_capabilities",
)
_EMBEDDED_SAFE_MCP_TOOLS = ("tg_rewrite_plan", "tg_rewrite_apply")
_NATIVE_REQUIRED_MCP_TOOLS = ("tg_index_search", "tg_rewrite_diff")
_MCP_TOOL_CAPABILITIES: dict[str, dict[str, object]] = {
    **{
        name: {
            "mode": "python-local",
            "native_required": False,
            "embedded_fallback": False,
            "native_required_options": [],
            "notes": "Runs without a standalone native tg binary.",
        }
        for name in _PYTHON_LOCAL_MCP_TOOLS
    },
    **{
        name: {
            "mode": "embedded-safe",
            "native_required": False,
            "embedded_fallback": True,
            "native_required_options": (
                [
                    "verify",
                    "audit_manifest",
                    "audit_signing_key",
                    "lint_cmd",
                    "test_cmd",
                ]
                if name == "tg_rewrite_apply"
                else []
            ),
            "notes": (
                "Uses embedded rewrite fallback for simple requests when standalone "
                "native tg is unavailable."
            ),
        }
        for name in _EMBEDDED_SAFE_MCP_TOOLS
    },
    **{
        name: {
            "mode": "native-required",
            "native_required": True,
            "embedded_fallback": False,
            "native_required_options": [],
            "notes": "Requires a standalone native tg binary.",
        }
        for name in _NATIVE_REQUIRED_MCP_TOOLS
    },
}
_MCP_TOOL_CAPABILITIES["tg_agent_capsule"]["notes"] = (
    "Runs without a standalone native tg binary for normal capsules; optional "
    "gpu_device_ids run a selected GPU evidence probe and report sidecar-routed "
    "GPU as unsupported."
)


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


def _envelope_base(
    *,
    routing_backend: str,
    routing_reason: str,
    include_schema_version: bool = True,
) -> dict[str, Any]:
    """Build a tool JSON envelope carrying the stable MCP contract version.

    audit A4: every tool envelope embeds ``mcp_contract_version`` alongside the
    existing data-shape ``version``/``schema_version`` so agent callers can pin
    the MCP tool/resource contract independently of the JSON output schema.
    """
    base: dict[str, Any] = {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
    }
    if include_schema_version:
        base["schema_version"] = _json_output_version()
    base["routing_backend"] = routing_backend
    base["routing_reason"] = routing_reason
    base["sidecar_used"] = False
    return base


def _log_tool_exception(tool_name: str, exc: BaseException) -> None:
    """Log the full exception (message + traceback) server-side only.

    q11: MCP tool responses are returned to the client over the protocol
    stream (stdout); the raw exception text can contain absolute filesystem
    paths, internal module structure, or a full stack trace and must never
    be shipped there. The full detail is written to stderr instead, which
    carries server-side diagnostics, not the MCP JSON-RPC channel.
    """
    import traceback

    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"[tensor-grep-mcp] {tool_name} failed: {detail}", file=sys.stderr, end="")


def _sanitized_tool_error(tool_name: str, exc: BaseException) -> dict[str, Any]:
    """Build a stable, sanitized error object for an MCP tool JSON response.

    Logs the full exception server-side (see `_log_tool_exception`) and
    returns only a short category + safe reason to the client -- never the
    raw exception text or a stack trace. Still signals failure (the caller
    keeps the call's error/non-empty-result contract); this only strips the
    internals from what crosses the wire.
    """
    _log_tool_exception(tool_name, exc)
    return {
        "code": "internal_error",
        "message": f"{tool_name} failed due to an internal error ({exc.__class__.__name__}).",
        "retryable": False,
    }


def _sanitized_tool_error_text(tool_name: str, exc: BaseException) -> str:
    """Plain-text counterpart of `_sanitized_tool_error` for tool response
    modes that return free text instead of a JSON envelope.
    """
    _log_tool_exception(tool_name, exc)
    return (
        f"{tool_name} failed: internal error ({exc.__class__.__name__}). "
        "See server logs for detail."
    )


def _rewrite_envelope() -> dict[str, Any]:
    return _envelope_base(
        routing_backend=_REWRITE_ROUTING_BACKEND,
        routing_reason=_REWRITE_ROUTING_REASON,
    )


def _rewrite_error_payload(
    message: str,
    *,
    code: str,
    details: list[dict[str, str]] | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    payload = _rewrite_envelope()
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    if retryable is not None:
        error["retryable"] = retryable
    payload["error"] = error
    return payload


def _rewrite_error(message: str, *, code: str, retryable: bool | None = None) -> str:
    return json.dumps(
        _rewrite_error_payload(message, code=code, retryable=retryable),
        indent=2,
    )


def _mcp_validation_commands_allowed() -> bool:
    """Whether lint_cmd/test_cmd may run over the MCP surface.

    These parameters execute a free-form shell command (sh -c / cmd /C) in the
    native apply path. Over the MCP trust boundary the tool arguments can be
    steered by untrusted repo content / prompt injection, so this shell-exec
    capability ships default-OFF (Enablement Discipline) and must be explicitly
    enabled by the operator via TG_MCP_ALLOW_VALIDATION_COMMANDS.
    """
    value = os.environ.get("TG_MCP_ALLOW_VALIDATION_COMMANDS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


# audit A2: native rewrite failures previously collapsed to code="invalid_input"
# regardless of cause, which makes an LLM caller retry a valid pattern when the
# real failure was environmental. Classify the native stderr/exit so genuinely
# distinct causes (pattern vs IO vs internal vs environment) get distinct codes
# plus a retryable hint. The historical "invalid_input" code is preserved as the
# default for unrecognized pattern-level failures that callers may key on.
_REWRITE_PATTERN_ERROR_SIGNATURES = (
    "pattern",
    "parse error",
    "failed to parse",
    "invalid rewrite",
    "invalid replacement",
    "metavar",
    "metavariable",
    "unsupported language",
    "unknown language",
    "no such language",
    "tree-sitter",
    "syntax error",
)
_REWRITE_IO_ERROR_SIGNATURES = (
    "no such file",
    "not found",
    "permission denied",
    "is a directory",
    "read-only file system",
    "os error",
    "i/o error",
    "io error",
    "failed to read",
    "failed to write",
    "failed to open",
    "broken pipe",
)
_REWRITE_INTERNAL_ERROR_SIGNATURES = (
    "panicked",
    "panic",
    "internal error",
    "unwrap",
    "index out of bounds",
    "assertion failed",
)


def _classify_native_rewrite_failure(
    stderr: str,
    *,
    returncode: int,
) -> tuple[str, bool]:
    """Map a native rewrite failure to a (code, retryable) pair.

    - ``pattern_error``: the request itself is malformed (bad pattern, bad
      replacement, unsupported language). Not retryable without changing input.
    - ``io_error``: filesystem/permission failure. Retryable once the
      environment is fixed; caller should not rewrite the pattern.
    - ``native_internal_error``: the native engine crashed/panicked. Retryable;
      pattern is likely valid.
    - ``invalid_input``: preserved historical fallback for unrecognized
      non-zero exits (treated as a request problem, not retryable).
    """
    haystack = stderr.casefold()
    if any(token in haystack for token in _REWRITE_INTERNAL_ERROR_SIGNATURES):
        return "native_internal_error", True
    if any(token in haystack for token in _REWRITE_IO_ERROR_SIGNATURES):
        return "io_error", True
    if any(token in haystack for token in _REWRITE_PATTERN_ERROR_SIGNATURES):
        return "pattern_error", False
    return "invalid_input", False


def _native_unavailable_error(
    *,
    tool: str,
    payload: dict[str, Any],
    message: str | None = None,
) -> str:
    unavailable_payload = dict(payload)
    unavailable_payload["routing_reason"] = "native-tg-unavailable"
    unavailable_payload["tool"] = tool
    unavailable_payload["error"] = {
        "code": "unavailable",
        "message": message or f"{tool} requires a standalone native tg binary.",
        "remediation": _NATIVE_TG_REMEDIATION,
    }
    return json.dumps(unavailable_payload, indent=2)


def _resolve_native_tg_binary_for_mcp() -> tuple[Path | None, str | None]:
    try:
        return resolve_native_tg_binary(), None
    except FileNotFoundError as exc:
        return None, str(exc)


def _audit_manifest_error(message: str, *, code: str) -> str:
    payload = _envelope_base(
        routing_backend="AuditManifest",
        routing_reason="audit-manifest-verify",
    )
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _audit_history_error(message: str, *, code: str) -> str:
    payload = _envelope_base(
        routing_backend="AuditManifest",
        routing_reason="audit-manifest-history",
    )
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _audit_diff_error(message: str, *, code: str) -> str:
    payload = _envelope_base(
        routing_backend="AuditManifest",
        routing_reason="audit-manifest-diff",
    )
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _effective_auto_refresh(refresh_on_stale: bool, auto_refresh: bool | None) -> bool:
    return bool(refresh_on_stale or auto_refresh)


def _session_error_payload(
    *,
    session_id: str,
    path: str,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "session_id": session_id,
        "path": str(Path(path).expanduser()),
        **extra,
        "error": {
            "code": code,
            "message": message,
            "detail": detail or {},
        },
    }
    return json.dumps(payload, indent=2)


def _session_exception_payload(
    *,
    session_id: str | None = None,
    path: str,
    message: str,
    detail: dict[str, Any] | None = None,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "path": str(Path(path).expanduser()),
        **extra,
        "error": {
            "code": "invalid_input",
            "message": message,
            "detail": detail or {},
        },
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return json.dumps(payload, indent=2)


def _review_bundle_error(message: str, *, code: str, routing_reason: str) -> str:
    payload = _envelope_base(
        routing_backend="AuditManifest",
        routing_reason=routing_reason,
        include_schema_version=False,
    )
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _ruleset_scan_error(message: str, *, code: str, ruleset: str | None, path: str) -> str:
    payload = _envelope_base(
        routing_backend="AstBackend",
        routing_reason="builtin-ruleset-scan",
        include_schema_version=False,
    )
    payload["ruleset"] = ruleset
    payload["path"] = str(Path(path).expanduser())
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _index_search_envelope() -> dict[str, Any]:
    return _envelope_base(
        routing_backend=_INDEX_ROUTING_BACKEND,
        routing_reason=_INDEX_ROUTING_REASON,
        include_schema_version=False,
    )


def _index_search_error(message: str, *, code: str, pattern: str, path: str) -> str:
    payload = _index_search_envelope()
    payload["query"] = pattern
    payload["path"] = path
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _agent_capsule_error(message: str, *, code: str, query: str, path: str) -> str:
    payload = _envelope_base(
        routing_backend="RepoMap",
        routing_reason=_AGENT_ROUTING_REASON,
        include_schema_version=False,
    )
    payload["query"] = query
    payload["path"] = str(Path(path).expanduser())
    payload["error"] = {"code": code, "message": message}
    return json.dumps(payload, indent=2)


def _embedded_rewrite_available() -> bool:
    try:
        from tensor_grep.rust_core import ast_rewrite_apply_json, ast_rewrite_plan_json
    except Exception:
        return False
    return callable(ast_rewrite_apply_json) and callable(ast_rewrite_plan_json)


def _mcp_capabilities_payload() -> dict[str, Any]:
    native_tg, native_error = _resolve_native_tg_binary_for_mcp()
    native_tg_payload: dict[str, Any] = {
        "available": native_tg is not None,
        "path": None if native_tg is None else str(native_tg),
    }
    if native_error is not None:
        native_tg_payload["error"] = native_error
    return {
        "version": _json_output_version(),
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": _json_output_version(),
        "routing_backend": "MCPRuntime",
        "routing_reason": "mcp-capabilities",
        "sidecar_used": False,
        "mcp_protocol_version": types.LATEST_PROTOCOL_VERSION,
        "mcp_supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "cli_version": _mcp_server_version(),
        "native_tg": native_tg_payload,
        "embedded_rewrite": {
            "available": _embedded_rewrite_available(),
        },
        "tools": [
            {"name": name, **capability}
            for name, capability in sorted(_MCP_TOOL_CAPABILITIES.items())
        ],
    }


def _inject_mcp_contract_fields(result_json: str) -> str:
    """H9: inject mcp_contract_version and schema_version into every tool JSON envelope.

    Operates on the final serialized string so it works uniformly across all tool
    code-paths regardless of which builder produced the underlying dict.  No-ops for
    non-dict JSON (arrays, primitives) to stay safe.
    """
    try:
        payload = json.loads(result_json)
    except (json.JSONDecodeError, ValueError):
        return result_json
    if not isinstance(payload, dict):
        return result_json
    payload.setdefault("mcp_contract_version", _TG_MCP_SERVER_CONTRACT_VERSION)
    payload.setdefault("schema_version", _json_output_version())
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


# audit A1: plan-bound apply / TOCTOU. tg_rewrite_plan emits a stable plan_digest
# derived from the normalized request plus the sorted pre-image content hashes of
# every site it would touch. tg_rewrite_apply can be passed expected_plan_digest /
# expected_match_count; when supplied they are recomputed against the *current*
# tree before any edit is written and the apply is refused with code="plan_drift"
# if reality diverged from what was previewed. Enforced only when supplied so the
# default plan -> apply flow stays fully back-compatible.
_PLAN_DIGEST_VERSION = "tg-plan-digest-v1"


def _normalize_plan_digest_path(file_value: object) -> str:
    if not isinstance(file_value, str) or not file_value.strip():
        return ""
    try:
        return Path(file_value).expanduser().as_posix()
    except (OSError, ValueError):
        return file_value


def _plan_edit_site_signatures(plan_payload: dict[str, Any]) -> list[str] | None:
    """Return one stable signature per planned edit site, or None if unparseable.

    Each signature binds the touched file path, the byte range, and a hash of the
    site's current pre-image text (``original_text``). The native engine derives
    ``original_text`` from the file as it exists right now, so any change to the
    underlying bytes at that site changes the signature.
    """
    edits = plan_payload.get("edits")
    if not isinstance(edits, list):
        return None
    signatures: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        file_token = _normalize_plan_digest_path(edit.get("file"))
        byte_range = edit.get("byte_range")
        if isinstance(byte_range, dict):
            start = byte_range.get("start")
            end = byte_range.get("end")
        else:
            start = None
            end = None
        original_text = edit.get("original_text")
        original_token = original_text if isinstance(original_text, str) else ""
        pre_image = hashlib.sha256(original_token.encode("utf-8")).hexdigest()
        signatures.append(f"{file_token}\x1f{start}\x1f{end}\x1f{pre_image}")
    signatures.sort()
    return signatures


def _compute_plan_digest(plan_payload: object) -> str | None:
    """Compute a stable digest binding the request to the previewed pre-image.

    Returns None when the payload is an error or does not carry a parseable edit
    list (so callers can skip digest stamping/enforcement instead of guessing).
    """
    if not isinstance(plan_payload, dict) or plan_payload.get("error"):
        return None
    site_signatures = _plan_edit_site_signatures(plan_payload)
    if site_signatures is None:
        return None
    pattern = str(plan_payload.get("pattern", "")).strip()
    replacement = str(plan_payload.get("replacement", "")).strip()
    lang = str(plan_payload.get("lang", "")).strip().casefold()
    hasher = hashlib.sha256()
    hasher.update(_PLAN_DIGEST_VERSION.encode("utf-8"))
    for component in (pattern, replacement, lang):
        hasher.update(b"\x1e")
        hasher.update(component.encode("utf-8"))
    hasher.update(b"\x1d")
    hasher.update(str(len(site_signatures)).encode("utf-8"))
    for signature in site_signatures:
        hasher.update(b"\x1e")
        hasher.update(signature.encode("utf-8"))
    return hasher.hexdigest()


def _plan_match_count(plan_payload: object) -> int | None:
    if not isinstance(plan_payload, dict):
        return None
    total_edits = plan_payload.get("total_edits")
    if isinstance(total_edits, int) and not isinstance(total_edits, bool):
        return total_edits
    edits = plan_payload.get("edits")
    if isinstance(edits, list):
        return len(edits)
    return None


def _stamp_plan_digest(plan_json: str) -> str:
    """Stamp plan_digest/match_count onto a successful plan JSON string."""
    try:
        plan_payload = json.loads(plan_json)
    except json.JSONDecodeError:
        return plan_json
    if not isinstance(plan_payload, dict) or plan_payload.get("error"):
        return plan_json
    digest = _compute_plan_digest(plan_payload)
    if digest is None:
        return plan_json
    plan_payload["plan_digest"] = digest
    match_count = _plan_match_count(plan_payload)
    if match_count is not None:
        plan_payload.setdefault("match_count", match_count)
    return json.dumps(plan_payload, indent=2)


def _plan_drift_detail(
    *,
    expected_plan_digest: str | None,
    actual_plan_digest: str | None,
    expected_match_count: int | None,
    actual_match_count: int | None,
    reason: str,
) -> list[dict[str, str]]:
    detail: dict[str, str] = {"reason": reason}
    if expected_plan_digest is not None:
        detail["expected_plan_digest"] = expected_plan_digest
    if actual_plan_digest is not None:
        detail["actual_plan_digest"] = actual_plan_digest
    if expected_match_count is not None:
        detail["expected_match_count"] = str(expected_match_count)
    if actual_match_count is not None:
        detail["actual_match_count"] = str(actual_match_count)
    return [detail]


def _extract_rewrite_error_message(stderr: str, fallback: str) -> str:
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Traceback"):
            continue
        return line
    return fallback


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


def _restore_variadic_metavar_escaping(value: str) -> str:
    return _WINDOWS_VARIADIC_METAVAR_RE.sub(r"$$$\1", value)


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
        str(resolve_native_tg_binary()),
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

    # round-3 security: end options before the user-controlled positionals so a pattern
    # beginning with `-` cannot be parsed by the native binary as a flag (argv injection).
    command.extend(["--", pattern, path])
    return command


def _build_index_search_command(*, pattern: str, path: str) -> list[str]:
    return [
        str(resolve_native_tg_binary()),
        "search",
        "--index",
        "--json",
        # round-3 security: end options before the user-controlled positionals so a pattern
        # beginning with `-` cannot be parsed by the native binary as a flag (argv injection).
        "--",
        pattern,
        path,
    ]


def _run_rewrite_subprocess(command: list[str]) -> subprocess.CompletedProcess[str]:
    import sys

    from tensor_grep.cli.subprocess_policy import run_subprocess

    env = os.environ.copy()
    env["TG_SIDECAR_PYTHON"] = sys.executable
    return run_subprocess(
        command,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def _execute_rewrite_json_command(command: list[str]) -> str:
    try:
        completed = _run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        return _rewrite_error(str(exc), code="unavailable", retryable=True)
    except OSError as exc:
        return _rewrite_error(
            f"Failed to execute rewrite command: {exc}",
            code="execution_failed",
            retryable=True,
        )

    if completed.returncode != 0:
        stderr = completed.stderr or ""
        code, retryable = _classify_native_rewrite_failure(
            stderr,
            returncode=completed.returncode,
        )
        return _rewrite_error(
            _extract_rewrite_error_message(
                stderr,
                f"Rewrite command failed with exit code {completed.returncode}.",
            ),
            code=code,
            retryable=retryable,
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

    _record_generated_audit_manifest(payload)
    return _normalize_rewrite_json_payload(payload)


def _execute_embedded_rewrite_json(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str,
    mode: str,
) -> str:
    try:
        from tensor_grep.rust_core import ast_rewrite_apply_json, ast_rewrite_plan_json
    except Exception as exc:
        return _rewrite_error(
            f"Embedded native rewrite support unavailable: {exc}",
            code="unavailable",
            retryable=True,
        )

    try:
        if mode == "plan":
            stdout = ast_rewrite_plan_json(pattern, replacement, lang, path)
        elif mode == "apply":
            stdout = ast_rewrite_apply_json(pattern, replacement, lang, path)
        else:
            return _rewrite_error(
                f"Embedded native rewrite mode is unsupported: {mode}",
                code="unavailable",
                retryable=True,
            )
    except Exception as exc:
        # audit A2: classify the embedded engine exception so callers can tell a
        # malformed pattern (not retryable) from an IO/internal failure (retryable).
        code, retryable = _classify_native_rewrite_failure(str(exc), returncode=1)
        return _rewrite_error(str(exc), code=code, retryable=retryable)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return _rewrite_error(
            "Embedded rewrite command produced invalid JSON output.",
            code="invalid_output",
        )

    _record_generated_audit_manifest(payload)
    return _normalize_rewrite_json_payload(payload)


def _normalize_apply_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """M12: inject applied_edits and normalize checkpoint timestamp/id format.

    The native Rust engine stores created_at as a Unix epoch seconds string and
    checkpoint_id as ckpt-{epoch}-{hex}, while the Python checkpoint_store uses
    ISO-8601 for created_at and ckpt-{datetime}-{hex}.  Normalize the native format
    here so all tg_rewrite_apply callers see a consistent envelope regardless of
    which code-path created the checkpoint.
    """
    from datetime import UTC, datetime

    # M12 part 1: inject top-level applied_edits count.
    if "applied_edits" not in payload:
        edits = payload.get("edits")
        if isinstance(edits, list):
            payload["applied_edits"] = len(edits)
        else:
            total = payload.get("total_edits")
            if isinstance(total, int) and not isinstance(total, bool):
                payload["applied_edits"] = total
            else:
                payload["applied_edits"] = 0

    # M12 part 2: normalize checkpoint created_at to ISO-8601 and checkpoint_id
    # from ckpt-{epoch}-{hex} to ckpt-{datetime}-{hex}.
    ckpt = payload.get("checkpoint")
    if isinstance(ckpt, dict):
        created_at = ckpt.get("created_at")
        ckpt_id = ckpt.get("checkpoint_id") or ""
        # Detect epoch string: all digits, 8-12 chars (covers seconds since 1970 for years
        # 2001-2286).
        if (
            isinstance(created_at, str)
            and created_at.strip().isdigit()
            and 8 <= len(created_at.strip()) <= 12
        ):
            epoch_s = int(created_at.strip())
            iso_str = datetime.fromtimestamp(epoch_s, tz=UTC).isoformat()
            ckpt["created_at"] = iso_str
            # Rewrite ckpt-{epoch}-{hex} → ckpt-{datetime}-{hex}
            prefix = f"ckpt-{created_at.strip()}-"
            if ckpt_id.startswith(prefix):
                hex_suffix = ckpt_id[len(prefix) :]
                dt_part = datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y%m%d%H%M%S")
                ckpt["checkpoint_id"] = f"ckpt-{dt_part}-{hex_suffix}"

    return payload


def _produce_rewrite_plan_json(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str,
) -> str:
    """Run a rewrite plan and return its raw (un-stamped) JSON string.

    Shared by ``execute_rewrite_plan_json`` (which stamps the plan digest) and the
    apply-side drift check (audit A1), so both observe identical plan semantics.
    Inputs must already be validated and metavar-unescaped by the caller.
    """
    native_tg, _native_error = _resolve_native_tg_binary_for_mcp()
    if native_tg is None:
        if not _embedded_rewrite_available():
            return _native_unavailable_error(
                tool="tg_rewrite_plan",
                payload=_rewrite_envelope(),
                message=(
                    "tg_rewrite_plan requires a standalone native tg binary "
                    "or embedded native rewrite support."
                ),
            )
        return _execute_embedded_rewrite_json(
            pattern=pattern,
            replacement=replacement,
            lang=lang,
            path=path,
            mode="plan",
        )
    command = _build_rewrite_command(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        mode="plan",
    )
    return _execute_rewrite_json_command(command)


def execute_rewrite_plan_json(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str = ".",
) -> tuple[str, int]:
    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input"), 1
    pattern = _restore_variadic_metavar_escaping(pattern)
    replacement = _restore_variadic_metavar_escaping(replacement)

    rewrite_json = _produce_rewrite_plan_json(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
    )

    rewrite_payload = json.loads(rewrite_json)
    if rewrite_payload.get("error"):
        return rewrite_json, 1
    # audit A1: stamp a stable plan_digest so callers can pin this preview and pass
    # it back to tg_rewrite_apply as expected_plan_digest for an apply-iff-unchanged
    # edit loop.
    return _stamp_plan_digest(rewrite_json), 0


def _check_apply_plan_drift(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str,
    expected_plan_digest: str | None,
    expected_match_count: int | None,
) -> str | None:
    """Return a ``plan_drift`` error JSON when the live plan diverges, else None.

    Re-plans against the current tree and compares the freshly computed digest /
    match count to the caller-supplied expectations. Inputs must already be
    validated and metavar-unescaped. No files are written by this check.
    """
    plan_json = _produce_rewrite_plan_json(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
    )
    try:
        plan_payload = json.loads(plan_json)
    except json.JSONDecodeError:
        plan_payload = None

    if not isinstance(plan_payload, dict) or plan_payload.get("error"):
        # Could not produce a comparable plan, so we cannot confirm the tree still
        # matches what was reviewed. Refuse rather than apply blindly.
        return json.dumps(
            _rewrite_error_payload(
                "Could not recompute the rewrite plan to verify expected_plan_digest; "
                "refusing to apply.",
                code="plan_drift",
                details=_plan_drift_detail(
                    expected_plan_digest=expected_plan_digest,
                    actual_plan_digest=None,
                    expected_match_count=expected_match_count,
                    actual_match_count=None,
                    reason="plan_unavailable",
                ),
                retryable=True,
            ),
            indent=2,
        )

    actual_plan_digest = _compute_plan_digest(plan_payload)
    actual_match_count = _plan_match_count(plan_payload)

    if expected_match_count is not None and actual_match_count != expected_match_count:
        return json.dumps(
            _rewrite_error_payload(
                "Rewrite plan drifted: expected_match_count no longer matches the "
                "current tree; refusing to apply.",
                code="plan_drift",
                details=_plan_drift_detail(
                    expected_plan_digest=expected_plan_digest,
                    actual_plan_digest=actual_plan_digest,
                    expected_match_count=expected_match_count,
                    actual_match_count=actual_match_count,
                    reason="match_count_mismatch",
                ),
                retryable=False,
            ),
            indent=2,
        )

    if expected_plan_digest is not None and actual_plan_digest != expected_plan_digest:
        return json.dumps(
            _rewrite_error_payload(
                "Rewrite plan drifted: expected_plan_digest no longer matches the "
                "current tree; refusing to apply.",
                code="plan_drift",
                details=_plan_drift_detail(
                    expected_plan_digest=expected_plan_digest,
                    actual_plan_digest=actual_plan_digest,
                    expected_match_count=expected_match_count,
                    actual_match_count=actual_match_count,
                    reason="digest_mismatch",
                ),
                retryable=False,
            ),
            indent=2,
        )

    return None


def execute_rewrite_apply_json(
    *,
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
    policy: str | None = None,
    expected_plan_digest: str | None = None,
    expected_match_count: int | None = None,
    allow_validation_commands: bool = False,
) -> tuple[str, int]:
    from tensor_grep.cli.apply_policy import (
        PolicyCommandsNotAllowedError,
        PolicyValidationError,
        evaluate_apply_policy,
        load_apply_policy,
    )

    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input"), 1
    pattern = _restore_variadic_metavar_escaping(pattern)
    replacement = _restore_variadic_metavar_escaping(replacement)

    # round-5 security: confine audit_manifest to cwd (the sibling precedent for a general
    # audit artifact — tg_review_bundle_create's output_path, not the rewrite scan root) and
    # consume the RESOLVED absolute path so the native subprocess argv (see
    # _build_rewrite_command below) carries the anchor-validated location, not the raw
    # candidate. Without this the validated path is discarded (TOCTOU) and the native binary
    # independently re-resolves the unconfined raw string against its own cwd.
    # NOTE (tracked follow-up, Part C of this fix): this Python-side confinement closes the
    # anchor-mismatch/discard TOCTOU (validated-location == written-location) and refuses an
    # escaping path before the native subprocess is ever spawned. It does NOT close the
    # narrower cross-process symlink-swap window: between this resolve() and the moment the
    # native Rust binary's write_audit_manifest_for_plan actually opens the resolved path
    # (rust_core/src/main.rs, ~6746-6838), a symlink could in principle be swapped in at the
    # final path component. Closing that residual window requires the Rust side to refuse via
    # symlink_metadata()+O_NOFOLLOW at the point the bytes hit disk (rust_core/src/main.rs is
    # explicitly out of scope for this PR — see deviations). This Python confinement is
    # defense-in-depth and the user-facing early error, not a full closure on its own.
    if audit_manifest is not None:
        try:
            audit_manifest = str(
                _confine_write_path(audit_manifest, _mcp_root(), label="audit_manifest")
            )
        except ValueError as exc:
            return _rewrite_error(str(exc), code="invalid_input"), 1

    # round-5 security: audit_signing_key is a READ of secret HMAC material that operators
    # legitimately keep OUTSIDE the repo (~/.config, CI-injected) — confining it to cwd would
    # be a regression. Instead gate it default-OFF behind an explicit opt-in env var, mirroring
    # the lint_cmd/test_cmd -> TG_MCP_ALLOW_VALIDATION_COMMANDS posture, closing the
    # arbitrary-read-as-HMAC-key primitive without over-restricting a legit out-of-tree key.
    if (
        audit_signing_key is not None
        and os.environ.get("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ") != "1"
    ):
        return (
            _rewrite_error(
                "audit_signing_key read requires TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1",
                code="unsupported_option",
                retryable=False,
            ),
            1,
        )

    # audit A1: plan-bound apply. When the caller pins the previously reviewed plan
    # via expected_plan_digest/expected_match_count, recompute the plan against the
    # CURRENT tree and refuse the apply (no files written) if reality has drifted.
    if expected_plan_digest is not None or expected_match_count is not None:
        drift_error = _check_apply_plan_drift(
            pattern=pattern,
            replacement=replacement,
            lang=lang,
            path=path,
            expected_plan_digest=expected_plan_digest,
            expected_match_count=expected_match_count,
        )
        if drift_error is not None:
            return drift_error, 1

    loaded_policy = None
    if policy is not None:
        # round-7 security (audit #81 Opus gate #2/#12 follow-up): policy is a caller-named
        # JSON file path read by load_apply_policy below -- unconfined it is a file-existence +
        # JSON-schema read-oracle over any path reachable from any MCP client
        # (PolicyValidationError.details echoes back which required fields are missing/
        # malformed, and a non-JSON file's json.JSONDecodeError message), same class as
        # tg_classify_logs.file_path / tg_ruleset_scan's baseline_path/suppressions_path.
        # Anchor to the REWRITE SCAN ROOT (path), not cwd: a policy file for THIS apply
        # operation legitimately lives under the scanned tree (mirrors baseline_path/
        # suppressions_path's scan_root anchor on tg_ruleset_scan, not audit_manifest's cwd
        # anchor). Forward the RESOLVED path so load_apply_policy reads the same
        # anchor-validated location this check validated.
        policy_anchor = Path(path).expanduser().resolve()
        # `path` may be a single FILE (a targeted rewrite), not a directory. A file has no
        # descendants, so confining the policy under the file itself fail-closed-REJECTS a
        # legitimately co-located policy (e.g. path=src/foo.py, policy=src/policy.json). Anchor
        # to the target's parent directory when path is not a directory, so a co-located policy
        # is allowed while a traversal escape (policy=../../etc/passwd) is still rejected -- the
        # confinement scope is the apply target's own directory subtree, which the caller is
        # already rewriting (audit #76 Opus-gate nit; the directory case is unchanged).
        if not policy_anchor.is_dir():
            policy_anchor = policy_anchor.parent
        try:
            policy = str(_confine_read_path(policy, policy_anchor, label="policy"))
        except ValueError as exc:
            return _rewrite_error(str(exc), code="invalid_input"), 1
        try:
            loaded_policy = load_apply_policy(
                policy,
                legacy_lint_cmd=lint_cmd,
                legacy_test_cmd=test_cmd,
                allow_validation_commands=allow_validation_commands,
            )
        except PolicyCommandsNotAllowedError as exc:
            # Audit HIGH (RCE): a policy file that carries lint_cmd/test_cmd is refused
            # on the gate-off surface with the same code as the direct-param rejection,
            # BEFORE any native binary or subprocess is reached.
            return _rewrite_error(str(exc), code="unsupported_option", retryable=False), 1
        except FileNotFoundError as exc:
            return _rewrite_error(str(exc), code="not_found"), 1
        except PolicyValidationError as exc:
            return (
                json.dumps(
                    _rewrite_error_payload(
                        str(exc),
                        code="invalid_policy",
                        details=exc.details,
                    ),
                    indent=2,
                ),
                1,
            )
        if loaded_policy.on_failure == "rollback" and not checkpoint:
            return (
                _rewrite_error(
                    "Policy on_failure=rollback requires checkpoint=true.",
                    code="invalid_input",
                ),
                1,
            )

    native_tg, _native_error = _resolve_native_tg_binary_for_mcp()
    checkpoint_payload: dict[str, Any] | None = None
    if native_tg is None:
        if verify or audit_manifest or audit_signing_key or lint_cmd or test_cmd:
            return (
                _native_unavailable_error(
                    tool="tg_rewrite_apply",
                    payload=_rewrite_envelope(),
                    message=(
                        "tg_rewrite_apply requires a standalone native tg binary for "
                        "verify, audit, lint, or test rewrite apply options."
                    ),
                ),
                1,
            )
        if not _embedded_rewrite_available():
            return (
                _native_unavailable_error(
                    tool="tg_rewrite_apply",
                    payload=_rewrite_envelope(),
                    message=(
                        "tg_rewrite_apply requires a standalone native tg binary "
                        "or embedded native rewrite support."
                    ),
                ),
                1,
            )
        if checkpoint:
            try:
                from tensor_grep.cli.checkpoint_store import create_checkpoint

                checkpoint_payload = create_checkpoint(path).__dict__
            except Exception as exc:
                return _rewrite_error(f"Failed to create checkpoint: {exc}", code="checkpoint"), 1
        rewrite_json = _execute_embedded_rewrite_json(
            pattern=pattern,
            replacement=replacement,
            lang=lang,
            path=path,
            mode="apply",
        )
    else:
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
            lint_cmd=None if loaded_policy is not None else lint_cmd,
            test_cmd=None if loaded_policy is not None else test_cmd,
        )
        rewrite_json = _execute_rewrite_json_command(command)
    rewrite_payload = json.loads(rewrite_json)
    if checkpoint_payload is not None:
        rewrite_payload["checkpoint"] = checkpoint_payload
    if rewrite_payload.get("error"):
        return json.dumps(rewrite_payload, indent=2), 1
    # M12: normalize applied_edits and checkpoint timestamp/id before returning.
    rewrite_payload = _normalize_apply_result_payload(rewrite_payload)
    rewrite_json = json.dumps(rewrite_payload, indent=2)
    if loaded_policy is None:
        return rewrite_json, 0

    policy_payload, exit_code = evaluate_apply_policy(
        rewrite_payload,
        loaded_policy,
        path=path,
    )
    return json.dumps(policy_payload, indent=2), exit_code


def _execute_rewrite_diff_command(command: list[str]) -> str:
    try:
        completed = _run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        return _rewrite_error(str(exc), code="unavailable", retryable=True)
    except OSError as exc:
        return _rewrite_error(
            f"Failed to execute rewrite diff command: {exc}",
            code="execution_failed",
            retryable=True,
        )

    if completed.returncode != 0:
        stderr = completed.stderr or ""
        code, retryable = _classify_native_rewrite_failure(
            stderr,
            returncode=completed.returncode,
        )
        return _rewrite_error(
            _extract_rewrite_error_message(
                stderr,
                f"Rewrite diff command failed with exit code {completed.returncode}.",
            ),
            code=code,
            retryable=retryable,
        )

    diff_preview = completed.stdout or ""
    payload = _rewrite_envelope()
    if not diff_preview.strip():
        # M11: zero matches is a valid result — return normal shape, not an error.
        payload["diff"] = ""
        payload["total_edits"] = 0
        return json.dumps(payload, indent=2)

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


@mcp.tool()  # type: ignore
def tg_mcp_capabilities() -> str:
    """
    Report MCP tool availability for the current runtime.

    The response lets clients distinguish tools that work without a standalone native
    tg binary from tools that require one.
    """
    return json.dumps(_mcp_capabilities_payload(), indent=2)


def _record_generated_audit_manifest(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    audit_manifest = payload.get("audit_manifest")
    if not isinstance(audit_manifest, dict):
        return
    manifest_path = audit_manifest.get("path")
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        return
    try:
        from tensor_grep.cli.audit_manifest import record_audit_manifest

        record_audit_manifest(manifest_path)
    except Exception:
        return


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


def _routing_payload(result: SearchResult) -> dict[str, object]:
    return {
        "backend": result.routing_backend or "unknown",
        "reason": result.routing_reason or "unknown",
        "gpu_device_ids": result.routing_gpu_device_ids,
        "gpu_chunk_plan_mb": result.routing_gpu_chunk_plan_mb,
        "distributed": result.routing_distributed,
        "workers": result.routing_worker_count,
    }


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
    merge_runtime_routing(all_results, result)
    if result.fallback_reason is not None:
        all_results.fallback_reason = result.fallback_reason


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


# H3 (Fable MCP-surface audit): the CLI's PR #400 unscoped-search-hang fix (per-file wall-clock
# deadline + the vendored/large-root refusal guards, `cli/main.py`) never reached the MCP `tg_search`
# / `tg_ast_search` walk loops -- they reimplemented the walk from scratch and drifted. This helper
# REUSES the CLI's own guard functions (imported, never reimplemented) so the two surfaces can never
# diverge again, adapted for MCP's streaming `DirectoryScanner.walk()` generator: the large-root probe
# only pulls the first `min(normalized_max_repo_files, _LARGE_ROOT_SCAN_FILE_CEILING + 1)` entries
# (never a full-tree enumeration -- that would just be the unbounded work this guard exists to avoid)
# and the caller resumes iterating from those SAME already-pulled entries via the returned iterator,
# so nothing is scanned twice.
#
# Bug #88 (dogfood v1.54.0): the guards below now require a `paths_defaulted` bool (a `--glob`/
# `--type` filter no longer bypasses them when PATH was left to default -- see
# `cli/main.py::_has_walk_scope_bound`). The MCP `path` parameter has a Python-level default
# (`path: str = "."`) rather than the CLI's argv-shape signal, so an explicit `path="."` and an
# omitted `path` are indistinguishable here -- treat the literal default value "." as
# "defaulted" (the conservative, safe reading) so a bare `tg_search(pattern=..., glob=...)` MCP
# call gets the same protection as the CLI's bare `tg search --glob ... PATTERN`.
#
# round-8 security (audit #95): `paths_defaulted` is now a REQUIRED param the caller computes
# from the RAW path BEFORE `_confine_mcp_path` resolves it to an absolute string -- deriving it
# internally here (the original `path == "."`) would silently and permanently read False once
# callers started reassigning `path` to its confined (absolute) form, defeating this whole
# bug #88 guard for every default-path call (caught by test_tg_search_refuses_glob_with_
# default_path_on_large_root going from a refusal to a real unbounded scan).
def _mcp_broad_root_scan_refusal(
    path: str,
    config: "SearchConfig",
    *,
    normalized_max_repo_files: int,
    check_large_root: bool,
    paths_defaulted: bool,
) -> tuple[str | None, "DirectoryScanner", Iterator[str]]:
    """Cheap pre-walk safety guard ported from the CLI `tg search` command.

    Returns ``(error_message, scanner, walker)``. When ``error_message`` is ``None`` the
    caller should iterate ``walker`` exactly as it would have iterated
    ``scanner.walk(path)`` -- it already carries any entries the refusal probe consumed,
    so no re-scan is needed. ``scanner`` is returned too so the caller can still read its
    post-walk ``scan_truncated`` bookkeeping attribute.
    """
    scanner = DirectoryScanner(config)
    walker: Iterator[str] = iter(scanner.walk(path))

    refuse_vendored, vendored_dirs = _should_refuse_unbounded_vendored_root_scan(
        [path],
        config,
        allow_broad_generated_scan=False,
        paths_defaulted=paths_defaulted,
    )
    if refuse_vendored:
        return _format_unbounded_vendored_root_scan_error(vendored_dirs), scanner, walker

    if not check_large_root:
        return None, scanner, walker

    probe_limit = min(normalized_max_repo_files, _LARGE_ROOT_SCAN_FILE_CEILING + 1)
    probe_files: list[str] = []
    for _ in range(probe_limit):
        try:
            probe_files.append(next(walker))
        except StopIteration:
            break
    if _should_refuse_unbounded_large_root_scan(
        len(probe_files),
        config,
        allow_broad_generated_scan=False,
        paths_defaulted=paths_defaulted,
    ):
        return (
            _format_unbounded_large_root_scan_error(_LARGE_ROOT_SCAN_FILE_CEILING),
            scanner,
            iter(probe_files),
        )
    return None, scanner, itertools.chain(probe_files, walker)


def _broad_root_scan_refusal_result(
    message: str,
    *,
    pattern: str,
    path: str,
    lang: str | None = None,
    structured_json: bool,
) -> str:
    """Render a `_mcp_broad_root_scan_refusal` message as a tool result.

    MCP has no exit codes (traps note, H3): a refusal is surfaced as a structured JSON
    field/error object, never an `exit(2)`-style abort -- this mirrors the CLI's refusal
    text (which already carries actionable remediation guidance) without terminating the
    server process.
    """
    if structured_json:
        payload: dict[str, Any] = {
            "pattern": pattern,
            "path": path,
            "total_matches": 0,
            "total_files": 0,
            "rendered_match_count": 0,
            "rendered_file_count": 0,
            "matches": [],
            "truncated": True,
            "result_incomplete": True,
            "incomplete_reason": message,
            "error": {
                "code": "broad_scan_refused",
                "message": message,
                "retryable": False,
            },
        }
        if lang is not None:
            payload["lang"] = lang
        return json.dumps(payload, indent=2)
    return f"tg: {message}"


@mcp.tool()  # type: ignore
def tg_rulesets() -> str:
    """Return metadata for built-in security and compliance rulesets."""
    return _inject_mcp_contract_fields(json.dumps(_build_rulesets_payload(), indent=2))


def _confine_write_path(candidate: str, anchor: Path, *, label: str) -> Path:
    """Resolve an MCP-supplied path and refuse anything outside ``anchor`` (round-4, round-6).

    Originally the write-path guard (ruleset baseline/suppressions, review-bundle output):
    those tools take a path straight from the (LLM/attacker-influenceable) tool call, and
    without confinement that is an arbitrary-file-write primitive. Round-6 reuses the same
    mechanism to confine READ paths on the audit/review-bundle tools (audit #7): an
    unconfined read is an arbitrary-file-read/exfil primitive -- the manifest/bundle/scan
    contents are echoed back into the JSON tool result, so any path the caller can name gets
    its bytes disclosed. Resolve the candidate (relative paths join the anchor), which also
    follows symlinks -- correct for confinement, since a symlink planted inside the anchor
    that points outside it must resolve to its real (outside) target to be caught -- and
    require the result to be the anchor itself or a descendant; raise ``ValueError``
    otherwise (fail closed). Callers MUST forward the resolved ``Path`` this returns, not the
    raw candidate string, so the downstream read/write sees the same anchor-validated
    location this check validated (closes the discard/TOCTOU class).
    """
    anchor_resolved = anchor.expanduser().resolve()
    raw = Path(candidate).expanduser()
    target = raw if raw.is_absolute() else (anchor_resolved / raw)
    resolved = target.resolve()
    if resolved != anchor_resolved and anchor_resolved not in resolved.parents:
        raise ValueError(f"{label} must stay within {anchor_resolved} (refused: {resolved})")
    return resolved


def _confine_read_path(candidate: str, anchor: Path, *, label: str) -> Path:
    """Resolve an MCP-supplied READ path and refuse anything outside ``anchor`` (round-7,
    audit #81 #1/#2).

    Read-labeled chokepoint for read-path MCP tool params. Round-6 (audit #7) already
    generalized ``_confine_write_path`` to cover read confinement for the audit-manifest /
    review-bundle family, because the confinement mechanism (resolve, then require the
    result to be the anchor or a descendant) is identical for reads and writes; this wrapper
    just gives the read side its own name so a NEW read-path param has an obvious, greppable
    chokepoint to route through -- so this class (an unconfined read-path param forwarded raw
    to a loader/reader = arbitrary-file-read/exfil primitive) can't recur one tool at a time.
    See ``_confine_write_path``'s docstring for the confinement semantics (symlink-following
    resolve, fail-closed ValueError, callers MUST forward the resolved ``Path`` this returns).
    """
    return _confine_write_path(candidate, anchor, label=label)


def _mcp_root() -> Path:
    """Return the confinement anchor for every MCP tool's PRIMARY path/root parameter.

    Round-8 (audit #95 gate must-fix): every symbol/session/search/rewrite/checkpoint tool
    took its scan/session root straight from the caller-supplied ``path``/``root`` argument
    with NO confinement at all -- only secondary params (baseline_path, manifest_path, ...)
    were anchored via `_confine_write_path`/`_confine_read_path`. Defaults to the server
    process's current working directory (the same anchor those secondary confinements
    already use), so the default-config behavior is unchanged. An operator running the MCP
    server against a repo other than cwd (a monorepo subtree, a fleet of repos) can move the
    anchor via the ``TG_MCP_ROOT`` environment variable -- this WIDENS or RELOCATES where
    reads/writes are permitted, it never disables confinement.

    Two fail-closed guards, both required by the gate:
    - An unset OR empty/whitespace-only ``TG_MCP_ROOT`` is treated as "not configured" and
      falls back to cwd, rather than letting ``Path("")`` resolve to the filesystem root
      (which would silently confine every tool call to "anywhere").
    - A configured override that does not resolve to a real, existing directory (typo,
      not-yet-mounted path, a file instead of a directory) is refused and falls back to cwd
      -- narrowing to the always-valid default is the safe failure mode; crashing the whole
      MCP server over one bad env var (or worse, silently confining to a non-existent path,
      which `_confine_read_path`'s `.resolve()` would still do without erroring) is not.
    """
    raw = os.environ.get("TG_MCP_ROOT", "").strip()
    if not raw:
        return Path.cwd()
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError:
        print(
            f"[tensor-grep-mcp] TG_MCP_ROOT={raw!r} could not be resolved; "
            "falling back to the current working directory.",
            file=sys.stderr,
        )
        return Path.cwd()
    if not resolved.is_dir():
        print(
            f"[tensor-grep-mcp] TG_MCP_ROOT={raw!r} is not an existing directory "
            f"(resolved: {resolved}); falling back to the current working directory.",
            file=sys.stderr,
        )
        return Path.cwd()
    return resolved


def _confine_mcp_path(candidate: str, *, label: str) -> Path:
    """Confine an MCP tool's PRIMARY path/root parameter to `_mcp_root()` (round-8, audit
    #95 gate must-fix #1/#2/#3).

    Thin wrapper over `_confine_read_path` anchored at `_mcp_root()` (instead of a
    per-tool-hardcoded `Path.cwd()`), so the one `TG_MCP_ROOT` override relocates the anchor
    of every tool confined THROUGH THIS HELPER together. Raises ``ValueError`` (fail closed)
    on an out-of-root candidate; callers MUST forward the resolved ``Path`` this returns
    (`str()`'d) as their new ``path``, and MUST do so as the very first operation in the
    tool body, BEFORE any secondary anchor (session_root, scan_root, policy_anchor, ...) is
    derived from ``path`` -- otherwise that secondary anchor still derives from the raw,
    unconfined candidate and the confinement is cosmetic (this exact bug was the gate's
    LIVE VULN finding on `tg_session_file_importers`'s `session_root`).

    ROUND-9 (audit #95 Part 2 / #102 fold-in): the round-6/7 residual set this docstring
    used to name as still anchored directly at `Path.cwd()` (tg_file_imports/importers
    `file`, tg_classify_logs `file_path`, the tg_audit_*/tg_review_bundle_* manifest+bundle
    params, tg_rewrite_apply `audit_manifest`) now ALSO route through `_mcp_root()` via
    `_confine_read_path`/`_confine_write_path` directly (they do not call this specific
    wrapper since they anchor to `_mcp_root()` without the PRIMARY-path semantics this
    function documents, but the anchor itself is the same `_mcp_root()` call) -- every
    confined param in this file now moves uniformly with `TG_MCP_ROOT`. See
    test_round8_residual_cwd_params_move_with_tg_mcp_root for the regression coverage.
    """
    return _confine_read_path(candidate, _mcp_root(), label=label)


# [SEC] audit #95 Part 2: bound the raw `inline_rules` string BEFORE it ever reaches
# yaml.safe_load. PyYAML's SafeLoader still resolves anchors/aliases (`&`/`*`); a small
# document can construct a "billion laughs"-style combinatorial blowup in memory even
# without executing arbitrary code, and the MCP surface accepts this string directly from an
# (LLM/attacker-influenceable) tool call rather than a human-typed CLI argv. This length cap
# is a cheap, unconditional first line of defense -- it does not fully close an anchor/alias
# expansion bomb (that needs a depth/complexity-limited loader), but it blunts the attack
# surface and matches the codebase's existing `_MAX_MCP_STDIO_MESSAGE_BYTES` precedent for
# bounding an untrusted MCP-supplied payload before it is parsed.
_MAX_INLINE_RULES_CHARS = 64 * 1024

# [SEC] Cap the NUMBER of inline rules, not just the total byte length. Each inline rule is scanned
# as a SEPARATE ast-grep invocation (~40 ms/rule), so an attacker-influenceable payload that stays
# well under _MAX_INLINE_RULES_CHARS can still drive an unbounded multi-minute scan fan-out (~1000
# tiny rules -> a >40 s hang; a ~33 KB payload is a multi-minute scan). The length cap admits ~4000
# minimal rules, so it is NOT the binding bound on fan-out -- this count cap is. Legitimate ad-hoc
# inline scanning uses a handful of rules; a large trusted rule set belongs in a named `ruleset=`
# pack, not inline. (audit #95 Part-2 re-gate: unbounded inline-rules scan fan-out DoS.)
_MAX_INLINE_RULES = 100


@mcp.tool()  # type: ignore
def tg_ruleset_scan(
    ruleset: str | None = None,
    inline_rules: str | None = None,
    path: str = ".",
    language: str | None = None,
    glob: str | None = None,
    file_type: str | None = None,
    max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
    baseline_path: str | None = None,
    write_baseline: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions: str | None = None,
    justification: str | None = None,
    include_evidence_snippets: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
) -> str:
    """
    Execute a built-in or inline-YAML ast-grep ruleset scan and return structured findings.

    This tool is read-only by default. Some optional parameters write files to disk
    when supplied: ``write_baseline`` and ``write_suppressions`` create or overwrite
    the file at the given path. Leave them unset for a pure read-only scan.

    Exactly one of ``ruleset`` or ``inline_rules`` is required.

    Args:
        ruleset: Built-in ruleset name to execute. Mutually exclusive with ``inline_rules``.
        inline_rules: Inline ast-grep rule YAML (one or more `---`-separated documents,
            each with `id`/`rule.pattern`/optional `language`/`severity`/`message`) to
            execute WITHOUT a built-in pack or any file I/O -- mirrors the CLI's
            ``--inline-rules``. Mutually exclusive with ``ruleset``. Bounded to
            64KiB to blunt a YAML anchor/alias expansion-bomb before it reaches the
            parser; fails closed (a structured ``invalid_input`` error, never a raw
            traceback) on invalid YAML or a language ast-grep does not support.
        path: Root path to scan.
        language: Optional language override for the ruleset, or the default language
            for any inline rule that does not specify its own.
        glob: Optional include/exclude glob for bounded scans.
        file_type: Optional extension/type filter for bounded scans.
        max_depth: Optional traversal depth limit for broad roots.
        allow_broad_generated_scan: Explicit opt-in for broad temp/cache/system roots.
        baseline_path: Optional path to an existing baseline JSON file. Read-only:
            findings present in the baseline are marked as known so only new
            findings are reported. Confined to the scan root (``path``); a baseline that
            legitimately lives outside the scan root must be copied in first (fail-closed,
            not a silent drop).
        write_baseline: Optional path to write a fresh baseline JSON snapshot of the
            current findings. SIDE EFFECT: creates or overwrites this file on disk.
        suppressions_path: Optional path to an existing suppressions JSON file. Read-only:
            matching findings are suppressed from the reported results. Confined to the
            scan root (``path``) like ``baseline_path``.
        write_suppressions: Optional path to write a suppressions JSON file derived from
            the current findings. SIDE EFFECT: creates or overwrites this file on disk;
            requires ``justification``.
        justification: Reason recorded alongside ``write_suppressions`` entries.
            Required when ``write_suppressions`` is supplied.
        include_evidence_snippets: When true, include bounded source snippets as
            evidence for each finding.
        max_evidence_snippets_per_file: Maximum evidence snippets to emit per file
            (evidence cap). Defaults to 1.
        max_evidence_snippet_chars: Maximum characters per evidence snippet
            (evidence cap). Defaults to 120.
    """
    try:
        # round-8 security (audit #95 gate must-fix #3, LIVE-VULN-adjacent): confine path to
        # the MCP root BEFORE root_dir/scan_root below derive anything from it. Both anchor
        # baseline_path/suppressions_path/write_baseline/write_suppressions confinement AND
        # the scan itself -- an unconfined path was a full arbitrary-directory scan/read
        # (and, via write_baseline/write_suppressions, write) primitive over the MCP surface.
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _ruleset_scan_error(
            str(exc),
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )

    # Mirrors main.py scan()'s mutual-exclusivity guard (`--rule`/`--inline-rules`/`--ruleset`)
    # narrowed to the two sources this MCP tool exposes today -- `--rule` (a single rule FILE)
    # and `--config` sgconfig are deliberately deferred (the latter does an unconfined
    # recursive rglob over ruleDirs/testDirs; confining only the top-level path is
    # insufficient, see _confine_mcp_path's sibling design doc).
    inline_source_count = sum(item is not None for item in (ruleset, inline_rules))
    if inline_source_count == 0:
        return _ruleset_scan_error(
            "Exactly one of ruleset or inline_rules is required.",
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )
    if inline_source_count > 1:
        return _ruleset_scan_error(
            "ruleset and inline_rules are mutually exclusive.",
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )

    if inline_rules is not None:
        # [SEC] bound BEFORE parsing -- see _MAX_INLINE_RULES_CHARS docstring.
        if len(inline_rules) > _MAX_INLINE_RULES_CHARS:
            return _ruleset_scan_error(
                f"inline_rules exceeds the {_MAX_INLINE_RULES_CHARS}-character limit "
                f"({len(inline_rules)} chars).",
                code="invalid_input",
                ruleset=ruleset,
                path=path,
            )
        try:
            rules = _load_inline_rule_specs(inline_rules, default_language=language)
        except ValueError as exc:
            return _ruleset_scan_error(str(exc), code="invalid_input", ruleset=ruleset, path=path)
        if not rules:
            return _ruleset_scan_error(
                "No valid inline rules were found.",
                code="invalid_input",
                ruleset=ruleset,
                path=path,
            )
        # [SEC] bound the scan fan-out -- each rule is a separate ast-grep pass; see
        # _MAX_INLINE_RULES. Reject a rule COUNT the length cap alone would admit into a
        # multi-minute scan.
        if len(rules) > _MAX_INLINE_RULES:
            return _ruleset_scan_error(
                f"inline_rules has {len(rules)} rules, exceeding the {_MAX_INLINE_RULES}-rule "
                "limit (each rule is a separate scan pass). Use a named ruleset or split the scan.",
                code="invalid_input",
                ruleset=ruleset,
                path=path,
            )
        try:
            inferred_language = (
                normalize_ast_language(language) if language else str(rules[0]["language"])
            )
        except ValueError as exc:
            # [SEC] normalize_ast_language raises ValueError on an unsupported `language` override.
            # A rule carrying its OWN valid `language:` short-circuits the loader's guarded
            # default_language normalization (mcp_server.py:1986-1989), so an invalid top-level
            # `language=` override reaches here UNGUARDED -- a raw traceback on a valid-but-bogus
            # payload, violating the tool's fail-closed contract. (audit #95 Part-2 round-5 gate:
            # demonstrated with language="zzznotalang" + a rule that sets its own language.)
            return _ruleset_scan_error(str(exc), code="invalid_input", ruleset=ruleset, path=path)
        project_cfg: dict[str, object] = {
            "config_path": "inline-rules",
            "root_dir": Path(path).expanduser().resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": inferred_language,
        }
        scan_ruleset_name: str | None = None
        scan_routing_reason = "ast-inline-rules-scan"
    else:
        try:
            ruleset_meta, rules = resolve_rule_pack(cast(str, ruleset), language)
        except ValueError as exc:
            return _ruleset_scan_error(
                str(exc),
                code="invalid_input",
                ruleset=ruleset,
                path=path,
            )
        project_cfg = {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": Path(path).expanduser().resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        }
        scan_ruleset_name = ruleset_meta["name"]
        scan_routing_reason = "builtin-ruleset-scan"

    # round-4/5 security: confine the two write paths to the scan root before any scan/write —
    # unconfined, they are an arbitrary-file-write primitive reachable from any MCP client.
    # round-5: consume the RESOLVED absolute path (not the raw candidate) below so the
    # downstream writer (_run_ast_scan_payload -> ... re-resolves once) sees the same
    # anchor-validated location this check validated (closes the discard/TOCTOU class).
    scan_root = Path(path).expanduser().resolve()
    try:
        if write_baseline is not None:
            write_baseline = str(
                _confine_write_path(write_baseline, scan_root, label="write_baseline")
            )
        if write_suppressions is not None:
            write_suppressions = str(
                _confine_write_path(write_suppressions, scan_root, label="write_suppressions")
            )
        # round-7 security (audit #81 #2): baseline_path/suppressions_path are READS that were
        # forwarded to the loader unconfined -- a file-existence + JSON-schema read-oracle over
        # any path reachable from any MCP client, even though the two WRITE siblings just above
        # were already confined (round-4/5). Anchor to the same scan_root so a legitimate
        # baseline/suppressions file for THIS scan (relative or in-root absolute) keeps working.
        if baseline_path is not None:
            baseline_path = str(_confine_read_path(baseline_path, scan_root, label="baseline_path"))
        if suppressions_path is not None:
            suppressions_path = str(
                _confine_read_path(suppressions_path, scan_root, label="suppressions_path")
            )
    except ValueError as exc:
        return _ruleset_scan_error(str(exc), code="invalid_input", ruleset=ruleset, path=path)
    try:
        payload = _run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason=scan_routing_reason,
            ruleset_name=scan_ruleset_name,
            scan_globs=[glob] if glob else None,
            scan_types=[file_type] if file_type else None,
            scan_max_depth=max_depth,
            allow_broad_generated_scan=allow_broad_generated_scan,
            baseline_path=baseline_path,
            write_baseline_path=write_baseline,
            suppressions_path=suppressions_path,
            write_suppressions_path=write_suppressions,
            suppression_justification=justification,
            include_evidence_snippets=include_evidence_snippets,
            max_evidence_snippets_per_file=max_evidence_snippets_per_file,
            max_evidence_snippet_chars=max_evidence_snippet_chars,
        )
    except BroadScanRefusedError as exc:
        return _ruleset_scan_error(
            str(exc),
            code="broad_scan_refused",
            ruleset=ruleset,
            path=path,
        )
    except ValueError as exc:
        return _ruleset_scan_error(
            str(exc),
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )
    except ConfigurationError as exc:
        # [SEC] ast-grep toolchain not available. ast-grep is NOT a declared dependency, so a
        # DEFAULT `pip install tensor-grep` has no ast-grep binary -- and on that install a trivial
        # one-line inline rule reaches _select_ast_backend_for_pattern, which raises
        # ConfigurationError (a RuntimeError, NOT a ValueError/BackendExecutionError). It was
        # escaping as a RAW TRACEBACK on the common default-install path. Surface it structured.
        # (audit #95 Part-2 round-4 gate; mirrors tg_ast_search's ConfigurationError handling.)
        return _ruleset_scan_error(
            str(exc),
            code="unavailable",
            ruleset=ruleset,
            path=path,
        )
    except OSError as exc:
        # [SEC] a caller-supplied baseline_path/suppressions_path that is unreadable (a directory,
        # permission-denied, a race-deleted file) makes _load_ruleset_baseline/_load_ruleset_
        # suppressions' read_text raise OSError/PermissionError/IsADirectoryError (NOT a
        # ValueError) -- was a raw traceback. Fail closed. (audit #95 Part-2 round-4 gate.)
        return _ruleset_scan_error(
            f"unreadable scan path: {exc}",
            code="invalid_input",
            ruleset=ruleset,
            path=path,
        )
    except RuntimeError as exc:
        # [SEC] Backend Fail-Closed backstop: BackendExecutionError (e.g. ast-grep failing on an
        # over-long pattern, WinError 206) AND any OTHER runtime-fault sibling must be a structured
        # error, never a raw traceback. Broadened from a BackendExecutionError-only catch to the
        # whole RuntimeError class, mirroring the CLI twin's `except (ValueError, RuntimeError)`
        # (main.py). Logic bugs (KeyError/TypeError/AttributeError) are NOT RuntimeError and still
        # surface. (audit #95 Part-2 round-4 gate: BLOCK on the incomplete fault class.)
        return _ruleset_scan_error(
            f"scan backend failed: {exc}",
            code="backend_error",
            ruleset=ruleset,
            path=path,
        )
    return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_repo_map(path: str = ".", max_repo_files: int | None = 512) -> str:
    """
    Return a deterministic repository inventory for agent context selection.

    Args:
        path: File or directory to inventory.
        max_repo_files: Maximum repo files to scan before returning. Defaults to 512.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- unconfined it is an arbitrary-directory-read primitive over the MCP
    # protocol (systemic finding: every path/root-taking tool except the file-scoped
    # tg_file_imports/tg_classify_logs lacked this).
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="repo-map",
            include_schema_version=False,
        )
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        from tensor_grep.cli.repo_map import DEFAULT_AGENT_REPO_MAP_LIMIT

        effective_max_repo_files = max_repo_files or DEFAULT_AGENT_REPO_MAP_LIMIT
        return _inject_mcp_contract_fields(
            json.dumps(
                build_repo_map(path, max_repo_files=effective_max_repo_files),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="repo-map",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="repo-map",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_orient(
    path: str = ".",
    max_tokens: int = 3000,
    max_central_files: int = 10,
    ignore: list[str] | None = None,
) -> str:
    """
    Call FIRST for orientation: return a one-call codebase orientation capsule.

    Mirrors `tg orient` (build_orient_capsule_json): the most central files by import-graph
    centrality, heuristically detected entry points, a symbol map, and bounded AST-boundary
    source snippets within a token budget. Pure-CPU, no API key, no GPU. Prefer this before
    tg_repo_map/tg_context_pack/tg_agent_capsule when orienting on an unfamiliar repo for the
    first time -- it answers "what is this codebase and where do I start" in one call.

    Args:
        path: File or directory to orient on. Confined to the MCP server root (cwd, or
            TG_MCP_ROOT if set); a path outside it is refused.
        max_tokens: Snippet token budget for the capsule. Defaults to 3000.
        max_central_files: Number of top central files to surface. Defaults to 10.
        ignore: Glob(s) to exclude from the centrality ranking (basename or repo-relative
            path), e.g. ["seo/**", "core/skills/**"]. Excludes vendor/skill CODE trees that
            otherwise rank as "central" on a harness repo.
    """
    # round-9 security (audit #95 Part 2): confine the primary path/root param to the MCP
    # root before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="orient",
            include_schema_version=False,
        )
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            build_orient_capsule_json(
                path,
                max_tokens=max_tokens,
                max_central_files=max_central_files,
                ignore=tuple(ignore or ()),
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        # Mirrors the CLI `orient` command's except clause (main.py) -- a bad path or
        # unresolvable root must return a clean structured error, never a raw traceback.
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="orient",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)
    except Exception as exc:  # propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="orient",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = _sanitized_tool_error("tg_orient", exc)
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_doctor(
    path: str = ".",
    config: str | None = "sgconfig.yml",
    with_lsp: bool = True,
) -> str:
    """
    Return system, GPU, cache, AST, daemon, shell-escaping, and LSP-provider diagnostics.

    Mirrors `tg doctor` (_build_doctor_payload). Provider availability
    (lsp_provider_items/lsp_providers) is not navigation proof -- inspect health_status/
    health_check before trusting an LSP-confirmed evidence label.

    Args:
        path: Workspace root to inspect. Confined to the MCP server root (cwd, or
            TG_MCP_ROOT if set); a path outside it is refused.
        config: Path to an ast-grep root config, resolved relative to path when not
            absolute. Confined to the (already-confined) path; a config that legitimately
            lives outside path must be copied in first (fail-closed, not a silent drop).
            Defaults to "sgconfig.yml".
        with_lsp: Include external LSP provider diagnostics. Defaults to true.
    """
    # round-9 security (audit #95 Part 2): confine the primary path/root param to the MCP
    # root before any probe -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="Doctor",
            routing_reason="doctor",
            include_schema_version=False,
        )
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    # New hardening (beyond the design's literal "wrap it" ask): `config` is a SECONDARY
    # param that `_build_doctor_payload` uses to relocate its own `root` (config's resolved
    # parent directory) for every downstream diagnostic probe -- unconfined, a caller could
    # point every probe at an arbitrary directory via `config=/some/other/place/x.yml`, the
    # same "secondary anchor derived from an unconfined param" class the #95 gate's MUST-FIX
    # #3 closed for tg_session_file_importers. Confine to the already-confined `path`,
    # mirroring tg_ruleset_scan's baseline_path/suppressions_path anchor-to-scan-root
    # pattern. `if config:` (not `is not None`) matches _build_doctor_payload's OWN
    # truthiness check so an empty string is treated identically to "not provided" on both
    # sides -- confining "" would otherwise turn a no-op default into a real (and wrong)
    # root-parent relocation.
    if config:
        try:
            config = str(_confine_read_path(config, Path(path), label="config"))
        except ValueError as exc:
            payload = _envelope_base(
                routing_backend="Doctor",
                routing_reason="doctor",
                include_schema_version=False,
            )
            payload["path"] = path
            payload["error"] = {"code": "invalid_input", "message": str(exc)}
            return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(_build_doctor_payload(path, config=config, with_lsp=with_lsp), indent=2)
        )
    except Exception as exc:  # propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="Doctor",
            routing_reason="doctor",
            include_schema_version=False,
        )
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = _sanitized_tool_error("tg_doctor", exc)
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_context_pack(
    query: str, path: str = ".", max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS
) -> str:
    """
    Return a ranked repository context pack for edit planning.

    Args:
        query: Query text used to rank relevant files, symbols, and tests.
        path: File or directory to inventory.
        max_tokens: Bound the pack for prompt injection (default ~16000; 0/None = unbounded).
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-pack",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(build_context_pack(query, path, max_tokens=max_tokens), indent=2)
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-pack",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-pack",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_edit_plan(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    provider: str = "native",
) -> str:
    """
    Return a machine-readable edit-planning bundle without rendered source text.

    Args:
        query: Query text used to rank edit targets.
        path: File or directory to inventory.
        max_files: Maximum files to include in the plan.
        max_repo_files: Maximum repository files to scan before ranking edit targets.
        max_sources: Maximum related source/span records to retain.
        max_tokens: Accepted for command-surface parity; no rendered source text is emitted.
        max_symbols: Maximum ranked symbols to retain.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
    """
    from tensor_grep.cli.repo_map import build_context_edit_plan

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-edit-plan",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_context_edit_plan(
                    query,
                    path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_symbols=max_symbols,
                    semantic_provider=provider,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-edit-plan",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-edit-plan",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_context_render(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    provider: str = "native",
    profile: bool = False,
) -> str:
    """
    Return a prompt-ready repository context bundle for edit planning.

    Args:
        query: Query text used to rank and render repo context.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before ranking context.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-render",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_context_render(
                    query,
                    path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    max_tokens=max_tokens,
                    model=model,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    semantic_provider=provider,
                    profile=profile,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-render",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="context-render",
            include_schema_version=False,
        )
        payload["query"] = query
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_agent_capsule(
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    model: str | None = None,
    provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
) -> str:
    """
    Return an Actionable Context Capsule for agent edit planning.

    Args:
        query: Natural-language task or symbol query.
        path: File or directory to inventory.
        max_files: Maximum ranked files to consider.
        max_sources: Maximum source snippets to include.
        max_tokens: Token budget for bounded capsule output.
        max_repo_files: Maximum repository files to scan.
        model: Optional model name used for token estimation.
        provider: Semantic provider for primary target proof: native, lsp, or hybrid.
        gpu_device_ids: Optional selected GPU IDs for native route evidence.
        gpu_timeout_s: Maximum seconds for each opt-in GPU evidence command.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _agent_capsule_error(str(exc), code="invalid_input", query=query, path=path)

    if not Path(path).expanduser().exists():
        return _agent_capsule_error(
            f"Path not found: {Path(path).expanduser().resolve()}",
            code="invalid_input",
            query=query,
            path=path,
        )

    try:
        from tensor_grep.cli.agent_capsule import build_agent_capsule

        return _inject_mcp_contract_fields(
            json.dumps(
                build_agent_capsule(
                    query,
                    path,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_repo_files=max_repo_files,
                    model=model,
                    semantic_provider=provider,
                    gpu_device_ids=gpu_device_ids,
                    gpu_timeout_s=gpu_timeout_s,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        return _agent_capsule_error(
            f"Path not found: {Path(path).expanduser().resolve()}",
            code="invalid_input",
            query=query,
            path=path,
        )
    except ValueError as exc:
        return _agent_capsule_error(
            str(exc),
            code="invalid_input",
            query=query,
            path=path,
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _agent_capsule_error(
            str(exc),
            code="internal_error",
            query=query,
            path=path,
        )


@mcp.tool()  # type: ignore
def tg_session_edit_plan(
    session_id: str,
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session edit-planning bundle without rendered source text.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank edit targets.
        path: File or directory rooted at the session scope.
        max_files: Maximum files to include in the plan.
        max_repo_files: Maximum repository files to scan before ranking edit targets.
        max_sources: Maximum related source/span records to retain.
        max_tokens: Accepted for command-surface parity; no rendered source text is emitted.
        max_symbols: Maximum ranked symbols to retain.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_context_edit_plan

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
            query=query,
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        return json.dumps(
            session_context_edit_plan(
                session_id,
                query,
                path,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_tokens=max_tokens,
                max_symbols=max_symbols,
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
            query=query,
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
            query=query,
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={"query": query, "max_files": max_files, "max_symbols": max_symbols},
            query=query,
        )


@mcp.tool()  # type: ignore
def tg_session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    max_files: int = 3,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a prompt-ready repository context bundle derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank and render repo context.
        path: File or directory rooted at the session scope.
        max_files: Maximum files to include in the render bundle.
        max_repo_files: Maximum cached repo files to score before rendering.
        max_sources: Maximum exact source blocks to include.
        max_symbols_per_file: Maximum summary symbols to include per file.
        max_render_chars: Maximum characters to emit in rendered_context.
        optimize_context: Strip blank lines and comment-only lines from rendered source blocks.
        render_profile: Render profile to use: full, compact, or llm.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_context_render

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query, "render_profile": render_profile},
            query=query,
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        return json.dumps(
            session_context_render(
                session_id,
                query,
                path,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                max_tokens=max_tokens,
                model=model,
                optimize_context=optimize_context,
                render_profile=render_profile,
                profile=profile,
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query, "render_profile": render_profile},
            query=query,
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={"query": query, "render_profile": render_profile},
            query=query,
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={"query": query, "render_profile": render_profile},
            query=query,
        )


@mcp.tool()  # type: ignore
def tg_session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
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

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        return json.dumps(
            session_blast_radius(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={"symbol": symbol, "max_depth": max(0, int(max_depth))},
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )


@mcp.tool()  # type: ignore
def tg_session_file_importers(
    session_id: str,
    file: str,
    path: str = ".",
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session (zero-reparse) list of the files that import FILE.

    Args:
        session_id: Session ID to query.
        file: File to find importers of. Confined to the session root (``path``); a file
            that legitimately lives outside it must be copied in first (fail-closed, not a
            silent drop).
        path: File or directory rooted at the session scope.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_file_importers

    # round-8 security (audit #95 gate must-fix #3, LIVE VULN): confine path to the MCP root
    # BEFORE session_root below derives from it. Previously session_root = Path(path).resolve()
    # used the RAW caller-supplied path with NO confinement at all, so path="/etc" made
    # session_root="/etc" and the "confine file to session_root" check just below then let
    # file="/etc/passwd" straight through -- an arbitrary-directory-read primitive reachable
    # from any MCP client today. Confining path here (rather than re-anchoring file's
    # confinement below to cwd) is deliberate: it keeps a legitimate relative `file` working
    # when session_root != cwd -- see the comment on the file confinement immediately below.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"file": file},
            file=file,
        )

    # round-7 security (audit #81 Opus gate #2 follow-up): confine file to the session root
    # (path) before any read, same class/rationale as tg_file_imports/tg_file_importers above.
    # Anchored to the session root rather than cwd because that is what session_file_importers
    # itself resolves a relative `file` against (build_file_importers_from_map joins it onto
    # the session's own repo_map root, not the MCP server process cwd) -- confining to cwd
    # here would refuse a legitimate relative `file` whenever the session root differs from cwd.
    session_root = Path(path).expanduser().resolve()
    if not session_root.is_dir():
        session_root = session_root.parent
    try:
        file = str(_confine_read_path(file, session_root, label="file"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"file": file},
            file=file,
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        return json.dumps(
            session_file_importers(
                session_id,
                file,
                path,
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"file": file},
            file=file,
        )
    except FileNotFoundError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"file": file},
            file=file,
        )
    except Exception as exc:  # propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={"file": file},
            file=file,
        )


@mcp.tool()  # type: ignore
def tg_symbol_blast_radius_plan(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return a machine-readable blast-radius planning bundle without rendered source text.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the plan.
        max_symbols: Maximum ranked symbols to retain.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    from tensor_grep.cli.repo_map import build_symbol_blast_radius_plan

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-plan",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return json.dumps(
            build_symbol_blast_radius_plan(
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_symbols=max_symbols,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
            ),
            indent=2,
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-plan",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-plan",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
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
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
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

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "render_profile": render_profile,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
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
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "render_profile": render_profile,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "render_profile": render_profile,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "render_profile": render_profile,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )


@mcp.tool()  # type: ignore
def tg_session_blast_radius_plan(
    session_id: str,
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
) -> str:
    """
    Return a cached-session blast-radius planning bundle without rendered source text.

    Args:
        session_id: Session ID to query.
        symbol: Exact symbol name to resolve.
        path: File or directory rooted at the session scope.
        max_depth: Maximum reverse-import depth to include.
        max_files: Maximum files to include in the plan.
        max_symbols: Maximum ranked symbols to retain.
    """
    from tensor_grep.cli.session_store import SessionStaleError, session_blast_radius_plan

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "max_files": max_files,
                "max_symbols": max_symbols,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        return json.dumps(
            session_blast_radius_plan(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_symbols=max_symbols,
                refresh_on_stale=effective_refresh,
            ),
            indent=2,
        )
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "max_files": max_files,
                "max_symbols": max_symbols,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "max_files": max_files,
                "max_symbols": max_symbols,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="internal_error",
            message=str(exc),
            detail={
                "symbol": symbol,
                "max_depth": max(0, int(max_depth)),
                "max_files": max_files,
                "max_symbols": max_symbols,
            },
            symbol=symbol,
            max_depth=max(0, int(max_depth)),
        )


@mcp.tool()  # type: ignore
def tg_symbol_defs(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return exact definition locations for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-defs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_defs(
                    symbol, path, semantic_provider=provider, max_repo_files=max_repo_files
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-defs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # C4: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-defs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_source(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Return exact source blocks for a symbol definition.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-source",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_source(
                    symbol, path, semantic_provider=provider, max_repo_files=max_repo_files
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-source",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # C4: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-source",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_impact(
    symbol: str, path: str = ".", provider: str = "native", deadline: float | None = None
) -> str:
    """
    Return likely impacted files and tests for a symbol change.

    Args:
        symbol: Exact symbol name to evaluate.
        path: File or directory to inventory.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-impact",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_impact(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=_DEFAULT_MCP_REPO_SCAN_LIMIT,
                    deadline_seconds=deadline,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-impact",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # C4: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-impact",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_refs(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return Python-first symbol references across the inventory root.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-refs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_refs(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                    deadline_seconds=deadline,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-refs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # C4: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-refs",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_symbol_callers(
    symbol: str,
    path: str = ".",
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return Python-first symbol call sites and likely impacted tests.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-callers",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_callers(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                    deadline_seconds=deadline,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-callers",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # C4: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-callers",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_file_imports(file: str) -> str:
    """
    Return what a single FILE imports, resolved to target files where possible.

    The scoped forward file-dependency primitive (#74): O(1) -- parses exactly one file, no
    repo scan. Far cheaper than a whole-repo `tg_map` for a single file's dependency edges.

    Args:
        file: File to inspect for its own imports. Confined to the project root (cwd); a
            file that legitimately lives outside the project must be copied in first
            (fail-closed, not a silent drop).
    """
    # round-7 security (audit #81 Opus gate #2 follow-up): confine file to the project root
    # (cwd) before any read -- unconfined it is a file-existence + import-string read-oracle
    # over any path reachable from any MCP client (build_file_imports below stats the file and
    # echoes its resolved path / import list back in the JSON result), same class as
    # tg_classify_logs.file_path above. Forward the RESOLVED path so build_file_imports sees
    # the same anchor-validated location this check validated.
    try:
        file = str(_confine_read_path(file, _mcp_root(), label="file"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-imports",
            include_schema_version=False,
        )
        payload["file"] = file
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)
    try:
        return _inject_mcp_contract_fields(json.dumps(build_file_imports(file), indent=2))
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-imports",
            include_schema_version=False,
        )
        payload["file"] = str(Path(file).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"File not found: {Path(file).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-imports",
            include_schema_version=False,
        )
        payload["file"] = str(Path(file).expanduser())
        payload["error"] = _sanitized_tool_error("tg_file_imports", exc)
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_file_importers(
    file: str,
    path: str = ".",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return the files that import a single FILE (the reverse #74 file-dependency primitive).

    Prefilters candidate importers via the repo's import-alias graph, then re-parses and
    CONFIRMS each candidate against FILE before reporting it as an edge.

    Args:
        file: File to find importers of. Confined to the project root (cwd); a file that
            legitimately lives outside the project must be copied in first (fail-closed,
            not a silent drop).
        path: Root to scan for importers.
        max_repo_files: Maximum repository files to scan before resolving importers.
        deadline: Optional wall-clock budget in seconds for the underlying repo scan. When
            exceeded, the scan stops and returns a flagged partial result instead of running
            unbounded.
    """
    # round-7 security (audit #81 Opus gate #2 follow-up): confine file to the project root
    # (cwd) before any read, same class/rationale as tg_file_imports above.
    try:
        file = str(_confine_read_path(file, _mcp_root(), label="file"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-importers",
            include_schema_version=False,
        )
        payload["file"] = file
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    # round-8 security (audit #95 gate): confine the secondary root param to the MCP root too
    # -- unconfined it is an arbitrary-directory-read primitive over the MCP protocol (the
    # design's proven example: `path` here resolved raw).
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-importers",
            include_schema_version=False,
        )
        payload["file"] = file
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_file_importers(
                    file, path, max_repo_files=max_repo_files, deadline_seconds=deadline
                ),
                indent=2,
            )
        )
    except FileNotFoundError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-importers",
            include_schema_version=False,
        )
        payload["file"] = str(Path(file).expanduser())
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": str(exc),
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="file-importers",
            include_schema_version=False,
        )
        payload["file"] = str(Path(file).expanduser())
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = _sanitized_tool_error("tg_file_importers", exc)
        return json.dumps(payload, indent=2)


@mcp.tool()
def tg_symbol_blast_radius(
    symbol: str,
    path: str = ".",
    max_depth: int = 3,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    deadline: float | None = None,
) -> str:
    """
    Return exact callers plus a transitive file/test blast radius for a symbol.

    Args:
        symbol: Exact symbol name to resolve.
        path: File or directory to inventory.
        max_depth: Maximum reverse-import depth to include.
        max_repo_files: Maximum repository files to scan before resolving the symbol.
        deadline: Optional wall-clock budget in seconds for the underlying graph traversal.
            When exceeded, the scan stops and returns a flagged partial result instead of
            running unbounded.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                build_symbol_blast_radius(
                    symbol,
                    path,
                    max_depth=max_depth,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                    deadline_seconds=deadline,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
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
    profile: bool = False,
    provider: str = "native",
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
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
        max_repo_files: Maximum repository files to scan before resolving the symbol.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-render",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = path
        payload["error"] = {"code": "invalid_input", "message": str(exc)}
        return json.dumps(payload, indent=2)

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
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
                    profile=profile,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                ),
                indent=2,
            )
        )
    except FileNotFoundError:
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-render",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "invalid_input",
            "message": f"Path not found: {Path(path).expanduser().resolve()}",
        }
        return json.dumps(payload, indent=2)
    except Exception as exc:  # M11: propagate as structured error, never a raw exception
        payload = _envelope_base(
            routing_backend="RepoMap",
            routing_reason="symbol-blast-radius-render",
            include_schema_version=False,
        )
        payload["symbol"] = symbol
        payload["max_depth"] = max(0, int(max_depth))
        payload["path"] = str(Path(path).expanduser())
        payload["error"] = {
            "code": "internal_error",
            "message": str(exc),
            "retryable": False,
        }
        return json.dumps(payload, indent=2)


@mcp.tool()  # type: ignore
def tg_search(
    pattern: str | None = None,
    path: str = ".",
    case_sensitive: bool = False,
    ignore_case: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    context: int | None = None,
    max_count: int | None = None,
    max_results: int | None = None,
    max_files: int | None = None,
    count_matches: bool = False,
    glob: str | None = None,
    type_filter: str | None = None,
    query: str | None = None,
    structured_json: bool = True,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    rank: bool = False,
    semantic: bool = False,
) -> str:
    """
    Search files for a regex pattern, with GPU acceleration when applicable.

    Args:
        pattern: A regular expression or exact string used for searching.
        query: Alias for pattern, accepted for agent callers that use query-shaped tools.
        path: A file or directory to search. Defaults to current directory.
        case_sensitive: Execute the search case sensitively.
        ignore_case: Search case insensitively (-i).
        fixed_strings: Treat pattern as a literal string instead of regex (-F).
        word_regexp: Only show matches surrounded by word boundaries (-w).
        context: Show NUM lines before and after each match (-C).
        max_count: Limit the number of matching lines per file (-m).
        max_results: Maximum materialized result rows to return. Defaults to 150.
        max_files: Maximum files to render. Defaults to 15.
        count_matches: Just count the matches using ultra-fast Rust backend (-c).
        glob: Include/exclude files matching glob (e.g. '*.py').
        type_filter: Only search files matching TYPE (e.g. 'py', 'js').
        structured_json: Return bounded structured JSON (default true). Set to false for
            plain-text output.
        max_repo_files: Maximum files the directory walk searches when the backend is not
            ripgrep (rg absent / GPU / hybrid / python-regex). Ignored when RipgrepBackend
            handles the whole-path search natively. Protects against an unscoped full-repo
            per-file search loop.
        rank: Re-rank results by BM25 lexical relevance to the query terms instead of grep
            order (pure-CPU ranking; no API key, no model download).
        semantic: Re-rank results by a hybrid of BM25 + local CPU dense-embedding relevance
            (RRF fusion), instead of grep order. No API key, no GPU. Requires the `semantic`
            extra and a fetched model; falls back to BM25-only (visibly, never silently, via
            `rank_fallback_reason`) when either is missing. Takes priority over `rank` when
            both are set.
    """
    search_pattern = pattern or query
    if not search_pattern:
        return "Search failed: either pattern or query is required."

    # Bug #88: capture the "was path left at its default" signal from the RAW caller-supplied
    # value BEFORE confinement below reassigns `path` to its confined (absolute) form -- once
    # reassigned, `path == "."` would always read False and silently defeat the large-root/
    # vendored-root refusal guard's paths_defaulted logic for every default-path call.
    paths_defaulted = path == "."

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        if structured_json:
            payload = {
                "pattern": search_pattern,
                "path": path,
                "total_matches": 0,
                "total_files": 0,
                "rendered_match_count": 0,
                "rendered_file_count": 0,
                "matches": [],
                "truncated": False,
                "result_incomplete": True,
                "incomplete_reason": str(exc),
                "error": {"code": "invalid_input", "message": str(exc)},
            }
            return json.dumps(payload, indent=2)
        return f"Search failed: {exc}"

    rendered_file_limit = max(0, max_files if max_files is not None else 15)
    rendered_result_limit = max(0, max_results if max_results is not None else 150)
    normalized_max_repo_files = max(1, int(max_repo_files))
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
    selected_backend_name = getattr(pipeline, "selected_backend_name", backend.__class__.__name__)
    selected_backend_reason = getattr(pipeline, "selected_backend_reason", "unknown")

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = selected_backend_name
    all_results.routing_reason = selected_backend_reason
    all_results.routing_gpu_device_ids = list(
        getattr(pipeline, "selected_gpu_device_ids", []) or []
    )
    all_results.routing_gpu_chunk_plan_mb = list(
        getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or []
    )
    all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
    scan_limit_payload: dict[str, Any] | None = None
    try:
        if isinstance(backend, RipgrepBackend):
            all_results = backend.search(path, search_pattern, config=config)
            all_results.routing_backend = all_results.routing_backend or selected_backend_name
            all_results.routing_reason = all_results.routing_reason or selected_backend_reason
        else:
            # H3 (Fable MCP-surface audit): before PR #400's fix landed here, this walk had
            # NO per-file wall-clock deadline, no BackendExecutionError fallback, and no
            # broad/vendored/large-root refusal -- an unscoped root could hang, and a mid-walk
            # backend fault fell through to the outer `except Exception` below and discarded
            # every match already collected. All three are now ported from the CLI (imported,
            # not reimplemented) so the two surfaces can't drift again.
            refusal_message, scanner, walker = _mcp_broad_root_scan_refusal(
                path,
                config,
                normalized_max_repo_files=normalized_max_repo_files,
                check_large_root=True,
                paths_defaulted=paths_defaulted,
            )
            if refusal_message is not None:
                return _broad_root_scan_refusal_result(
                    refusal_message,
                    pattern=search_pattern,
                    path=path,
                    structured_json=structured_json,
                )
            files_scanned = 0
            scan_capped = False
            native_walk_deadline = compute_native_walk_deadline()
            for current_file in walker:
                if files_scanned >= normalized_max_repo_files:
                    scan_capped = True
                    break
                if native_walk_deadline_exceeded(native_walk_deadline):
                    scan_capped = True
                    all_results.result_incomplete = True
                    all_results.incomplete_reason = (
                        "native search exceeded the wall-clock deadline and was stopped; "
                        "returning partial results. Scope the search to a smaller path, or "
                        "lower max_repo_files."
                    )
                    break
                try:
                    result = backend.search(current_file, search_pattern, config=config)
                except BackendExecutionError as exc:
                    # A native backend failed at runtime; retry on the always-available CPU
                    # backend so the search returns correct partial results instead of
                    # silently discarding everything collected so far (audit B2/I1, ported).
                    result = _search_with_cpu_fallback(current_file, search_pattern, config, exc)
                files_scanned += 1
                all_results.matches.extend(result.matches)
                all_results.matched_file_paths.extend(result.matched_file_paths)
                _merge_count_metadata(all_results, result)
                all_results.total_matches += result.total_matches
                if result.total_files > 0 or result.total_matches > 0:
                    all_results.total_files += 1
                _merge_runtime_routing(all_results, result)
            # The 200k-entry DirectoryScanner traversal budget (Q14) is a separate,
            # coarser defensive cap than max_repo_files -- it can trip first and
            # truncate the walk below max_repo_files without ever hitting the
            # per-file counter above, so OR it into possibly_truncated too. Coerce to a
            # real bool: a mocked scanner in a test can auto-vivify a truthy non-bool.
            scan_capped = scan_capped or bool(getattr(scanner, "scan_truncated", False))
            scan_limit_payload = {
                "max_repo_files": normalized_max_repo_files,
                "scanned_files": files_scanned,
                "possibly_truncated": scan_capped,
            }

        _apply_selected_gpu_defaults(
            all_results=all_results,
            selected_backend_name=selected_backend_name,
            selected_backend_reason=selected_backend_reason,
        )
        _finalize_aggregate_result(all_results)

        # audit #95 Part 2: mirror main.py search_command's --rank/--semantic post-processing
        # (`if config.semantic_rank: ... elif config.rank_bm25 and all_results.matches:`) so an
        # MCP agent caller gets the same BM25/hybrid-semantic relevance reordering as the CLI.
        # Applied once here, before the empty/count/full-result branches below, so every render
        # path sees the same reranked all_results -- matches main.py's ordering (rerank runs
        # before any output-mode branching, not duplicated per branch).
        if semantic:
            if all_results.matches:
                try:
                    all_results = _apply_semantic_rerank(all_results, search_pattern)
                except BackendExecutionError as exc:
                    # Backend Fail-Closed Contract boundary (mirrors main.py's search_command):
                    # _apply_semantic_rerank deliberately does NOT catch a genuine dense-backend
                    # fault (e.g. a corrupt model directory) -- it must surface here as a
                    # distinguishable structured error, never fall through to the generic
                    # internal_error catch-all at the bottom of this tool, which would lose the
                    # fail-closed signal (an agent needs to tell "the backend broke" apart from
                    # an ordinary internal_error).
                    if structured_json:
                        error_payload = {
                            "pattern": search_pattern,
                            "path": path,
                            "error": {
                                "code": "semantic_backend_error",
                                "message": str(exc),
                                "retryable": False,
                            },
                        }
                        return json.dumps(error_payload, indent=2)
                    return f"Search failed: semantic backend error: {exc}"
            else:
                # F16 parity (main.py _set_semantic_rank_fallback_reason): probe dense-leg
                # availability even on a 0-match search so rank_fallback_reason is set whenever
                # the leg is unavailable, regardless of match count.
                _set_semantic_rank_fallback_reason(all_results)
        elif rank and all_results.matches:
            from tensor_grep.core.reranker import rerank_by_bm25

            all_results = rerank_by_bm25(
                all_results, search_pattern, all_results.matched_file_paths
            )

        empty_scan_capped = bool(scan_limit_payload and scan_limit_payload["possibly_truncated"])
        if all_results.is_empty:
            if structured_json:
                payload = {
                    "pattern": search_pattern,
                    "path": path,
                    "total_matches": 0,
                    "total_files": all_results.total_files,
                    "rendered_match_count": 0,
                    "rendered_file_count": 0,
                    "matches": [],
                    "truncated": empty_scan_capped,
                    "omitted_matches": 0,
                    "omitted_files": 0,
                    # Partial results (rg exit 2 soft error, or a mid-walk deadline/fault) --
                    # top-level so an agent caller can't read a truncated result as complete.
                    "result_incomplete": all_results.result_incomplete,
                    "incomplete_reason": all_results.incomplete_reason,
                    "routing": _routing_payload(all_results),
                }
                if scan_limit_payload is not None:
                    payload["scan_limit"] = scan_limit_payload
                # `--semantic` fail-closed degrade signal (audit #95 Part 2): emitted ONLY when
                # set, mirroring json_fmt.py's own "omitted entirely, not null" rule so every
                # OTHER (non-rank/non-semantic) search's envelope shape stays byte-identical.
                if all_results.rank_fallback_reason:
                    payload["rank_fallback_reason"] = all_results.rank_fallback_reason
                return json.dumps(payload, indent=2)
            capped_note = (
                f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                if empty_scan_capped
                else ""
            )
            return (
                f"No matches found for '{search_pattern}' in {path}.\n{_routing_summary(all_results)}"
                f"{capped_note}"
            )

        if count_matches:
            # M10 (Fable MCP-surface audit): this branch used to ALWAYS return plain text,
            # ignoring `structured_json` (default True) -- a default caller doing
            # `json.loads()` on the response would fail. Honor the flag like every other
            # branch of this tool.
            if structured_json:
                count_payload = {
                    "pattern": search_pattern,
                    "path": path,
                    "total_matches": all_results.total_matches,
                    "total_files": all_results.total_files,
                    "result_incomplete": all_results.result_incomplete,
                    "incomplete_reason": all_results.incomplete_reason,
                    "routing": _routing_payload(all_results),
                }
                if scan_limit_payload is not None:
                    count_payload["scan_limit"] = scan_limit_payload
                if all_results.rank_fallback_reason:
                    count_payload["rank_fallback_reason"] = all_results.rank_fallback_reason
                return json.dumps(count_payload, indent=2)
            return (
                f"Found a total of {all_results.total_matches} matches across {all_results.total_files} files in {path}.\n"
                f"{_routing_summary(all_results)}"
            )

        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        rendered_by_file: dict[str, list[Any]] = {}
        rendered_match_count = 0
        if by_file:
            for filepath, matches in by_file.items():
                if filepath not in rendered_by_file:
                    if len(rendered_by_file) >= rendered_file_limit:
                        continue
                    rendered_by_file[filepath] = []
                for match in matches:
                    if rendered_match_count >= rendered_result_limit:
                        break
                    rendered_by_file[filepath].append(match)
                    rendered_match_count += 1
                if rendered_match_count >= rendered_result_limit:
                    break

        rendered_file_count = len(rendered_by_file)
        omitted_matches = max(0, all_results.total_matches - rendered_match_count)
        omitted_files = max(0, all_results.total_files - rendered_file_count)
        scan_capped = bool(scan_limit_payload and scan_limit_payload["possibly_truncated"])
        truncated = omitted_matches > 0 or omitted_files > 0 or scan_capped

        if structured_json:
            payload_matches = [
                {"file": filepath, "line_number": match.line_number, "text": match.text.strip()}
                for filepath, matches in rendered_by_file.items()
                for match in matches
            ]
            payload = {
                "pattern": search_pattern,
                "path": path,
                "total_matches": all_results.total_matches,
                "total_files": all_results.total_files,
                "rendered_match_count": len(payload_matches),
                "rendered_file_count": rendered_file_count,
                "matches": payload_matches,
                "truncated": truncated,
                "omitted_matches": omitted_matches,
                "omitted_files": omitted_files,
                # Partial results (rg exit 2 soft error) — top-level so an agent caller can't
                # read a truncated result as complete (suppression != absence).
                "result_incomplete": all_results.result_incomplete,
                "incomplete_reason": all_results.incomplete_reason,
                "routing": _routing_payload(all_results),
            }
            if scan_limit_payload is not None:
                payload["scan_limit"] = scan_limit_payload
            if all_results.rank_fallback_reason:
                payload["rank_fallback_reason"] = all_results.rank_fallback_reason
            return json.dumps(payload, indent=2)

        # Format the results into a readable string for the LLM
        output = [
            f"Found {all_results.total_matches} matches across {all_results.total_files} files:",
            _routing_summary(all_results),
        ]

        if rendered_by_file:
            for filepath, matches in rendered_by_file.items():
                output.append(f"\n{filepath}:")
                for m in matches:
                    output.append(f"  {m.line_number}: {m.text.strip()}")

            if truncated:
                output.append(
                    f"\n... output truncated to {rendered_match_count} results across "
                    f"{rendered_file_count} files; omitted {omitted_matches} matches "
                    f"across {omitted_files} files."
                )
            if scan_capped:
                output.append(
                    f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                )
        elif all_results.match_counts_by_file:
            rendered_counts = list(all_results.match_counts_by_file.items())[:rendered_file_limit]
            for filepath, count in rendered_counts:
                output.append(f"\n{filepath}:")
                output.append(f"  count={count}")
            omitted_count_files = max(
                0, len(all_results.match_counts_by_file) - len(rendered_counts)
            )
            if omitted_count_files:
                output.append(f"\n... and {omitted_count_files} more files.")
        elif all_results.matched_file_paths:
            rendered_paths = all_results.matched_file_paths[:rendered_file_limit]
            for filepath in rendered_paths:
                output.append(f"\n{filepath}:")
            omitted_path_files = max(0, len(all_results.matched_file_paths) - len(rendered_paths))
            if omitted_path_files:
                output.append(f"\n... and {omitted_path_files} more files.")

        return "\n".join(output)

    except Exception as e:
        return _sanitized_tool_error_text("tg_search", e)


@mcp.tool()  # type: ignore
def tg_ast_search(
    pattern: str,
    lang: str,
    path: str = ".",
    structured_json: bool = True,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Search source code structurally using the ast-grep/tree-sitter backend.
    Ignores whitespace and formatting, matching the true AST structure.

    Args:
        pattern: AST pattern to search for (e.g. 'if ($A) { return $B; }').
        lang: Language to parse (e.g. 'python', 'javascript').
        path: Directory or file to search.
        structured_json: Return bounded structured JSON (default true). Set to false for
            plain-text output.
        max_repo_files: Maximum files the directory walk parses before the scan is
            capped (protects against an unscoped full-monorepo AST parse).
    """
    # Bug #88: capture the "was path left at its default" signal from the RAW caller-supplied
    # value BEFORE confinement below reassigns `path` to its confined (absolute) form -- see
    # tg_search's identical comment for the full rationale.
    paths_defaulted = path == "."

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        if structured_json:
            return json.dumps(
                {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "error": {"code": "invalid_input", "message": str(exc)},
                },
                indent=2,
            )
        return f"AST search failed: {exc}"

    normalized_max_repo_files = max(1, int(max_repo_files))
    config = SearchConfig(ast=True, lang=lang, no_messages=True)
    try:
        pipeline = Pipeline(config=config)
        backend = pipeline.get_backend()
    except ConfigurationError as exc:
        # Fail closed with a STRUCTURED "unavailable" error instead of letting the
        # ConfigurationError escape as an unwrapped FastMCP ToolError (Backend Fail-Closed
        # Contract). `Pipeline(ast=True)` construction itself raises when the ast-grep /
        # tree-sitter deps are absent for this pattern (e.g. a Linux runner without ast-grep),
        # which is EARLIER than the backend-type check below -- mirror that branch's response
        # so a valid in-root path returns a clean "unavailable" rather than a raw exception.
        if structured_json:
            return json.dumps(
                {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "error": {
                        "code": "unavailable",
                        "message": f"AstBackend is not available on this system: {exc}",
                    },
                },
                indent=2,
            )
        return f"Error: AstBackend is not available on this system: {exc}"

    backend_name = type(backend).__name__
    if backend_name not in {"AstBackend", "AstGrepWrapperBackend"}:
        if structured_json:
            return json.dumps(
                {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "error": {
                        "code": "unavailable",
                        "message": "AstBackend is not available on this system. Requires ast-grep/tree-sitter.",
                    },
                },
                indent=2,
            )
        return "Error: AstBackend is not available on this system. Requires torch_geometric and tree_sitter."

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
    all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
    try:
        # H3 (Fable MCP-surface audit): same PR #400 walk-deadline/fallback/broad-root-refusal
        # port as `tg_search` -- the AST walk had the identical unbounded-hang and
        # discard-partial-results-on-fault gaps (this backend is NEVER `RipgrepBackend`, so
        # the large-root probe always applies).
        refusal_message, _scanner, walker = _mcp_broad_root_scan_refusal(
            path,
            config,
            normalized_max_repo_files=normalized_max_repo_files,
            check_large_root=True,
            paths_defaulted=paths_defaulted,
        )
        if refusal_message is not None:
            return _broad_root_scan_refusal_result(
                refusal_message,
                pattern=pattern,
                path=path,
                lang=lang,
                structured_json=structured_json,
            )
        files_scanned = 0
        scan_capped = False
        native_walk_deadline = compute_native_walk_deadline()
        for current_file in walker:
            if files_scanned >= normalized_max_repo_files:
                scan_capped = True
                break
            if native_walk_deadline_exceeded(native_walk_deadline):
                scan_capped = True
                all_results.result_incomplete = True
                all_results.incomplete_reason = (
                    "native AST search exceeded the wall-clock deadline and was stopped; "
                    "returning partial results. Scope the search to a smaller path, or "
                    "lower max_repo_files."
                )
                break
            try:
                result = backend.search(current_file, pattern, config=config)
            except BackendExecutionError as exc:
                # Unlike `tg_search`'s regex CPU-fallback, there is no equivalent
                # same-contract fallback engine for an AST query (CPUBackend does not
                # understand `config.ast`/`config.lang` and would silently degrade to a
                # nonsensical plain-text match on the AST pattern string -- exactly the
                # "silently swap engines for a contract flag" failure the Backend
                # Fail-Closed Contract forbids). Skip just this file, keep every match
                # already collected, and mark the result explicitly incomplete instead.
                sys.stderr.write(
                    f"tensor-grep-mcp: tg_ast_search backend failed on {current_file} "
                    f"({exc}); skipping file, keeping partial AST results.\n"
                )
                all_results.result_incomplete = True
                if not all_results.incomplete_reason:
                    all_results.incomplete_reason = (
                        f"AST backend failed on one or more files (first: {current_file}); "
                        "returning partial results."
                    )
                files_scanned += 1
                continue
            files_scanned += 1
            all_results.matches.extend(result.matches)
            all_results.matched_file_paths.extend(result.matched_file_paths)
            _merge_count_metadata(all_results, result)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
            _merge_runtime_routing(all_results, result)
        # NOTE: unlike `tg_search`, this does NOT OR in `scanner.scan_truncated` -- doing so
        # was tried and reverted (it broke `test_mcp_ast_search_reports_no_cap_hit_when_under_the_limit`:
        # a bare `MagicMock()` scanner auto-vivifies a truthy `.scan_truncated` attribute unless a
        # test explicitly stubs it False, which is out of scope for this fix).
        scan_limit_payload = {
            "max_repo_files": normalized_max_repo_files,
            "scanned_files": files_scanned,
            "possibly_truncated": scan_capped,
        }

        _apply_selected_gpu_defaults(
            all_results=all_results,
            selected_backend_name=getattr(
                pipeline, "selected_backend_name", backend.__class__.__name__
            ),
            selected_backend_reason=getattr(pipeline, "selected_backend_reason", "unknown"),
        )
        _finalize_aggregate_result(all_results)

        if all_results.is_empty:
            if structured_json:
                return json.dumps(
                    {
                        "pattern": pattern,
                        "lang": lang,
                        "path": path,
                        "total_matches": 0,
                        "total_files": all_results.total_files,
                        "rendered_match_count": 0,
                        "rendered_file_count": 0,
                        "matches": [],
                        "truncated": scan_capped,
                        "omitted_matches": 0,
                        "omitted_files": 0,
                        "result_incomplete": all_results.result_incomplete,
                        "incomplete_reason": all_results.incomplete_reason,
                        "scan_limit": scan_limit_payload,
                        "routing": _routing_payload(all_results),
                    },
                    indent=2,
                )
            capped_note = (
                f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                if scan_capped
                else ""
            )
            return (
                f"No AST matches found for pattern in {path}.\n{_routing_summary(all_results)}"
                f"{capped_note}"
            )

        # Group by file
        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        if structured_json:
            rendered_file_limit = 15
            rendered_result_limit = 150
            rendered_by_file: dict[str, list[Any]] = {}
            rendered_match_count = 0
            for filepath, matches in by_file.items():
                if len(rendered_by_file) >= rendered_file_limit:
                    break
                rendered_by_file[filepath] = []
                for match in matches:
                    if rendered_match_count >= rendered_result_limit:
                        break
                    rendered_by_file[filepath].append(match)
                    rendered_match_count += 1
            rendered_file_count = len(rendered_by_file)
            omitted_matches = max(0, all_results.total_matches - rendered_match_count)
            omitted_files = max(0, all_results.total_files - rendered_file_count)
            payload_matches = [
                {
                    "file": filepath,
                    "line_number": m.line_number,
                    "text": m.text.strip(),
                }
                for filepath, matches in rendered_by_file.items()
                for m in matches
            ]
            return json.dumps(
                {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "total_matches": all_results.total_matches,
                    "total_files": all_results.total_files,
                    "rendered_match_count": len(payload_matches),
                    "rendered_file_count": rendered_file_count,
                    "matches": payload_matches,
                    "truncated": omitted_matches > 0 or omitted_files > 0 or scan_capped,
                    "omitted_matches": omitted_matches,
                    "omitted_files": omitted_files,
                    "result_incomplete": all_results.result_incomplete,
                    "incomplete_reason": all_results.incomplete_reason,
                    "scan_limit": scan_limit_payload,
                    "routing": _routing_payload(all_results),
                },
                indent=2,
            )

        output = [
            f"Found {all_results.total_matches} structural AST matches across {all_results.total_files} files:",
            _routing_summary(all_results),
        ]
        if scan_capped:
            output.append(
                f"Scan capped at {normalized_max_repo_files} files; results may be incomplete."
            )

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
        if structured_json:
            return json.dumps(
                {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "error": _sanitized_tool_error("tg_ast_search", e),
                },
                indent=2,
            )
        return _sanitized_tool_error_text("tg_ast_search", e)


@mcp.tool()  # type: ignore
def tg_classify_logs(file_path: str, structured_json: bool = True) -> str:
    """
    Analyze a system log file with local heuristics by default, or the opt-in
    CyBERT/Triton provider when TENSOR_GREP_CLASSIFY_PROVIDER=cybert is set.

    Args:
        file_path: The absolute path to the log file to classify. Confined to the
            project root (cwd); a log file that legitimately lives outside the project
            must be copied in first (fail-closed, not a silent drop).
        structured_json: Return bounded structured JSON (default true). Set to false for
            plain-text output.
    """
    # round-7 security (audit #81 #1): confine file_path to the project root (cwd) before any
    # read -- unconfined it is an arbitrary-file-read/exfil primitive (FallbackReader will
    # happily read .env/keys/anything locally readable, and up to 20 heuristic-flagged lines
    # are echoed back verbatim in anomalies[].text below). Forward the RESOLVED path so the
    # downstream read below sees the same anchor-validated location this check validated.
    try:
        file_path = str(_confine_read_path(file_path, _mcp_root(), label="file_path"))
    except ValueError as exc:
        if structured_json:
            return json.dumps(
                {
                    "file_path": file_path,
                    "error": {"code": "invalid_input", "message": str(exc)},
                },
                indent=2,
            )
        return f"Error: {exc}"

    try:
        from tensor_grep.io.reader_fallback import FallbackReader
        from tensor_grep.sidecar import (
            DEFAULT_CLASSIFY_MAX_LINES,
            _apply_classify_line_budget,
            _classify_lines_with_metadata,
        )

        reader = FallbackReader()
        # round-7 security (audit #81 #1): bound the read BEFORE materializing. read_lines()
        # is a generator; previously `list(reader.read_lines(file_path))` fully materialized
        # the entire file into memory before the DEFAULT_CLASSIFY_MAX_LINES budget was applied
        # below -- an unbounded-memory DoS on a large (or attacker-influenceable) file. Cap the
        # read one line past the budget via itertools.islice so `_apply_classify_line_budget`
        # can still report `truncated=True` accurately without reading the rest of the file.
        lines = list(itertools.islice(reader.read_lines(file_path), DEFAULT_CLASSIFY_MAX_LINES + 1))
        if not lines:
            if structured_json:
                return json.dumps(
                    {
                        "file_path": file_path,
                        "error": {
                            "code": "invalid_input",
                            "message": f"File {file_path} is empty or unreadable.",
                        },
                    },
                    indent=2,
                )
            return f"Error: File {file_path} is empty or unreadable."

        budgeted_lines, line_budget = _apply_classify_line_budget(
            lines,
            DEFAULT_CLASSIFY_MAX_LINES,
        )
        results, backend_metadata = _classify_lines_with_metadata(budgeted_lines)
        provider_used = backend_metadata.get("provider_used", "heuristic")
        provider_status = backend_metadata.get("provider_status", "local")

        warnings_or_errors = []
        for i, r in enumerate(results):
            if r["label"] in ("warn", "error") and r["confidence"] > 0.8:
                warnings_or_errors.append((budgeted_lines[i].strip(), r["label"], r["confidence"]))

        if structured_json:
            return json.dumps(
                {
                    "file_path": file_path,
                    "provider": provider_used,
                    "provider_status": provider_status,
                    "sample_lines": line_budget["emitted_lines"],
                    "total_lines": line_budget["total_lines"],
                    "anomaly_count": len(warnings_or_errors),
                    "anomalies": [
                        {"label": label, "confidence": conf, "text": text}
                        for text, label, conf in warnings_or_errors[:20]
                    ],
                },
                indent=2,
            )

        if not warnings_or_errors:
            return f"No severe anomalies detected in {file_path}. All logs appear nominal."

        output = [
            (
                f"Log Classification for {file_path} "
                f"(provider={provider_used}, status={provider_status}, "
                f"sample={line_budget['emitted_lines']}/{line_budget['total_lines']} lines):"
            )
        ]
        output.append(f"\nDetected {len(warnings_or_errors)} High-Confidence Anomalies:")
        for text, label, conf in warnings_or_errors[:20]:  # Limit output
            output.append(f"[{label.upper()}] ({conf:.2f}) {text}")

        return "\n".join(output)

    except Exception as e:
        if structured_json:
            return json.dumps(
                {
                    "file_path": file_path,
                    "error": _sanitized_tool_error("tg_classify_logs", e),
                },
                indent=2,
            )
        return _sanitized_tool_error_text("tg_classify_logs", e)


@mcp.tool()  # type: ignore
def tg_devices(json_output: bool = True) -> str:
    """
    Return routable GPU inventory for scheduling and diagnostics.

    Args:
        json_output: Emit machine-readable JSON output (default true). Set to false for
            plain-text output.
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
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _index_search_error(str(exc), code="invalid_input", pattern=pattern, path=path)

    validation_error = _validate_index_search_inputs(pattern, path)
    if validation_error:
        return _index_search_error(
            validation_error,
            code="invalid_input",
            pattern=pattern,
            path=path,
        )

    native_tg, _native_error = _resolve_native_tg_binary_for_mcp()
    if native_tg is None:
        payload = _index_search_envelope()
        payload["query"] = pattern
        payload["path"] = path
        return _native_unavailable_error(tool="tg_index_search", payload=payload)

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
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _rewrite_error(str(exc), code="invalid_input")

    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    payload, _exit_code = execute_rewrite_plan_json(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
    )
    return payload


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
    policy: str | None = None,
    expected_plan_digest: str | None = None,
    expected_match_count: int | None = None,
) -> str:
    """
    Apply native AST rewrites and optionally verify the written bytes.

    For an agent-safe edit loop, call tg_rewrite_plan first, then pass the plan's
    ``plan_digest`` back here as ``expected_plan_digest``. When supplied, the plan
    is recomputed against the current tree before any edit is written and the apply
    fails with code="plan_drift" (no files modified) if the tree changed since the
    preview. Omit both expectation parameters for the original apply behavior.

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
            Executes a shell command; disabled on the MCP surface unless the operator
            sets TG_MCP_ALLOW_VALIDATION_COMMANDS=1 (rejected with code="unsupported_option").
        test_cmd: Optional command to run after apply/verify for structured test validation.
            Gated identically to lint_cmd via TG_MCP_ALLOW_VALIDATION_COMMANDS.
        policy: Optional path to an apply policy JSON file for post-apply checks and rollback.
            Confined to the rewrite scan root (``path``); a policy file that legitimately
            lives outside the scan root must be copied in first (fail-closed, not a silent
            drop).
        expected_plan_digest: Optional plan_digest from a prior tg_rewrite_plan. When
            supplied, the apply is refused with code="plan_drift" if the recomputed
            digest no longer matches the current tree.
        expected_match_count: Optional expected number of edit sites from a prior plan.
            When supplied, the apply is refused with code="plan_drift" if the current
            tree no longer yields exactly this many edits.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # BEFORE any of the checks below -- execute_rewrite_apply_json derives policy's
    # confinement anchor from this same `path` (policy_anchor), so an unconfined path here
    # would make that downstream anchor unconfined too (see tg_repo_map for the systemic
    # rationale, and tg_session_file_importers for the exact class of bug this order avoids).
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _rewrite_error(str(exc), code="invalid_input")

    # Audit HIGH (2026-06-24): lint_cmd/test_cmd execute a free-form shell command
    # in the native apply path. Over the MCP trust boundary (agent-steerable args)
    # that is an RCE primitive, so refuse them unless the operator explicitly opts in.
    # The agent-safe edit loop does not require validation commands.
    if (lint_cmd is not None or test_cmd is not None) and not _mcp_validation_commands_allowed():
        return _rewrite_error(
            "lint_cmd/test_cmd execute a shell command and are disabled on the MCP "
            "surface by default. Set TG_MCP_ALLOW_VALIDATION_COMMANDS=1 in the server "
            "environment to opt in (the agent-safe edit loop does not require them).",
            code="unsupported_option",
            retryable=False,
        )
    payload, _exit_code = execute_rewrite_apply_json(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        verify=verify,
        checkpoint=checkpoint,
        audit_manifest=audit_manifest,
        audit_signing_key=audit_signing_key,
        lint_cmd=lint_cmd,
        test_cmd=test_cmd,
        policy=policy,
        expected_plan_digest=expected_plan_digest,
        expected_match_count=expected_match_count,
        # Audit HIGH (RCE): a policy file's lint_cmd/test_cmd is a shell-exec sink on
        # the (agent-steerable) MCP boundary; gate it on the same operator opt-in as
        # the direct lint_cmd/test_cmd params above.
        allow_validation_commands=_mcp_validation_commands_allowed(),
    )
    return payload


@mcp.tool()  # type: ignore
def tg_audit_manifest_verify(
    manifest_path: str,
    signing_key: str | None = None,
    previous_manifest: str | None = None,
) -> str:
    """
    Verify a rewrite audit manifest digest, chain, and optional signature.

    Args:
        manifest_path: Path to the rewrite audit manifest JSON file. Confined to the
            project root (cwd); a manifest that legitimately lives outside the project
            must be copied in first (fail-closed, not a silent drop).
        signing_key: Optional HMAC signing key path for signed manifests. A READ of
            secret HMAC material; disabled on the MCP surface by default -- set
            TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1 in the server environment to opt in
            (mirrors tg_rewrite_apply's audit_signing_key gate, round-5).
        previous_manifest: Optional previous manifest path for validating manifest
            chaining. Confined to the project root (cwd) like manifest_path.
    """
    from tensor_grep.cli.audit_manifest import verify_audit_manifest_json

    if not manifest_path.strip():
        return _audit_manifest_error("manifest_path must not be empty.", code="invalid_input")

    # round-7 security (audit #81 #12): signing_key is a READ of secret HMAC key material.
    # Gate it default-OFF behind the same opt-in as tg_rewrite_apply's audit_signing_key
    # (round-5) for consistency -- unrestricted, it lets any MCP client point verification at
    # HMAC material anywhere locally readable. The key bytes themselves are never echoed back,
    # so an env-var opt-in gate is the right control here (not path confinement -- operators
    # legitimately keep HMAC keys outside the repo, e.g. ~/.config).
    if signing_key is not None and os.environ.get("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ") != "1":
        return _audit_manifest_error(
            "signing_key read requires TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1",
            code="unsupported_option",
        )

    # round-6 security (audit #7): confine the read-path params to the project root (cwd) --
    # unconfined they are an arbitrary-file-read/exfil primitive reachable from any MCP
    # client. Forward the RESOLVED paths so the downstream read in audit_manifest.py sees
    # the same anchor-validated location this check validated (closes the discard/TOCTOU
    # class), mirroring the write-side _confine_write_path precedent (round-4/5).
    try:
        manifest_path = str(_confine_write_path(manifest_path, _mcp_root(), label="manifest_path"))
        if previous_manifest is not None:
            previous_manifest = str(
                _confine_write_path(previous_manifest, _mcp_root(), label="previous_manifest")
            )
    except ValueError as exc:
        return _audit_manifest_error(str(exc), code="invalid_input")

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
def tg_audit_history(path: str = ".") -> str:
    """
    List audit manifest history for a project root.

    Args:
        path: Project root to inspect for audit manifests.
    """
    from tensor_grep.cli.audit_manifest import list_audit_history_payload

    if not path.strip():
        return _audit_history_error("path must not be empty.", code="invalid_input")

    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _audit_history_error(str(exc), code="invalid_input")

    try:
        return _inject_mcp_contract_fields(json.dumps(list_audit_history_payload(path), indent=2))
    except FileNotFoundError as exc:
        return _audit_history_error(str(exc), code="not_found")
    except ValueError as exc:
        return _audit_history_error(str(exc), code="invalid_input")
    except Exception as exc:
        return _audit_history_error(str(exc), code="internal_error")


@mcp.tool()  # type: ignore
def tg_audit_diff(previous_manifest: str, current_manifest: str) -> str:
    """
    Compute a semantic diff between two audit manifest JSON files.

    Args:
        previous_manifest: Path to the previous audit manifest JSON file. Confined to
            the project root (cwd); a manifest outside the project must be copied in
            first (fail-closed, not a silent drop).
        current_manifest: Path to the current audit manifest JSON file. Confined to
            the project root (cwd) like previous_manifest.
    """
    from tensor_grep.cli.audit_manifest import diff_audit_manifests_payload

    if not previous_manifest.strip() or not current_manifest.strip():
        return _audit_diff_error(
            "previous_manifest and current_manifest must not be empty.",
            code="invalid_input",
        )

    # round-6 security (audit #7): confine both read-path params to the project root
    # (cwd) -- unconfined they are an arbitrary-file-read/exfil primitive: the diff
    # (added/removed/changed) echoes raw field values from BOTH files verbatim into the
    # returned JSON. Forward the RESOLVED paths (see the audit #7 note on
    # tg_audit_manifest_verify above / _confine_write_path docstring).
    try:
        previous_manifest = str(
            _confine_write_path(previous_manifest, _mcp_root(), label="previous_manifest")
        )
        current_manifest = str(
            _confine_write_path(current_manifest, _mcp_root(), label="current_manifest")
        )
    except ValueError as exc:
        return _audit_diff_error(str(exc), code="invalid_input")

    try:
        return _inject_mcp_contract_fields(
            json.dumps(
                diff_audit_manifests_payload(previous_manifest, current_manifest),
                indent=2,
            )
        )
    except FileNotFoundError as exc:
        return _audit_diff_error(str(exc), code="not_found")
    except (json.JSONDecodeError, ValueError) as exc:
        return _audit_diff_error(str(exc), code="invalid_json")
    except Exception as exc:
        return _audit_diff_error(str(exc), code="internal_error")


@mcp.tool()  # type: ignore
def tg_review_bundle_create(
    manifest_path: str,
    scan_path: str | None = None,
    checkpoint_id: str | None = None,
    previous_manifest: str | None = None,
    output_path: str | None = None,
) -> str:
    """
    Create a review bundle containing audit, scan, checkpoint, and diff artifacts.

    Args:
        manifest_path: Path to the rewrite audit manifest JSON file. Confined to the
            project root (cwd); a manifest outside the project must be copied in first
            (fail-closed, not a silent drop).
        scan_path: Optional path to the ruleset scan JSON file. Confined to the project
            root (cwd) like manifest_path.
        checkpoint_id: Optional checkpoint ID to include.
        previous_manifest: Optional previous audit manifest JSON for diff generation.
            Confined to the project root (cwd) like manifest_path.
        output_path: Optional file path where the bundle JSON should be written.
    """
    from tensor_grep.cli.audit_manifest import create_review_bundle_json

    if not manifest_path.strip():
        return _review_bundle_error(
            "manifest_path must not be empty.",
            code="invalid_input",
            routing_reason="review-bundle-create",
        )

    # round-6 security (audit #7): confine the read-path params (manifest_path, scan_path,
    # previous_manifest) to the project root (cwd) -- unconfined they are an
    # arbitrary-file-read/exfil primitive: create_review_bundle_json echoes the manifest
    # and scan_results contents (and a diff of previous_manifest) verbatim into the
    # returned bundle JSON. Forward the RESOLVED paths so the downstream reads in
    # audit_manifest.py see the same anchor-validated locations this check validated
    # (closes the discard/TOCTOU class), mirroring the output_path write-confinement below.
    try:
        manifest_path = str(_confine_write_path(manifest_path, _mcp_root(), label="manifest_path"))
        if scan_path is not None:
            scan_path = str(_confine_write_path(scan_path, _mcp_root(), label="scan_path"))
        if previous_manifest is not None:
            previous_manifest = str(
                _confine_write_path(previous_manifest, _mcp_root(), label="previous_manifest")
            )
    except ValueError as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_input",
            routing_reason="review-bundle-create",
        )

    # round-4/5 security: confine the bundle output to the project (cwd) — unconfined it is an
    # arbitrary-file-write primitive reachable from any MCP client. round-5: consume the
    # RESOLVED absolute path (not the raw candidate) below so create_review_bundle_json's own
    # re-resolve in audit_manifest.py sees the same anchor-validated location this check
    # validated (closes the discard/TOCTOU class).
    if output_path is not None:
        try:
            output_path = str(_confine_write_path(output_path, _mcp_root(), label="output_path"))
        except ValueError as exc:
            return _review_bundle_error(
                str(exc),
                code="invalid_input",
                routing_reason="review-bundle-create",
            )

    try:
        return create_review_bundle_json(
            manifest_path,
            scan_path=scan_path,
            checkpoint_id=checkpoint_id,
            previous_manifest=previous_manifest,
            output_path=output_path,
        )
    except FileNotFoundError as exc:
        return _review_bundle_error(
            str(exc),
            code="not_found",
            routing_reason="review-bundle-create",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_json",
            routing_reason="review-bundle-create",
        )
    except Exception as exc:
        return _review_bundle_error(
            str(exc),
            code="internal_error",
            routing_reason="review-bundle-create",
        )


@mcp.tool()  # type: ignore
def tg_review_bundle_verify(bundle_path: str) -> str:
    """
    Verify review bundle integrity and component checksums.

    Args:
        bundle_path: Path to the review bundle JSON file. Confined to the project root
            (cwd); a bundle outside the project must be copied in first (fail-closed,
            not a silent drop).
    """
    from tensor_grep.cli.audit_manifest import verify_review_bundle_json

    if not bundle_path.strip():
        return _review_bundle_error(
            "bundle_path must not be empty.",
            code="invalid_input",
            routing_reason="review-bundle-verify",
        )

    # round-6 security (audit #7): confine bundle_path to the project root (cwd) --
    # unconfined it is an arbitrary-file-read/exfil primitive (see the audit #7 note on
    # tg_review_bundle_create above / _confine_write_path docstring).
    try:
        bundle_path = str(_confine_write_path(bundle_path, _mcp_root(), label="bundle_path"))
    except ValueError as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_input",
            routing_reason="review-bundle-verify",
        )

    try:
        return verify_review_bundle_json(bundle_path)
    except FileNotFoundError as exc:
        return _review_bundle_error(
            str(exc),
            code="not_found",
            routing_reason="review-bundle-verify",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_json",
            routing_reason="review-bundle-verify",
        )
    except Exception as exc:
        return _review_bundle_error(
            str(exc),
            code="internal_error",
            routing_reason="review-bundle-verify",
        )


@mcp.tool()  # type: ignore
def tg_checkpoint_create(path: str = ".") -> str:
    """
    Create an edit checkpoint rooted at the given path.

    Args:
        path: File or directory rooted at the checkpoint scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read/write -- see tg_repo_map for the systemic-finding rationale. Checkpoint
    # create/undo write rollback state rooted at `path`, so unconfined this was also an
    # arbitrary-directory-WRITE primitive, not just a read.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": path,
            },
            indent=2,
        )

    from tensor_grep.cli.checkpoint_store import create_checkpoint

    try:
        payload = create_checkpoint(path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps(
        {
            "version": _json_output_version(),
            "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
            "schema_version": _json_output_version(),
            **payload.__dict__,
        },
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_checkpoint_list(path: str = ".") -> str:
    """
    List checkpoints rooted at the given path.

    Args:
        path: File or directory rooted at the checkpoint scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": path,
            },
            indent=2,
        )

    from tensor_grep.cli.checkpoint_store import list_checkpoints

    try:
        checkpoints = [record.__dict__ for record in list_checkpoints(path)]
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
            },
            indent=2,
        )

    return json.dumps(
        {
            "version": _json_output_version(),
            "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
            "checkpoints": checkpoints,
        },
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_checkpoint_undo(checkpoint_id: str, path: str = ".") -> str:
    """
    Undo an edit checkpoint rooted at the given path.

    Args:
        checkpoint_id: Checkpoint ID to restore.
        path: File or directory rooted at the checkpoint scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read/write -- see tg_repo_map for the systemic-finding rationale. Checkpoint
    # undo restores files rooted at `path`, so unconfined this was also an
    # arbitrary-directory-WRITE primitive, not just a read.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": path,
                "checkpoint_id": checkpoint_id,
            },
            indent=2,
        )

    from tensor_grep.cli.checkpoint_store import undo_checkpoint

    try:
        payload = undo_checkpoint(checkpoint_id, path)
    except Exception as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": str(Path(path).expanduser()),
                "checkpoint_id": checkpoint_id,
            },
            indent=2,
        )

    return json.dumps(
        {
            "version": _json_output_version(),
            "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
            "schema_version": _json_output_version(),
            **payload.__dict__,
        },
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_session_open(path: str = ".", max_repo_files: int | None = 512) -> str:
    """
    Create a cached repository-map session for repeated edit loops.

    Args:
        path: File or directory rooted at the session scope.
        max_repo_files: Optional cap for files scanned into the initial session repo map.
            Defaults to 512 for agent-safe cold opens.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before opening a session rooted there -- see tg_repo_map for the systemic-finding
    # rationale. A session persists a cached repo-map keyed to `path`, so unconfined this was
    # also an arbitrary-directory-read primitive (the cached repo_map content is later
    # returned verbatim by tg_session_show/tg_session_context/etc.).
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_exception_payload(path=path, message=str(exc), detail={})

    from tensor_grep.cli.session_store import get_session, open_session

    try:
        result = open_session(path, max_repo_files=max_repo_files)
    except Exception as exc:
        return _session_exception_payload(path=path, message=str(exc), detail={})

    # M13: add tracked_file_count which counts source + test files (related_paths)
    # to complement file_count which only counts non-test source files.
    try:
        session_payload = get_session(result.session_id, path)
        repo_map = session_payload.get("repo_map") or {}
        related_paths = repo_map.get("related_paths") or []
        tracked_file_count = len(related_paths)
    except Exception:
        tracked_file_count = result.file_count

    return json.dumps(
        {
            "version": _json_output_version(),
            "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
            "schema_version": _json_output_version(),
            **result.__dict__,
            "tracked_file_count": tracked_file_count,
        },
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_session_list(path: str = ".") -> str:
    """
    List cached sessions for the current root.

    Args:
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_exception_payload(path=path, message=str(exc), detail={})

    from tensor_grep.cli.session_store import list_sessions

    try:
        sessions = [record.__dict__ for record in list_sessions(path)]
    except Exception as exc:
        return _session_exception_payload(path=path, message=str(exc), detail={})

    return json.dumps(
        {
            "version": _json_output_version(),
            "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
            "sessions": sessions,
        },
        indent=2,
    )


@mcp.tool()  # type: ignore
def tg_session_show(session_id: str, path: str = ".") -> str:
    """
    Return the cached repository-map payload for a session.

    Args:
        session_id: Session ID to inspect.
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_exception_payload(
            session_id=session_id,
            path=path,
            message=str(exc),
            detail={},
        )

    from tensor_grep.cli.session_store import get_session

    try:
        payload = get_session(session_id, path)
    except Exception as exc:
        return _session_exception_payload(
            session_id=session_id,
            path=path,
            message=str(exc),
            detail={},
        )

    return _inject_mcp_contract_fields(json.dumps(payload, indent=2))


@mcp.tool()  # type: ignore
def tg_session_refresh(session_id: str, path: str = ".") -> str:
    """
    Refresh a cached repository-map session after file changes.

    Args:
        session_id: Session ID to refresh.
        path: File or directory rooted at the session scope.
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_exception_payload(
            session_id=session_id,
            path=path,
            message=str(exc),
            detail={},
        )

    from tensor_grep.cli.session_store import refresh_session

    try:
        payload = refresh_session(session_id, path)
    except Exception as exc:
        return _session_exception_payload(
            session_id=session_id,
            path=path,
            message=str(exc),
            detail={},
        )

    return json.dumps(payload.__dict__, indent=2)


@mcp.tool()  # type: ignore
def tg_session_context(
    session_id: str,
    query: str,
    path: str = ".",
    refresh_on_stale: bool = False,
    auto_refresh: bool | None = None,
    max_tokens: int | None = _DEFAULT_MCP_CONTEXT_MAX_TOKENS,
) -> str:
    """
    Return a context pack derived from a cached session.

    Args:
        session_id: Session ID to query.
        query: Query text used to rank relevant repo context.
        path: File or directory rooted at the session scope.
        max_tokens: Bound the pack for prompt injection (default ~16000; 0/None = unbounded).
    """
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any read -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query},
            query=query,
        )

    from tensor_grep.cli.session_store import SessionStaleError, session_context

    effective_refresh = _effective_auto_refresh(refresh_on_stale, auto_refresh)
    try:
        payload = session_context(session_id, query, path, refresh_on_stale=effective_refresh)
        # H4 (Fable MCP-surface audit): every sibling context tool (`tg_context_pack`,
        # `tg_context_render`, `tg_agent_capsule`, the session render/edit-plan family) bounds
        # its output by `max_tokens`; this tool called `session_context` ->
        # `build_context_pack_from_map` with NO bound at all (dogfood 1.27.0: unbounded at
        # ~557KB/384 files -- the exact regression `session context --daemon` hit on the CLI,
        # main.py's `_apply_context_token_budget` call). Port the same post-processing step
        # here (imported from repo_map.py, not reimplemented) since `session_store.py` is out
        # of scope for this fix.
        payload = _apply_context_token_budget(payload, max_tokens)
    except SessionStaleError as exc:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=str(exc),
            detail={"query": query},
            query=query,
        )
    except FileNotFoundError:
        return _session_error_payload(
            session_id=session_id,
            path=path,
            code="invalid_input",
            message=f"Path not found: {Path(path).expanduser().resolve()}",
            detail={"query": query},
            query=query,
        )
    except Exception as exc:
        return _session_exception_payload(
            session_id=session_id,
            path=path,
            message=str(exc),
            detail={"query": query},
            query=query,
        )

    return _inject_mcp_contract_fields(json.dumps(payload, indent=2))


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
    # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
    # before any scan -- see tg_repo_map for the systemic-finding rationale.
    try:
        path = str(_confine_mcp_path(path, label="path"))
    except ValueError as exc:
        return _rewrite_error(str(exc), code="invalid_input")

    validation_error = _validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    native_tg, _native_error = _resolve_native_tg_binary_for_mcp()
    if native_tg is None:
        return _native_unavailable_error(
            tool="tg_rewrite_diff",
            payload=_rewrite_envelope(),
        )

    command = _build_rewrite_command(
        pattern=pattern,
        replacement=replacement,
        lang=lang,
        path=path,
        mode="diff",
    )
    return _execute_rewrite_diff_command(command)


# Bound the Content-Length compatibility read. Official MCP stdio is newline-delimited; this framed
# path is a legacy shim, and a hostile/buggy client sending a huge Content-Length must not drive an
# unbounded stdin.read (memory DoS). Mirrors lsp_external_provider._MAX_LSP_MESSAGE_BYTES.
_MAX_MCP_STDIO_MESSAGE_BYTES = 64 * 1024 * 1024


async def _read_stdio_message_payload(stdin: anyio.AsyncFile[str]) -> str | None:
    line = await stdin.readline()
    if line == "":
        return None
    if not line.strip():
        return ""
    if not line.lower().startswith("content-length:"):
        return line

    try:
        content_length = int(line.split(":", 1)[1].strip())
    except (IndexError, ValueError):
        return line
    if content_length <= 0 or content_length > _MAX_MCP_STDIO_MESSAGE_BYTES:
        # Fail closed: a non-positive or oversized frame is refused rather than read unbounded.
        return None
    while True:
        header = await stdin.readline()
        if header == "":
            return None
        if not header.strip():
            break
    return await stdin.read(content_length)


@asynccontextmanager
async def _stdio_server_accepting_content_length(
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> AsyncIterator[tuple[Any, Any]]:
    if not stdin:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    if not stdout:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    # Subscript the anyio factory so the item type is explicit: newer mypy cannot infer the generic
    # of `create_memory_object_stream(0)` and errors "Need type annotation" (older mypy did not).
    # read side carries a validated SessionMessage OR the JSON-decode Exception (sent at the reader);
    # write side carries a SessionMessage.
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](
        0
    )
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def stdin_reader() -> None:
        try:
            async with read_stream_writer:
                while True:
                    payload = await _read_stdio_message_payload(stdin)
                    if payload is None:
                        break
                    if not payload.strip():
                        continue
                    try:
                        message = types.JSONRPCMessage.model_validate_json(payload)
                    except Exception as exc:  # pragma: no cover
                        await read_stream_writer.send(exc)
                        continue
                    await read_stream_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    await stdout.write(payload + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream


async def _run_mcp_stdio_async() -> None:
    _apply_mcp_server_metadata(mcp)
    async with _stdio_server_accepting_content_length() as (read_stream, write_stream):
        await mcp._mcp_server.run(
            read_stream,
            write_stream,
            mcp._mcp_server.create_initialization_options(),
        )


def run_mcp_server() -> None:
    """Entry point for the MCP server."""
    anyio.run(_run_mcp_stdio_async)
