"""MCP tool family: native AST rewrite (plan/apply/diff), inline/ruleset ast-grep
scanning, index search, rewrite audit manifests, review bundles, and edit checkpoints.

Split out of mcp_server.py (docs/design/2026-08-19-split-floor-escape.md, Route A) as a
pure code move: no wire-surface change. Every relocated function keeps its original
``_self.NAME(...)`` calls verbatim, but ``_self`` here is bound to the mcp_server module
object (not this one) -- so a test that patches ``mcp_server.tg_rewrite_apply`` still
reaches the patched callable because the meta-tool ``tg_rewrite`` (kept in
mcp_server.py) dispatches via ``_self.tg_rewrite_apply(...)``, which resolves against
mcp_server namespace regardless of where the function object physically lives.

The native AST rewrite/index-search ENGINE (subprocess/plan-digest helpers with no MCP
tool decorator) lives in the sibling module mcp_rewrite_tools.py; the plain names
imported from it below are not monkeypatched anywhere in this repo's test suite
(scripts/bare_call_ratchet.py confirms 0 bare calls to a patched name repo-wide), so a
direct cross-module import is safe.
"""

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tensor_grep.backends.ast_backend import normalize_ast_language
from tensor_grep.cli.main import (
    _build_rulesets_payload,
    _load_inline_rule_specs,
)
from tensor_grep.cli.rule_packs import resolve_rule_pack
from tensor_grep.cli.scan_guardrails import BroadScanRefusedError
from tensor_grep.core.pipeline import ConfigurationError

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]

from tensor_grep.cli.mcp_rewrite_tools import (
    _audit_diff_error as _audit_diff_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _audit_history_error as _audit_history_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _audit_manifest_error as _audit_manifest_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _build_index_search_command as _build_index_search_command,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _build_rewrite_command as _build_rewrite_command,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _execute_index_search_command as _execute_index_search_command,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _execute_rewrite_diff_command as _execute_rewrite_diff_command,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _index_search_envelope as _index_search_envelope,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _index_search_error as _index_search_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _mcp_validation_commands_allowed as _mcp_validation_commands_allowed,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _native_unavailable_error as _native_unavailable_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _resolve_native_tg_binary_for_mcp as _resolve_native_tg_binary_for_mcp,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _review_bundle_error as _review_bundle_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _rewrite_envelope as _rewrite_envelope,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _rewrite_error as _rewrite_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _ruleset_scan_error as _ruleset_scan_error,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _validate_index_search_inputs as _validate_index_search_inputs,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    _validate_rewrite_inputs as _validate_rewrite_inputs,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    execute_rewrite_apply_json as execute_rewrite_apply_json,
)
from tensor_grep.cli.mcp_rewrite_tools import (
    execute_rewrite_plan_json as execute_rewrite_plan_json,
)
from tensor_grep.cli.mcp_server import (
    _MAX_INLINE_RULES as _MAX_INLINE_RULES,
)
from tensor_grep.cli.mcp_server import (
    _MAX_INLINE_RULES_CHARS as _MAX_INLINE_RULES_CHARS,
)
from tensor_grep.cli.mcp_server import (
    _TG_MCP_SERVER_CONTRACT_VERSION as _TG_MCP_SERVER_CONTRACT_VERSION,
)
from tensor_grep.cli.mcp_server import (
    PathConfinementError as PathConfinementError,
)
from tensor_grep.cli.mcp_server import (
    _confine_mcp_path as _confine_mcp_path,
)
from tensor_grep.cli.mcp_server import (
    _confine_read_path as _confine_read_path,
)
from tensor_grep.cli.mcp_server import (
    _confine_write_path as _confine_write_path,
)
from tensor_grep.cli.mcp_server import (
    _json_output_version as _json_output_version,
)
from tensor_grep.cli.mcp_server import (
    _log_tool_exception as _log_tool_exception,
)
from tensor_grep.cli.mcp_server import (
    _mcp_root as _mcp_root,
)
from tensor_grep.cli.mcp_server import (
    _register_legacy_tool as _register_legacy_tool,
)
from tensor_grep.cli.mcp_server import (
    _sanitized_tool_error as _sanitized_tool_error,
)


