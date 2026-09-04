"""Native AST rewrite (plan/apply/diff) and trigram index-search ENGINE: pure
computation/subprocess helpers with no MCP tool decorators.

Split out of mcp_server.py (docs/design/2026-08-19-split-floor-escape.md, Route A) as a
pure code move: no wire-surface change. Every relocated function keeps its original
``_self.NAME(...)`` calls verbatim, but ``_self`` here is bound to the mcp_server module
object (not this one) -- so a test that does
``monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", fake)`` keeps working
exactly as it did before the split. The MCP tool wrappers that call into this engine
(tg_rewrite_plan/apply/diff, tg_index_search, tg_ruleset_scan, tg_audit_*,
tg_review_bundle_*, tg_checkpoint_*) live in the sibling module mcp_audit_tools.py,
which imports the public/plain names below directly (none of them are monkeypatched in
this repo's test suite -- scripts/bare_call_ratchet.py confirms 0 bare calls to a
patched name repo-wide).

Everything imported directly below (``_confine_mcp_path``, ``_mcp_root``,
``_envelope_base``, ...) is NOT part of the bare-call locked set, for the same reason.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.incompleteness import disclosed_incomplete

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]

from tensor_grep.cli.mcp_server import (
    _INDEX_ROUTING_BACKEND as _INDEX_ROUTING_BACKEND,
)
from tensor_grep.cli.mcp_server import (
    _INDEX_ROUTING_REASON as _INDEX_ROUTING_REASON,
)
from tensor_grep.cli.mcp_server import (
    _NATIVE_TG_REMEDIATION as _NATIVE_TG_REMEDIATION,
)
from tensor_grep.cli.mcp_server import (
    _PLAN_DIGEST_VERSION as _PLAN_DIGEST_VERSION,
)
from tensor_grep.cli.mcp_server import (
    _REWRITE_INTERNAL_ERROR_SIGNATURES as _REWRITE_INTERNAL_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp_server import (
    _REWRITE_IO_ERROR_SIGNATURES as _REWRITE_IO_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp_server import (
    _REWRITE_PATTERN_ERROR_SIGNATURES as _REWRITE_PATTERN_ERROR_SIGNATURES,
)
from tensor_grep.cli.mcp_server import (
    _REWRITE_ROUTING_BACKEND as _REWRITE_ROUTING_BACKEND,
)
from tensor_grep.cli.mcp_server import (
    _REWRITE_ROUTING_REASON as _REWRITE_ROUTING_REASON,
)
from tensor_grep.cli.mcp_server import (
    _WINDOWS_VARIADIC_METAVAR_RE as _WINDOWS_VARIADIC_METAVAR_RE,
)
from tensor_grep.cli.mcp_server import (
    PathConfinementError as PathConfinementError,
)
from tensor_grep.cli.mcp_server import (
    _confine_read_path as _confine_read_path,
)
from tensor_grep.cli.mcp_server import (
    _confine_write_path as _confine_write_path,
)
from tensor_grep.cli.mcp_server import (
    _envelope_base as _envelope_base,
)
from tensor_grep.cli.mcp_server import (
    _log_tool_exception as _log_tool_exception,
)
from tensor_grep.cli.mcp_server import (
    _mcp_root as _mcp_root,
)
from tensor_grep.cli.mcp_server import (
    _record_generated_audit_manifest as _record_generated_audit_manifest,
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


def _classify_native_rewrite_failure(
    stderr: str | BaseException,
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
    haystack = str(stderr).casefold()
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
        return _self.resolve_native_tg_binary(), None
    except FileNotFoundError as exc:
        _log_tool_exception("resolve_native_tg_binary", exc)
        return None, f"Native binary not found: {exc.__class__.__name__}"
    except Exception as exc:
        _log_tool_exception("resolve_native_tg_binary", exc)
        return None, f"Native binary resolution failed: {exc.__class__.__name__}"


_ALLOWED_POLICY_FIELDS = {
    "$",
    "version",
    "lint_cmd",
    "test_cmd",
    "ruleset_scan",
    "ruleset_scan.enabled",
    "ruleset_scan.pack",
    "ruleset_scan.language",
    "ruleset_scan.baseline",
    "on_failure",
    "timeout",
}


def _sanitize_policy_validation_details(details: object) -> list[dict[str, str]]:
    """Sanitize PolicyValidationError details to prevent path or secret leaks on MCP wire."""
    if not isinstance(details, list):
        return []
    sanitized: list[dict[str, str]] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", ""))
        safe_field = field if field in _ALLOWED_POLICY_FIELDS else "policy"
        raw_msg = str(item.get("message", ""))

        if "is required" in raw_msg:
            safe_msg = f"{safe_field} is required"
        elif "must be a boolean" in raw_msg:
            safe_msg = f"{safe_field} must be a boolean"
        elif "must be a string" in raw_msg:
            safe_msg = f"{safe_field} must be a string or null"
        elif "must not be empty" in raw_msg:
            safe_msg = f"{safe_field} must not be empty"
        elif "must be a positive integer" in raw_msg:
            safe_msg = f"{safe_field} must be a positive integer"
        elif "must be valid JSON" in raw_msg:
            safe_msg = f"{safe_field} must be valid JSON"
        elif "must be a JSON object" in raw_msg or "must be an object" in raw_msg:
            safe_msg = f"{safe_field} must be a JSON object"
        elif "must equal 1" in raw_msg:
            safe_msg = f"{safe_field} must equal 1"
        elif "must be one of" in raw_msg:
            safe_msg = f"{safe_field} must be one of rollback, warn, or fail"
        elif "must be provided when enabled" in raw_msg:
            safe_msg = f"{safe_field} must be provided when enabled"
        elif "within the policy directory" in raw_msg:
            safe_msg = f"{safe_field} path must be within the policy directory"
        elif "does not exist" in raw_msg:
            safe_msg = f"{safe_field} path does not exist"
        else:
            safe_msg = f"Invalid {safe_field} specification"

        sanitized.append({"field": safe_field, "message": safe_msg})
    return sanitized


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


def _embedded_rewrite_available() -> bool:
    try:
        from tensor_grep.rust_core import ast_rewrite_apply_json, ast_rewrite_plan_json
    except Exception:
        return False
    return callable(ast_rewrite_apply_json) and callable(ast_rewrite_plan_json)


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


def _extract_rewrite_error_message(
    stderr: str,
    fallback: str,
    *,
    code: str | None = None,
) -> str:
    if stderr:
        sys.stderr.write(f"[mcp] native rewrite stderr: {stderr}\n")
        sys.stderr.flush()
    if code is None and stderr:
        code, _ = _classify_native_rewrite_failure(stderr, returncode=1)
    if code == "pattern_error":
        return "Invalid rewrite pattern or syntax."
    if code == "io_error":
        return "Filesystem I/O or permission error during rewrite."
    if code == "native_internal_error":
        return "Native rewrite engine internal error."
    if code == "invalid_input":
        return "Invalid rewrite input or options."
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
    native_binary: str | Path | None = None,
) -> list[str]:
    if native_binary is not None:
        binary_str = str(native_binary)
    else:
        try:
            binary_str = str(_self.resolve_native_tg_binary())
        except Exception as exc:
            _log_tool_exception("resolve_native_tg_binary", exc)
            binary_str = "tg"
    command = [
        binary_str,
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


def _build_index_search_command(
    *, pattern: str, path: str, native_binary: str | Path | None = None
) -> list[str]:
    if native_binary is not None:
        binary_str = str(native_binary)
    else:
        try:
            binary_str = str(_self.resolve_native_tg_binary())
        except Exception as exc:
            _log_tool_exception("resolve_native_tg_binary", exc)
            binary_str = "tg"
    return [
        binary_str,
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
        completed = _self._run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        _log_tool_exception("rewrite_subprocess", exc)
        return _rewrite_error(
            "Rewrite command binary not found.", code="unavailable", retryable=True
        )
    except OSError as exc:
        _log_tool_exception("rewrite_subprocess", exc)
        return _rewrite_error(
            f"Failed to execute rewrite command: {exc.__class__.__name__}",
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
                code=code,
            ),
            code=code,
            retryable=retryable,
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return _rewrite_error("Rewrite command produced no JSON output.", code="invalid_output")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log_tool_exception("rewrite_subprocess_json", exc)
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
        _log_tool_exception("embedded_rewrite_import", exc)
        return _rewrite_error(
            f"Embedded native rewrite support unavailable: {exc.__class__.__name__}",
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
        _log_tool_exception("embedded_rewrite_execution", exc)
        code, retryable = _classify_native_rewrite_failure(exc, returncode=1)
        return _rewrite_error(
            f"Embedded rewrite {mode} failed: {exc.__class__.__name__}",
            code=code,
            retryable=retryable,
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log_tool_exception("embedded_rewrite_json", exc)
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
    native_tg, _native_error = _self._resolve_native_tg_binary_for_mcp()
    if native_tg is None:
        if not _self._embedded_rewrite_available():
            return _native_unavailable_error(
                tool="tg_rewrite_plan",
                payload=_rewrite_envelope(),
                message=(
                    "tg_rewrite_plan requires a standalone native tg binary "
                    "or embedded native rewrite support."
                ),
            )
        return _self._execute_embedded_rewrite_json(
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
        native_binary=native_tg,
    )
    return _execute_rewrite_json_command(command)


def execute_rewrite_plan_json(
    *,
    pattern: str,
    replacement: str,
    lang: str,
    path: str = ".",
) -> tuple[str, int]:
    try:
        validation_error = _self._validate_rewrite_inputs(pattern, lang, path)
        if validation_error:
            return _rewrite_error(validation_error, code="invalid_input"), 1
        pattern = _restore_variadic_metavar_escaping(pattern)
        replacement = _restore_variadic_metavar_escaping(replacement)

        rewrite_json = _self._produce_rewrite_plan_json(
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
    except Exception as exc:
        _log_tool_exception("execute_rewrite_plan_json", exc)
        return (
            _rewrite_error(
                f"Rewrite plan failed: {exc.__class__.__name__}",
                code="internal_error",
            ),
            1,
        )


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
    plan_json = _self._produce_rewrite_plan_json(
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

    validation_error = _self._validate_rewrite_inputs(pattern, lang, path)
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
        except PathConfinementError as exc:
            return _rewrite_error(str(exc), code="invalid_input"), 1
        except ValueError as exc:
            _log_tool_exception("execute_rewrite_apply_json", exc)
            return _rewrite_error("Invalid audit_manifest path", code="invalid_input"), 1

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
        except PathConfinementError as exc:
            return _rewrite_error(str(exc), code="invalid_input"), 1
        except ValueError as exc:
            _log_tool_exception("execute_rewrite_apply_json", exc)
            return _rewrite_error("Invalid policy path", code="invalid_input"), 1
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
            _log_tool_exception("load_apply_policy", exc)
            return (
                _rewrite_error(
                    "Policy validation commands are not allowed.",
                    code="unsupported_option",
                    retryable=False,
                ),
                1,
            )
        except FileNotFoundError as exc:
            _log_tool_exception("load_apply_policy", exc)
            return _rewrite_error("Policy file not found.", code="not_found"), 1
        except PolicyValidationError as exc:
            _log_tool_exception("load_apply_policy", exc)
            try:
                print(
                    f"[tensor-grep-mcp] load_apply_policy details: {json.dumps(exc.details)}",
                    file=sys.stderr,
                )
            except Exception:
                pass
            return (
                json.dumps(
                    _rewrite_error_payload(
                        "Policy validation failed.",
                        code="invalid_policy",
                        details=_sanitize_policy_validation_details(exc.details),
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

    native_tg, _native_error = _self._resolve_native_tg_binary_for_mcp()
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
        if not _self._embedded_rewrite_available():
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
                _log_tool_exception("create_checkpoint", exc)
                return (
                    _rewrite_error(
                        f"Failed to create checkpoint: {exc.__class__.__name__}",
                        code="checkpoint",
                    ),
                    1,
                )
        rewrite_json = _self._execute_embedded_rewrite_json(
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
            native_binary=native_tg,
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

    try:
        policy_payload, exit_code = evaluate_apply_policy(
            rewrite_payload,
            loaded_policy,
            path=path,
        )
        return json.dumps(policy_payload, indent=2), exit_code
    except Exception as exc:
        _log_tool_exception("evaluate_apply_policy", exc)
        payload = dict(rewrite_payload)
        payload["policy_evaluation_error"] = f"Policy evaluation failed: {exc.__class__.__name__}"
        payload["error"] = {
            "code": "policy_evaluation_failed",
            "message": (
                "Policy evaluation failed after rewrite application. "
                "Edits may have already been applied."
            ),
        }
        return json.dumps(payload, indent=2), 1


def _execute_rewrite_diff_command(command: list[str]) -> str:
    try:
        completed = _self._run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        _log_tool_exception("rewrite_diff_subprocess", exc)
        return _rewrite_error(
            "Rewrite diff command binary not found.", code="unavailable", retryable=True
        )
    except OSError as exc:
        _log_tool_exception("rewrite_diff_subprocess", exc)
        return _rewrite_error(
            f"Failed to execute rewrite diff command: {exc.__class__.__name__}",
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
                code=code,
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
        completed = _self._run_rewrite_subprocess(command)
    except FileNotFoundError as exc:
        _log_tool_exception("index_search_subprocess", exc)
        return _index_search_error(
            "Index search command binary not found.",
            code="unavailable",
            pattern=pattern,
            path=path,
        )
    except OSError as exc:
        _log_tool_exception("index_search_subprocess", exc)
        return _index_search_error(
            f"Failed to execute index search command: {exc.__class__.__name__}",
            code="execution_failed",
            pattern=pattern,
            path=path,
        )

    # Task #276 slice C0: an incomplete-but-honest scan exits 2 and says so. Treating that as
    # `invalid_input` would be the worst outcome on this surface -- a TOTAL loss of results for an
    # MCP client, on a run that actually found matches. Allow-list only: a bare `== 2` tolerance
    # would swallow a genuine regex/engine failure too.
    disclosed_partial = completed.returncode == 2 and disclosed_incomplete(
        completed.stdout, completed.stderr
    )
    if completed.returncode != 0 and not disclosed_partial:
        return _index_search_error(
            _extract_rewrite_error_message(
                completed.stderr or "",
                f"Index search command failed with exit code {completed.returncode}.",
                code="invalid_input",
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
    except json.JSONDecodeError as exc:
        _log_tool_exception("index_search_subprocess_json", exc)
        return _index_search_error(
            "Index search command produced invalid JSON output.",
            code="invalid_output",
            pattern=pattern,
            path=path,
        )

    return _normalize_index_search_json_payload(payload, pattern=pattern, path=path)
