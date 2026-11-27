"""MCP path confinement, error sanitization, and meta-tool envelopes."""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.checkpoint_store import CheckpointNotFoundError
from tensor_grep.cli.main import (
    _LARGE_ROOT_SCAN_FILE_CEILING,
    _format_unbounded_large_root_scan_error,
    _format_unbounded_vendored_root_scan_error,
    _should_refuse_unbounded_large_root_scan,
    _should_refuse_unbounded_vendored_root_scan,
)
from tensor_grep.cli.mcp.server import _TG_MCP_SERVER_CONTRACT_VERSION
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.io.directory_scanner import DirectoryScanner

AuditAction = _tool_contract.AuditAction
CheckpointAction = _tool_contract.CheckpointAction
ContextAction = _tool_contract.ContextAction
ExploreAction = _tool_contract.ExploreAction
ImpactAction = _tool_contract.ImpactAction
NavigateAction = _tool_contract.NavigateAction
QueryAction = _tool_contract.QueryAction
RewriteAction = _tool_contract.RewriteAction
ScanAction = _tool_contract.ScanAction
SessionAction = _tool_contract.SessionAction
_TG_AUDIT_ACTIONS = _tool_contract.TG_AUDIT_ACTIONS
_TG_CHECKPOINT_ACTIONS = _tool_contract.TG_CHECKPOINT_ACTIONS
_TG_CONTEXT_ACTIONS = _tool_contract.TG_CONTEXT_ACTIONS
_TG_EXPLORE_ACTIONS = _tool_contract.TG_EXPLORE_ACTIONS
_TG_IMPACT_ACTIONS = _tool_contract.TG_IMPACT_ACTIONS
_TG_NAVIGATE_ACTIONS = _tool_contract.TG_NAVIGATE_ACTIONS
_TG_QUERY_ACTIONS = _tool_contract.TG_QUERY_ACTIONS
_TG_REWRITE_ACTIONS = _tool_contract.TG_REWRITE_ACTIONS
_TG_SCAN_ACTIONS = _tool_contract.TG_SCAN_ACTIONS
_TG_SESSION_ACTIONS = _tool_contract.TG_SESSION_ACTIONS
register_mcp_tool = _tool_contract.register_mcp_tool

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]


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
    Strictly non-throwing: even if stderr write/flush raises, this must
    never raise and allow exceptions to escape to the MCP transport.
    """
    try:
        import traceback

        try:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        except BaseException:
            try:
                msg = str(exc)
            except BaseException:
                msg = "<unprintable>"
            type_name = getattr(type(exc), "__name__", "Exception")
            detail = f"{type_name}: {msg}\n"
        print(f"[tensor-grep-mcp] {tool_name} failed: {detail}", file=sys.stderr, end="")
    except BaseException:
        pass


_TRUSTED_EXCEPTION_CLASSES: dict[type, str] = {
    # Standard Python built-in exceptions
    ArithmeticError: "ArithmeticError",
    AssertionError: "AssertionError",
    AttributeError: "AttributeError",
    BaseException: "BaseException",
    BufferError: "BufferError",
    BytesWarning: "BytesWarning",
    DeprecationWarning: "DeprecationWarning",
    EOFError: "EOFError",
    EnvironmentError: "EnvironmentError",
    Exception: "Exception",
    FloatingPointError: "FloatingPointError",
    FutureWarning: "FutureWarning",
    GeneratorExit: "GeneratorExit",
    ImportError: "ImportError",
    ImportWarning: "ImportWarning",
    IndexError: "IndexError",
    IOError: "IOError",
    KeyError: "KeyError",
    KeyboardInterrupt: "KeyboardInterrupt",
    LookupError: "LookupError",
    MemoryError: "MemoryError",
    ModuleNotFoundError: "ModuleNotFoundError",
    NameError: "NameError",
    NotImplementedError: "NotImplementedError",
    OSError: "OSError",
    OverflowError: "OverflowError",
    PendingDeprecationWarning: "PendingDeprecationWarning",
    PermissionError: "PermissionError",
    ProcessLookupError: "ProcessLookupError",
    RecursionError: "RecursionError",
    ReferenceError: "ReferenceError",
    ResourceWarning: "ResourceWarning",
    RuntimeError: "RuntimeError",
    RuntimeWarning: "RuntimeWarning",
    StopAsyncIteration: "StopAsyncIteration",
    StopIteration: "StopIteration",
    SyntaxError: "SyntaxError",
    SyntaxWarning: "SyntaxWarning",
    SystemError: "SystemError",
    SystemExit: "SystemExit",
    TabError: "TabError",
    TimeoutError: "TimeoutError",
    TypeError: "TypeError",
    UnboundLocalError: "UnboundLocalError",
    UnicodeDecodeError: "UnicodeDecodeError",
    UnicodeEncodeError: "UnicodeEncodeError",
    UnicodeError: "UnicodeError",
    UnicodeTranslateError: "UnicodeTranslateError",
    UserWarning: "UserWarning",
    ValueError: "ValueError",
    Warning: "Warning",
    ZeroDivisionError: "ZeroDivisionError",
    FileNotFoundError: "FileNotFoundError",
    IsADirectoryError: "IsADirectoryError",
    NotADirectoryError: "NotADirectoryError",
    ConnectionError: "ConnectionError",
    BrokenPipeError: "BrokenPipeError",
    ConnectionAbortedError: "ConnectionAbortedError",
    ConnectionRefusedError: "ConnectionRefusedError",
    ConnectionResetError: "ConnectionResetError",
    FileExistsError: "FileExistsError",
    InterruptedError: "InterruptedError",
    ChildProcessError: "ChildProcessError",
    # Standard library exceptions
    json.JSONDecodeError: "JSONDecodeError",
    subprocess.CalledProcessError: "CalledProcessError",
    subprocess.SubprocessError: "SubprocessError",
    subprocess.TimeoutExpired: "TimeoutExpired",
    # Framework exceptions
    BackendExecutionError: "BackendExecutionError",
    CheckpointNotFoundError: "FileNotFoundError",
    ConfigurationError: "ConfigurationError",
}


def _safe_exception_class_name(exc: BaseException) -> str:
    """Classify by exact trusted type identity, without invoking custom accessors."""
    try:
        raw_type = type(exc)
        # Verify raw_type is a standard class, not an object with a spoofed metaclass
        if type(raw_type) is not type:
            return "InternalError"
        # Strict object identity check (prevents metaclass __eq__ / __hash__ spoofing)
        for trusted_cls, name in _TRUSTED_EXCEPTION_CLASSES.items():
            if raw_type is trusted_cls:
                return name
        return "InternalError"
    except BaseException:
        return "InternalError"


def _sanitized_tool_error(
    tool_name: str,
    exc: BaseException,
    *,
    code: str = "internal_error",
    retryable: bool = False,
) -> dict[str, Any]:
    """Build a stable, sanitized error object for an MCP tool JSON response.

    Logs the full exception server-side (see `_log_tool_exception`) and
    returns only a short category + safe reason to the client -- never the
    raw exception text or a stack trace. Still signals failure (the caller
    keeps the call's error/non-empty-result contract); this only strips the
    internals from what crosses the wire.
    """
    _log_tool_exception(str(tool_name) if isinstance(tool_name, str) else "mcp_tool", exc)
    cls_name = _safe_exception_class_name(exc)
    safe_name = tool_name if isinstance(tool_name, str) else "mcp_tool"
    return {
        "code": code,
        "message": f"{safe_name} failed due to an internal error ({cls_name}).",
        "retryable": retryable,
    }


def _sanitized_tool_error_text(tool_name: str, exc: BaseException) -> str:
    """Plain-text counterpart of `_sanitized_tool_error` for tool response
    modes that return free text instead of a JSON envelope.
    """
    safe_name = tool_name if isinstance(tool_name, str) else "mcp_tool"
    _log_tool_exception(safe_name, exc)
    cls_name = _safe_exception_class_name(exc)
    return f"{safe_name} failed: internal error ({cls_name}). See server logs for detail."


# #98 (MCP consolidation Phase-1): shared envelope/error helpers for the 10 task-shaped
# meta-tools. Every meta tool dispatches to an existing legacy tool function for the actual
# work (which already returns a fully-formed, tool-specific JSON envelope) -- these helpers
# only cover the meta layer's OWN error surface: an unknown `action`, a required param missing
# for the chosen `action`, or a confinement failure caught before any action runs.
def _meta_envelope(
    *, tool: str, action: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = _envelope_base(
        routing_backend="MCPMeta",
        routing_reason=f"{tool}:{action}",
        include_schema_version=False,
    )
    payload["tool"] = tool
    payload["action"] = action
    if extra:
        payload.update(extra)
    return payload


def _meta_unknown_action_error(tool: str, action: str, valid_actions: tuple[str, ...]) -> str:
    payload = _meta_envelope(tool=tool, action=action)
    payload["error"] = {
        "code": "invalid_input",
        "message": (
            f"Unknown action {action!r} for {tool}. Valid actions: {', '.join(valid_actions)}."
        ),
    }
    return json.dumps(payload, indent=2)


def _meta_missing_param_error(tool: str, action: str, param: str) -> str:
    payload = _meta_envelope(tool=tool, action=action)
    payload["error"] = {
        "code": "invalid_input",
        "message": f"{tool} action={action!r} requires '{param}'.",
    }
    return json.dumps(payload, indent=2)


def _meta_invalid_input_error(tool: str, action: str, message: str) -> str:
    payload = _meta_envelope(tool=tool, action=action)
    payload["error"] = {"code": "invalid_input", "message": message}
    return json.dumps(payload, indent=2)


_TRUSTED_EXCEPTION_CLASSES[PathConfinementError] = "PathConfinementError"


def _meta_confinement_error(tool: str, action: str, exc: PathConfinementError) -> str:
    payload = _meta_envelope(tool=tool, action=action)
    payload["error"] = {"code": "invalid_input", "message": str(exc)}
    payload["incomplete"] = {"status": True, "cause": "error", "budget_remediable": False}
    return json.dumps(payload, indent=2)


def _meta_workspace_roots_cap_error(tool: str, action: str, *, count: int, cap: int) -> str:
    payload = _meta_envelope(tool=tool, action=action)
    payload["error"] = {
        "code": "invalid_input",
        "message": (
            f"{tool} action={action!r} received {count} workspace_roots, exceeding the "
            f"{cap}-root limit (each root re-runs the full action as an independent scan "
            "pass). Split the call across multiple workspace_roots batches."
        ),
    }
    return json.dumps(payload, indent=2)


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
_PLAN_DIGEST_VERSION = "tg-plan-digest-v1"
_REWRITE_ROUTING_BACKEND = "AstBackend"
_REWRITE_ROUTING_REASON = "ast-native"
_INDEX_ROUTING_BACKEND = "TrigramIndex"
_INDEX_ROUTING_REASON = "index-accelerated"
_WINDOWS_VARIADIC_METAVAR_RE = re.compile(r"(?<!\$)\$\$([A-Z][A-Z0-9_]*)")
_NATIVE_TG_REMEDIATION = (
    "Install a standalone native tg binary, put it on PATH, or set TG_NATIVE_TG_BINARY."
)


def _record_generated_audit_manifest(payload: object) -> None:
    audit_manifest = payload.get("audit_manifest") if isinstance(payload, dict) else None
    if not isinstance(audit_manifest, dict):
        return
    manifest_path = audit_manifest.get("path")
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        return
    try:
        from tensor_grep.cli.audit_manifest import record_audit_manifest

        record_audit_manifest(manifest_path)
    except Exception as exc:  # W1-a: was a bare `return` = fail-open on the audit trail
        _log_tool_exception("record_audit_manifest", exc)  # raw reason, server-side only
        audit_manifest.update(recorded=False, record_error=_safe_exception_class_name(exc))
        return


def _mcp_broad_root_scan_refusal(
    path: str,
    config: SearchConfig,
    *,
    normalized_max_repo_files: int,
    check_large_root: bool,
    paths_defaulted: bool,
) -> tuple[str | None, DirectoryScanner, Iterator[str]]:
    """Cheap pre-walk safety guard ported from the CLI `tg search` command.

    Returns ``(error_message, scanner, walker)``. When ``error_message`` is ``None`` the
    caller should iterate ``walker`` exactly as it would have iterated
    ``scanner.walk(path)`` -- it already carries any entries the refusal probe consumed,
    so no re-scan is needed. ``scanner`` is returned too so the caller can still read its
    post-walk ``scan_truncated`` bookkeeping attribute.
    """
    scanner = _self.DirectoryScanner(config)
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
            # A scan-policy ceiling: `truncated` AND `result_incomplete` are both true, which is
            # exactly what `scan_limit` denotes. `error.code` below is a sibling signal for
            # WHICH policy refused; this is the standard budget-remediability answer an agent
            # branches on without reading either string.
            "incomplete_reason_class": "scan_limit",
            "error": {
                "code": "broad_scan_refused",
                "message": message,
                "retryable": False,
            },
        }
        if lang is not None:
            payload["lang"] = lang
        # M14: this helper is shared by tg_search / tg_ast_search but is unreachable by
        # black-box census probes -- route through the injector like every other envelope.
        return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
    return f"tg: {message}"


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
    require the result to be the anchor itself or a descendant; raise ``PathConfinementError``
    otherwise (fail closed). Callers MUST forward the resolved ``Path`` this returns, not the
    raw candidate string, so the downstream read/write sees the same anchor-validated
    location this check validated (closes the discard/TOCTOU class).

    SEC-007: the client-facing message is a constant refusal (no absolute paths). The
    resolved escape target is logged to stderr for server-side debugging only.
    """
    try:
        anchor_resolved = anchor.expanduser().resolve()
        raw = Path(candidate).expanduser()
        target = raw if raw.is_absolute() else (anchor_resolved / raw)
        resolved = target.resolve()
        if resolved != anchor_resolved and anchor_resolved not in resolved.parents:
            print(
                f"[tensor-grep-mcp] path confinement refusal: {label} {resolved} escapes {anchor_resolved}",
                file=sys.stderr,
            )
            raise PathConfinementError(label)
        return resolved
    except PathConfinementError:
        raise
    except Exception as exc:
        print(
            f"[tensor-grep-mcp] path confinement resolution failure: {label} {candidate}: {exc}",
            file=sys.stderr,
        )
        raise PathConfinementError(label) from exc


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
    resolve, fail-closed PathConfinementError, callers MUST forward the resolved ``Path``
    this returns).
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
    if raw:
        try:
            resolved = Path(raw).expanduser().resolve()
            if resolved.is_dir():
                return resolved
            print(
                f"[tensor-grep-mcp] TG_MCP_ROOT={raw!r} is not an existing directory "
                f"(resolved: {resolved}); falling back to the current working directory.",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[tensor-grep-mcp] TG_MCP_ROOT={raw!r} could not be resolved: {exc}; "
                "falling back to the current working directory.",
                file=sys.stderr,
            )
    try:
        return Path.cwd().resolve()
    except Exception as exc:
        print(f"[tensor-grep-mcp] root resolution failure for root: {exc}", file=sys.stderr)
        raise PathConfinementError("root") from exc


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
    try:
        root = _mcp_root()
    except Exception as exc:
        print(f"[tensor-grep-mcp] root resolution failure for {label}: {exc}", file=sys.stderr)
        raise PathConfinementError(label) from exc
    return _confine_read_path(candidate, root, label=label)


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