@_register_legacy_tool  # type: ignore
def tg_rulesets() -> str:
    """Return metadata for built-in security and compliance rulesets."""
    try:
        return _self._inject_mcp_contract_fields(json.dumps(_build_rulesets_payload(), indent=2))
    except Exception as exc:
        _log_tool_exception("tg_rulesets", exc)
        return json.dumps(
            {
                "version": _json_output_version(),
                "error": {
                    "code": "internal_error",
                    "message": f"Rulesets lookup failed: {exc.__class__.__name__}",
                },
            },
            indent=2,
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _ruleset_scan_error(
            str(exc),
            code="invalid_input",
            ruleset=ruleset,
            path="[refused]",
        )
    except ValueError as exc:
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            f"Invalid path: {path}",
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
            _log_tool_exception("tg_ruleset_scan", exc)
            return _ruleset_scan_error(
                "Invalid inline rules YAML", code="invalid_input", ruleset=ruleset, path=path
            )
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
            _log_tool_exception("tg_ruleset_scan", exc)
            return _ruleset_scan_error(
                f"Unsupported AST language {language}",
                code="invalid_input",
                ruleset=ruleset,
                path=path,
            )
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
            _log_tool_exception("tg_ruleset_scan", exc)
            return _ruleset_scan_error(
                f"Invalid ruleset: {ruleset}",
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
    except PathConfinementError as exc:
        return _ruleset_scan_error(str(exc), code="invalid_input", ruleset=ruleset, path=path)
    except ValueError as exc:
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "Invalid scan path configuration", code="invalid_input", ruleset=ruleset, path=path
        )
    try:
        payload = _self._run_ast_scan_payload(
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
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "broad AST scan refused: root directory matches broad scan criteria. Pass --allow-broad-generated-scan to override.",
            code="broad_scan_refused",
            ruleset=ruleset,
            path=path,
        )
    except ValueError as exc:
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "Invalid scan parameter",
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
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "AST scan backend unavailable. Run 'tg doctor' or install ast extras.",
            code="unavailable",
            ruleset=ruleset,
            path=path,
        )
    except OSError as exc:
        # [SEC] a caller-supplied baseline_path/suppressions_path that is unreadable (a directory,
        # permission-denied, a race-deleted file) makes _load_ruleset_baseline/_load_ruleset_
        # suppressions' read_text raise OSError/PermissionError/IsADirectoryError (NOT a
        # ValueError) -- was a raw traceback. Fail closed. (audit #95 Part-2 round-4 gate.)
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "unreadable scan path",
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
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            "scan backend failed",
            code="backend_error",
            ruleset=ruleset,
            path=path,
        )
    except Exception as exc:
        _log_tool_exception("tg_ruleset_scan", exc)
        return _ruleset_scan_error(
            f"Ruleset scan failed: {exc.__class__.__name__}",
            code="internal_error",
            ruleset=ruleset,
            path=path,
        )
    # M14: the scan success payload is assembled inline and crossed the wire un-stamped.
    return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _index_search_error(
            str(exc), code="invalid_input", pattern=pattern, path="[refused]"
        )
    except ValueError as exc:
        _log_tool_exception("tg_index_search", exc)
        return _index_search_error(
            "Invalid path", code="invalid_input", pattern=pattern, path="[refused]"
        )

    validation_error = _validate_index_search_inputs(pattern, path)
    if validation_error:
        return _index_search_error(
            validation_error,
            code="invalid_input",
            pattern=pattern,
            path=path,
        )

    try:
        native_tg, _native_error = _self._resolve_native_tg_binary_for_mcp()
        if native_tg is None:
            payload = _index_search_envelope()
            payload["query"] = pattern
            payload["path"] = path
            return _native_unavailable_error(tool="tg_index_search", payload=payload)

        command = _build_index_search_command(pattern=pattern, path=path, native_binary=native_tg)
        return _execute_index_search_command(command, pattern=pattern, path=path)
    except Exception as exc:
        _log_tool_exception("tg_index_search", exc)
        return _index_search_error(
            f"Index search failed: {exc.__class__.__name__}",
            code="internal_error",
            pattern=pattern,
            path=path,
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _rewrite_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_rewrite_plan", exc)
        return _rewrite_error("Invalid path", code="invalid_input")

    validation_error = _self._validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    # Route via `_self` (bound to mcp_server) rather than a bare call: this name is
    # re-exported into mcp_server.py and `ast_workflows.py` patches it there too.
    try:
        payload, _exit_code = _self.execute_rewrite_plan_json(
            pattern=pattern,
            replacement=replacement,
            lang=lang,
            path=path,
        )
        return payload
    except Exception as exc:
        _log_tool_exception("tg_rewrite_plan", exc)
        return _rewrite_error(
            f"Rewrite plan failed: {exc.__class__.__name__}",
            code="internal_error",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _rewrite_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_rewrite_apply", exc)
        return _rewrite_error("Invalid path", code="invalid_input")

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
    # Route via `_self` (bound to mcp_server): tests patch
    # "tensor_grep.cli.mcp_server.execute_rewrite_apply_json" directly.
    try:
        payload, _exit_code = _self.execute_rewrite_apply_json(
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
    except Exception as exc:
        _log_tool_exception("tg_rewrite_apply", exc)
        return _rewrite_error(
            f"Rewrite apply failed: {exc.__class__.__name__}",
            code="internal_error",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _audit_manifest_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_audit_manifest_verify", exc)
        return _audit_manifest_error("Invalid manifest path", code="invalid_input")

    try:
        # M14: verify_audit_manifest_json serializes a flat CLI payload with no MCP
        # envelope -- stamp at the tool seam (the error arms above already embed the
        # const via _audit_manifest_error).
        return _self._inject_mcp_contract_fields(
            verify_audit_manifest_json(
                manifest_path,
                signing_key=signing_key,
                previous_manifest=previous_manifest,
            )
        )
    except FileNotFoundError as exc:
        _log_tool_exception("tg_audit_manifest_verify", exc)
        return _audit_manifest_error("Manifest file not found", code="not_found")
    except ValueError as exc:
        _log_tool_exception("tg_audit_manifest_verify", exc)
        return _audit_manifest_error("Invalid manifest payload", code="invalid_input")
    except Exception as exc:
        _log_tool_exception("tg_audit_manifest_verify", exc)
        return _audit_manifest_error(
            "Audit manifest verification failed due to an internal error.",
            code="internal_error",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _audit_history_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_audit_history", exc)
        return _audit_history_error("Invalid path", code="invalid_input")

    try:
        return _self._inject_mcp_contract_fields(
            json.dumps(list_audit_history_payload(path), indent=2)
        )
    except FileNotFoundError as exc:
        _log_tool_exception("tg_audit_history", exc)
        return _audit_history_error("Path not found", code="not_found")
    except ValueError as exc:
        _log_tool_exception("tg_audit_history", exc)
        return _audit_history_error("Invalid audit history request", code="invalid_input")
    except Exception as exc:
        _log_tool_exception("tg_audit_history", exc)
        return _audit_history_error(
            "Audit history failed due to an internal error.",
            code="internal_error",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _audit_diff_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_audit_diff", exc)
        return _audit_diff_error("Invalid manifest path", code="invalid_input")

    try:
        return _self._inject_mcp_contract_fields(
            json.dumps(
                diff_audit_manifests_payload(previous_manifest, current_manifest),
                indent=2,
            )
        )
    except FileNotFoundError as exc:
        _log_tool_exception("tg_audit_diff", exc)
        return _audit_diff_error("Manifest file not found", code="not_found")
    except (json.JSONDecodeError, ValueError) as exc:
        _log_tool_exception("tg_audit_diff", exc)
        return _audit_diff_error("Invalid JSON in manifest", code="invalid_json")
    except Exception as exc:
        _log_tool_exception("tg_audit_diff", exc)
        return _audit_diff_error(
            "Audit diff failed due to an internal error.",
            code="internal_error",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_input",
            routing_reason="review-bundle-create",
        )
    except ValueError as exc:
        _log_tool_exception("tg_review_bundle_create", exc)
        return _review_bundle_error(
            "Invalid bundle input path",
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
        except PathConfinementError as exc:
            return _review_bundle_error(
                str(exc),
                code="invalid_input",
                routing_reason="review-bundle-create",
            )
        except ValueError as exc:
            _log_tool_exception("tg_review_bundle_create", exc)
            return _review_bundle_error(
                "Invalid output path",
                code="invalid_input",
                routing_reason="review-bundle-create",
            )

    try:
        # M14: create_review_bundle_json serializes a flat CLI payload with no MCP envelope --
        # stamp at the tool seam (the error arms above already embed the const via
        # _review_bundle_error).
        return _self._inject_mcp_contract_fields(
            create_review_bundle_json(
                manifest_path,
                scan_path=scan_path,
                checkpoint_id=checkpoint_id,
                previous_manifest=previous_manifest,
                output_path=output_path,
            )
        )
    except FileNotFoundError as exc:
        _log_tool_exception("tg_review_bundle_create", exc)
        return _review_bundle_error(
            "Bundle input file not found",
            code="not_found",
            routing_reason="review-bundle-create",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        _log_tool_exception("tg_review_bundle_create", exc)
        return _review_bundle_error(
            "Invalid JSON in bundle inputs",
            code="invalid_json",
            routing_reason="review-bundle-create",
        )
    except Exception as exc:
        _log_tool_exception("tg_review_bundle_create", exc)
        return _review_bundle_error(
            "Review bundle creation failed due to an internal error.",
            code="internal_error",
            routing_reason="review-bundle-create",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _review_bundle_error(
            str(exc),
            code="invalid_input",
            routing_reason="review-bundle-verify",
        )
    except ValueError as exc:
        _log_tool_exception("tg_review_bundle_verify", exc)
        return _review_bundle_error(
            "Invalid bundle path",
            code="invalid_input",
            routing_reason="review-bundle-verify",
        )

    try:
        # M14: verify_review_bundle_json serializes a flat CLI payload with no MCP envelope --
        # stamp at the tool seam (the error arms above already embed the const via
        # _review_bundle_error).
        return _self._inject_mcp_contract_fields(verify_review_bundle_json(bundle_path))
    except FileNotFoundError as exc:
        _log_tool_exception("tg_review_bundle_verify", exc)
        return _review_bundle_error(
            "Bundle file not found",
            code="not_found",
            routing_reason="review-bundle-verify",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        _log_tool_exception("tg_review_bundle_verify", exc)
        return _review_bundle_error(
            "Invalid JSON in review bundle",
            code="invalid_json",
            routing_reason="review-bundle-verify",
        )
    except Exception as exc:
        _log_tool_exception("tg_review_bundle_verify", exc)
        return _review_bundle_error(
            "Review bundle verification failed due to an internal error.",
            code="internal_error",
            routing_reason="review-bundle-verify",
        )


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": "[refused]",
            },
            indent=2,
        )
    except ValueError as exc:
        _log_tool_exception("tg_checkpoint_create", exc)
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": f"Invalid path: {path}"},
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
                "error": _sanitized_tool_error("tg_checkpoint_create", exc),
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


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": "[refused]",
            },
            indent=2,
        )
    except ValueError as exc:
        _log_tool_exception("tg_checkpoint_list", exc)
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": f"Invalid path: {path}"},
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
                "error": _sanitized_tool_error("tg_checkpoint_list", exc),
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


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": str(exc)},
                "path": "[refused]",
                "checkpoint_id": checkpoint_id,
            },
            indent=2,
        )
    except ValueError as exc:
        _log_tool_exception("tg_checkpoint_undo", exc)
        return json.dumps(
            {
                "version": _json_output_version(),
                "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {"code": "invalid_input", "message": f"Invalid path: {path}"},
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
                "error": _sanitized_tool_error("tg_checkpoint_undo", exc),
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


@_register_legacy_tool  # type: ignore
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
    except PathConfinementError as exc:
        return _rewrite_error(str(exc), code="invalid_input")
    except ValueError as exc:
        _log_tool_exception("tg_rewrite_diff", exc)
        return _rewrite_error("Invalid path", code="invalid_input")

    validation_error = _self._validate_rewrite_inputs(pattern, lang, path)
    if validation_error:
        return _rewrite_error(validation_error, code="invalid_input")

    try:
        native_tg, _native_error = _self._resolve_native_tg_binary_for_mcp()
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
            native_binary=native_tg,
        )
        return _execute_rewrite_diff_command(command)
    except Exception as exc:
        _log_tool_exception("tg_rewrite_diff", exc)
        return _rewrite_error(
            f"Rewrite diff failed: {exc.__class__.__name__}",
            code="internal_error",
        )
