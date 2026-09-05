from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from tensor_grep.backends.ast_backend import is_native_ast_language, normalize_ast_language
from tensor_grep.backends.base import BackendExecutionError

# NOTE: several names below are re-exported (used only via `ast_workflows.X` from
# tests/other modules, not by any remaining code in this file) rather than
# genuinely unused -- each is imported `as` itself so ruff's F401 recognizes the
# re-export as intentional instead of flagging it for removal (removing it would
# silently break every `ast_workflows._name` qualified test access).
from tensor_grep.cli.ast_workflow_rules import (
    _ast_run_remediation_lines as _ast_run_remediation_lines,
)
from tensor_grep.cli.ast_workflow_rules import (
    _batch_search_snippets,
    _collect_candidate_files,
    _describe_ast_backend_mode,
    _describe_ast_backend_modes,
    _emit_ast_run_remediation,
    _extract_rule_pattern,
    _inject_run_json_fields,
    _rule_needs_ast_grep_wrapper,
    _safe_stdout_line,
    _suffix_for_language,
    _warn_windows_single_quote_pattern,
)
from tensor_grep.cli.ast_workflow_rules import (
    _extract_rule_member_patterns as _extract_rule_member_patterns,
)
from tensor_grep.cli.ast_workflow_rules import (
    _iter_yaml_files as _iter_yaml_files,
)

# EXPLICIT re-exports (`X as X`). cli/main.py imports these three THROUGH this facade,
# and mypy runs with implicit_reexport off, so a plain `from ... import X` here is a
# private binding as far as the type checker is concerned -- runtime resolves it fine
# and `mypy` fails with attr-defined. Caught by CI, not locally.
from tensor_grep.cli.ast_workflow_rules import (
    _load_ast_project_data as _load_ast_project_data,
)
from tensor_grep.cli.ast_workflow_rules import (
    _load_rule_specs_and_meta as _load_rule_specs_and_meta,
)
from tensor_grep.cli.ast_workflow_rules import (
    _load_test_data_and_meta as _load_test_data_and_meta,
)
from tensor_grep.cli.ast_workflow_rules import (
    _match_node_identity as _match_node_identity,
)
from tensor_grep.cli.ast_workflow_rules import (
    _pattern_is_native_shaped as _pattern_is_native_shaped,
)
from tensor_grep.cli.ast_workflow_rules import (
    _precompute_orchestration_hints as _precompute_orchestration_hints,
)
from tensor_grep.cli.ast_workflow_rules import (
    _rule_member_patterns as _rule_member_patterns,
)
from tensor_grep.cli.ast_workflow_rules import (
    _select_ast_backend_name_for_pattern as _select_ast_backend_name_for_pattern,
)
from tensor_grep.cli.ast_workflow_rules import (
    _select_ast_backend_name_for_rule as _select_ast_backend_name_for_rule,
)
from tensor_grep.cli.scan_guardrails import BroadScanRefusedError, ensure_scan_not_broad

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine, SearchResult

# Global caches
_YAML_MODULE: Any = None
_YAML_LOADER: Any = None
_BACKEND_AVAILABILITY: dict[tuple[str, type[Any], str], bool] = {}
_SUPPORTED_NATIVE_PATTERN_RE = None
_CACHED_BACKENDS: dict[tuple[str, type[Any]], Any] = {}
_NORM_CACHE: dict[str, str] = {}

_SUFFIX_CACHE = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "rust": ".rs",
    "rs": ".rs",
}


_AST_GREP_REMEDIATION = (
    " -- install the ast-grep CLI to enable it: `pip install ast-grep-cli` "
    "(or `npm i -g @ast-grep/cli`). A stock `pip install tensor-grep` does not include it, "
    "so every built-in `tg scan --ruleset` fails until it is on PATH."
)
"""Remediation appended to EVERY ast-grep-unavailable refusal.

One constant, not a literal per site: a remediation present on one reachable path and absent
on another is the LOGGED-DEGRADE failure this repo has already paid for -- the user meets
whichever path their input takes.

`AstGrepWrapperBackend.is_available()` probes for an `ast-grep`/`sg` BINARY on PATH, so the
CLI distribution is what must be named; `ast-grep-py` ships Python bindings and would be
advice that does not fix the problem. Verified in a clean container on the PUBLISHED wheel:
`tg scan --ruleset subprocess-safe` exits 1, `pip install ast-grep-cli` puts `ast-grep` on
PATH, and the same command then exits 0 with `matched_rules: 1`.
"""


def _fast_norm(p: str) -> str:
    """Fast path normalization for string comparison on Windows."""
    if p not in _NORM_CACHE:
        # normpath + lower is usually enough for absolute paths we control
        _NORM_CACHE[p] = os.path.normpath(p).lower()
    return _NORM_CACHE[p]


def execute_rewrite_apply_json(*args: Any, **kwargs: Any) -> tuple[str, int]:
    """
    Lazy wrapper for execute_rewrite_apply_json to allow monkeypatching in tests
    without paying the import cost at module load time.
    """
    from tensor_grep.cli.mcp_server import execute_rewrite_apply_json as real_func

    return real_func(*args, **kwargs)


def execute_rewrite_plan_json(*args: Any, **kwargs: Any) -> tuple[str, int]:
    """
    Lazy wrapper for execute_rewrite_plan_json to allow monkeypatching in tests
    without paying the import cost at module load time.
    """
    from tensor_grep.cli.mcp_server import execute_rewrite_plan_json as real_func

    return real_func(*args, **kwargs)


def _get_yaml() -> tuple[Any, Any]:
    global _YAML_MODULE, _YAML_LOADER
    if _YAML_MODULE is None or _YAML_LOADER is None:
        import yaml

        _YAML_MODULE = yaml
        _YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return _YAML_MODULE, _YAML_LOADER


def _reset_yaml_cache() -> None:
    global _YAML_MODULE, _YAML_LOADER
    _YAML_MODULE = None
    _YAML_LOADER = None


def _load_yaml_dict(path: Path) -> dict[str, object]:
    yaml_mod, loader = _get_yaml()
    with path.open(encoding="utf-8") as handle:
        try:
            loaded = yaml_mod.load(handle, Loader=loader) or {}
        except yaml_mod.YAMLError:
            _reset_yaml_cache()
            yaml_mod, _loader = _get_yaml()
            handle.seek(0)
            try:
                loaded = yaml_mod.safe_load(handle) or {}
            except yaml_mod.YAMLError as exc:
                detail = str(exc).splitlines()[0] if str(exc).strip() else "parse error"
                raise ValueError(f"Invalid YAML in {path}: {detail}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML in {path} must be a mapping.")
    return loaded


def _normalize_string_list(value: object, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return fallback


def _get_cache_dir(root_dir: Path) -> Path:
    return root_dir / ".tg_cache" / "ast"


# M16 (F3): twin of PROJECT_DATA_V6_SCHEMA_VERSION in
# rust_core/src/backend_ast_workflow.rs. Python is a compatibility READER of the
# Rust-written `project_data_v6.json` cache; a legacy-schema cache carries
# `rule_specs` WITHOUT composite members and severity/message even when
# mtime-fresh, so both sides reject (rebuild from source) on mismatch. Bump
# BOTH constants together whenever the persisted rule-spec schema changes.
_PROJECT_DATA_CACHE_SCHEMA_VERSION = 2


def run_command(
    pattern: str,
    path: str | None = None,
    *,
    rewrite: str | None = None,
    lang: str | None = None,
    apply: bool = False,
    verify: bool = False,
    json_mode: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
    policy: str | None = None,
    interactive: bool = False,
    filter_regex: str | None = None,
    files_with_matches: bool = False,
    selector: str | None = None,
    strictness: str | None = None,
    stdin: bool = False,
    globs: list[str] | None = None,
) -> int:
    # M4: ``--batch-rewrite`` is handled entirely by the native Rust binary
    # (``rust_core/src/main.rs`` → ``parse_batch_rewrite_config_value``).
    # It is NOT routed through this Python function; bootstrap.py dispatches
    # ``tg run --batch-rewrite`` straight to ``_run_native_tg_command``.
    #
    # The ``--batch-rewrite`` config file format is:
    #
    #   {
    #     "rewrites": [
    #       {"pattern": "<ast-pattern>", "replacement": "<replacement>", "lang": "<language>"},
    #       ...
    #     ],
    #     "verify": false   // optional boolean, default false
    #   }
    #
    # Passing a JSON array (``[ ... ]``) instead of an object produces the error
    # "invalid batch rewrite config field `$`: expected object" from the Rust
    # parser (rust_core/src/main.rs:parse_batch_rewrite_config_value, line ~6088).
    # That error originates in Rust; see cross-file FLAG below for the fix location.
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import ConfigurationError
    from tensor_grep.core.result import SearchResult

    if policy is not None and not apply and not interactive:
        print("--policy requires --apply or --interactive.", file=sys.stderr)
        return 1
    if (
        (verify or checkpoint or audit_manifest or audit_signing_key or lint_cmd or test_cmd)
        and not apply
        and not interactive
    ):
        print(
            "--verify, --checkpoint, --audit-manifest, --audit-signing-key, --lint-cmd, and "
            "--test-cmd require --apply or --interactive.",
            file=sys.stderr,
        )
        return 1
    if interactive and not rewrite:
        print("--interactive requires --rewrite.", file=sys.stderr)
        return 1
    semantic_run_options = bool(selector or strictness or stdin or globs)
    if semantic_run_options and (
        rewrite is not None
        or apply
        or verify
        or checkpoint
        or audit_manifest is not None
        or audit_signing_key is not None
        or lint_cmd is not None
        or test_cmd is not None
        or policy is not None
        or interactive
    ):
        print(
            "ast-grep semantic run options are read-only in tg run; use ast-grep directly "
            "for semantic rewrites.",
            file=sys.stderr,
        )
        return 1
    if stdin and files_with_matches:
        print("--stdin cannot be combined with --files-with-matches.", file=sys.stderr)
        return 1
    if stdin and path:
        print("--stdin cannot be combined with a PATH argument.", file=sys.stderr)
        return 1

    if files_with_matches and (
        rewrite is not None
        or apply
        or verify
        or checkpoint
        or audit_manifest is not None
        or audit_signing_key is not None
        or lint_cmd is not None
        or test_cmd is not None
        or policy is not None
        or interactive
        or json_mode
    ):
        print("--files-with-matches is a read-only text output mode.", file=sys.stderr)
        return 1

    if rewrite is not None and not apply and not interactive:
        rewrite_json, exit_code = execute_rewrite_plan_json(
            pattern=pattern,
            replacement=rewrite,
            lang=lang or "",
            path=path or ".",
        )
        _safe_stdout_line(_inject_run_json_fields(rewrite_json, "rewrite-plan"))
        return exit_code

    if (apply or interactive) and not filter_regex and not interactive:
        if rewrite is None:
            print(f"--{'apply' if apply else 'interactive'} requires --rewrite.", file=sys.stderr)
            return 1

        rewrite_json, exit_code = execute_rewrite_apply_json(
            pattern=pattern,
            replacement=rewrite,
            lang=lang or "",
            path=path or ".",
            verify=verify,
            checkpoint=checkpoint,
            audit_manifest=audit_manifest,
            audit_signing_key=audit_signing_key,
            lint_cmd=lint_cmd,
            test_cmd=test_cmd,
            policy=policy,
            # Trusted local CLI: a user who typed `tg run --apply --policy` is trusted
            # to run its lint_cmd/test_cmd (unlike the agent-steerable MCP surface).
            allow_validation_commands=True,
        )
        _safe_stdout_line(_inject_run_json_fields(rewrite_json, "apply"))
        return exit_code

    search_path = path or "."
    stdin_input = sys.stdin.read() if stdin else None
    cfg = SearchConfig(
        ast=True,
        ast_prefer_native=not semantic_run_options,
        lang=lang or ("python" if stdin else None),
        query_pattern=pattern,
        ast_selector=selector,
        ast_strictness=strictness,
        ast_stdin=stdin,
        ast_stdin_input=stdin_input,
        glob=list(globs or []),
    )
    try:
        backend = _select_ast_backend_for_pattern(cfg, pattern)
    except ConfigurationError as exc:
        # CEO#6(a): honest-error mirror of Task #166's main.py `_exit_search_error` pattern
        # (main.py's Pipeline-construction ConfigurationError handler). A `$`-metavariable
        # (wrapper-shaped) pattern requires the ast-grep `sg` binary; when neither the wrapper
        # nor a native-shaped fallback can serve it, _select_ast_backend_for_pattern
        # deliberately raises ConfigurationError (Backend Fail-Closed Contract) rather than
        # silently rerouting to native tree-sitter, which speaks a different query DSL and
        # would return wrong/empty results instead of an honest error (task #141 -- no
        # translation shim). This call previously sat outside any try/except, so the
        # ConfigurationError propagated as a raw, uncaught Python traceback; catch it here and
        # surface a clean, actionable message + exit 2 instead, like every other expected
        # run_command error path. Only this specific, deliberate exception type is caught -- a
        # genuinely unexpected exception still surfaces loudly.
        message = (
            f"{exc} This pattern needs a metavariable-capable ast-grep matcher: install the "
            "ast-grep `sg` binary (https://ast-grep.github.io/guide/quick-start.html) so "
            "$NAME/$$$ARGS patterns can run, or rewrite the pattern without $ metavariables "
            "to use tg's native fallback."
        )
        if json_mode:
            import json

            _safe_stdout_line(
                json.dumps({
                    "version": 1,
                    "schema_version": 1,
                    "mode": "search",
                    "total_matches": 0,
                    "ok": False,
                    "error": "configuration_error",
                    "detail": str(exc),
                    "query": pattern,
                    "path": search_path,
                })
            )
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 2
    backend_name = type(backend).__name__

    if not json_mode and not files_with_matches:
        _safe_stdout_line(f"Executing {_describe_ast_backend_mode(backend_name)} run...")

        if backend_name not in {"AstBackend", "AstGrepWrapperBackend"}:
            print(
                "Warning: AstBackend not available (requires tree-sitter or ast-grep). "
                "Falling back to CPU regex.",
                file=sys.stderr,
            )

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    try:
        if backend_name == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
            search_paths = [] if stdin else [search_path]
            result = cast(Any, backend).search_many(search_paths, pattern, config=cfg)
            all_results.matches.extend(result.matches)
            all_results.matched_file_paths.extend(result.matched_file_paths)
            all_results.total_matches += result.total_matches
            all_results.total_files = max(all_results.total_files, result.total_files)
        else:
            from tensor_grep.io.directory_scanner import DirectoryScanner

            scanner = DirectoryScanner(cfg)
            candidate_files, _, _ = _collect_candidate_files(scanner, [search_path])
            for current_file in candidate_files:
                result = backend.search(current_file, pattern, config=cfg)
                all_results.matches.extend(result.matches)
                all_results.matched_file_paths.extend(result.matched_file_paths)
                all_results.total_matches += result.total_matches
                if result.total_files > 0 or result.total_matches > 0:
                    all_results.total_files += 1
    except BackendExecutionError as exc:
        # audit M2: --selector/--strictness combinations ast-grep rejects must surface as a
        # structured error (or a clean stderr message), never a raw Python traceback.
        if json_mode:
            import json

            _safe_stdout_line(
                json.dumps({
                    "version": 1,
                    "schema_version": 1,
                    "mode": "search",
                    "total_matches": 0,
                    "ok": False,
                    "error": "backend_error",
                    "detail": str(exc),
                    "routing_backend": backend_name,
                    "routing_reason": "ast",
                    "query": pattern,
                    "path": search_path,
                })
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2

    # Filter matches
    if filter_regex:
        import re

        regex = re.compile(filter_regex)
        all_results.matches = [m for m in all_results.matches if regex.search(m.text)]
        all_results.total_matches = len(all_results.matches)

    if interactive and rewrite:
        # Perform interactive rewrites
        if not all_results.matches:
            print("No matches found to rewrite.", file=sys.stderr)
            return 0

        # Group matches by file for more natural interactive flow
        matches_by_file: dict[str, list[MatchLine]] = {}
        for match in all_results.matches:
            matches_by_file.setdefault(match.file, []).append(match)

        applied_files: set[str] = set()
        for file_path, file_matches in matches_by_file.items():
            print(f"{chr(10)}File: {file_path}")

            for m in file_matches:
                print(f"  L{m.line_number}: {m.text.strip()}")

            choice = (
                input(f"Apply rewrite to {len(file_matches)} matches in this file? [y/n/a/q]: ")
                .strip()
                .lower()
            )
            if choice == "q":
                break
            if choice == "a":
                # Apply all remaining files
                keys = list(matches_by_file.keys())
                for remaining_file in keys[keys.index(file_path) :]:
                    applied_files.add(remaining_file)
                break

            if choice in ("y", "yes", ""):
                applied_files.add(file_path)

        if not applied_files:
            print("No changes applied.")
            return 0

        total_applied = 0
        apply_failed = False
        for f in applied_files:
            rewrite_json, exit_code = execute_rewrite_apply_json(
                pattern=pattern,
                replacement=rewrite,
                lang=lang or "",
                path=f,
                verify=verify,
                checkpoint=checkpoint,
                audit_manifest=audit_manifest,
                audit_signing_key=audit_signing_key,
                lint_cmd=lint_cmd,
                test_cmd=test_cmd,
                policy=policy,
                # Trusted local CLI (interactive apply): user-invoked, so validation
                # commands are permitted here — the MCP boundary is gated separately.
                allow_validation_commands=True,
            )
            if exit_code != 0:
                apply_failed = True
                print(f"Error applying rewrite to {f}: {rewrite_json}", file=sys.stderr)
                continue
            total_applied += 1

        if apply_failed:
            print(f"Successfully applied rewrites to {total_applied} files.")
            return 1
        print(f"Successfully applied rewrites to {total_applied} files.")
        return 0

    if json_mode:
        import json

        payload = {
            "version": 1,
            "schema_version": 1,
            "mode": "stdin" if stdin else "search",
            "routing_backend": backend_name,
            "routing_reason": "ast",
            "sidecar_used": False,
            "query": pattern,
            "path": search_path,
            "total_matches": all_results.total_matches,
            "matches": [
                {
                    "file": m.file,
                    "line": m.line_number,
                    "text": m.text,
                }
                for m in all_results.matches
            ],
        }
        if all_results.total_matches == 0:
            # CEO#6(b): payload must be enriched BEFORE serialization so the additive
            # "remediation" key ships in the same JSON line -- never a second write.
            _emit_ast_run_remediation(pattern, lang, json_payload=payload)
        _safe_stdout_line(json.dumps(payload))
        if all_results.total_matches == 0:
            _warn_windows_single_quote_pattern(pattern)
            return 1
        return 0

    if files_with_matches:
        seen_paths: set[str] = set()
        ordered_paths: list[str] = []
        for match in all_results.matches:
            if match.file and match.file not in seen_paths:
                seen_paths.add(match.file)
                ordered_paths.append(match.file)
        if not ordered_paths:
            for matched_path in all_results.matched_file_paths:
                if matched_path not in seen_paths:
                    seen_paths.add(matched_path)
                    ordered_paths.append(matched_path)
        for matched_path in ordered_paths:
            _safe_stdout_line(matched_path)
        if not ordered_paths:
            # CEO#6(b): remediation goes to STDERR here (like the Windows-quote hint just
            # above it) so `--files-with-matches`' stdout stays a clean, parseable path list.
            _warn_windows_single_quote_pattern(pattern)
            _emit_ast_run_remediation(pattern, lang)
            return 1
        return 0

    from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

    _safe_stdout_line(RipgrepFormatter().format(all_results))
    if all_results.total_matches == 0:
        _warn_windows_single_quote_pattern(pattern)
        _emit_ast_run_remediation(pattern, lang)
        return 1
    return 0


def _get_cached_backend(name: str) -> Any:
    backend_class: type[Any]
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
    backend_cache: dict[tuple[str | None, str, bool, bool], Any] | None = None,
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
    requires_ast_grep_wrapper = bool(
        base_config.ast_selector
        or base_config.ast_strictness
        or base_config.ast_stdin
        or base_config.glob
    )
    pattern_kind = (
        "native"
        if (
            base_config.ast_prefer_native
            and not requires_ast_grep_wrapper
            and supports_native_pattern
            and is_native_ast_language(base_config.lang)
        )
        else "wrapper"
    )
    cache_key = (
        base_config.lang,
        pattern_kind,
        base_config.ast_prefer_native,
        requires_ast_grep_wrapper,
    )
    if backend_cache is not None and cache_key in backend_cache:
        return backend_cache[cache_key]

    from tensor_grep.core.pipeline import Pipeline

    backend: Any
    if Pipeline.__module__ == "tensor_grep.core.pipeline":
        # Prefer the ast-grep wrapper whenever it is available -- it is the stable,
        # results-defining backend for BOTH native-shaped and wrapper-shaped patterns.
        # The native tree-sitter AstBackend uses a DIFFERENT query DSL and returns
        # DIFFERENT results for the same pattern (e.g. `identifier`: native matches every
        # identifier node, the wrapper's code-pattern matches none), so it must NOT be
        # silently preferred here -- that would change `tg run`/`tg scan` results for every
        # box that has ast-grep + tree-sitter but no CUDA. Making native the CPU-perf default
        # (and reconciling the two DSLs) is tracked separately (task #141). Native is reached
        # ONLY as the ast-grep-absent fallback for native-shaped patterns.
        if _check_backend_available("AstGrepWrapperBackend"):
            backend = _get_cached_backend("AstGrepWrapperBackend")
        elif pattern_kind == "native" and _check_backend_available("AstBackend"):
            backend = _get_cached_backend("AstBackend")
        elif pattern_kind == "wrapper":
            from tensor_grep.core.pipeline import ConfigurationError

            raise ConfigurationError(
                "Explicit AST search requires AST dependencies: ast-grep wrapper backend "
                "is required for this pattern but is not available" + _AST_GREP_REMEDIATION
            )
        else:
            backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()
    else:
        backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()

    if backend_cache is not None:
        backend_cache[cache_key] = backend
    return backend


def _select_ast_backend_for_rule(
    base_config: SearchConfig,
    rule: Mapping[str, object],
    backend_cache: dict[tuple[str | None, str, bool, bool], Any] | None = None,
) -> Any:
    """Composite-aware backend selection (M16 F3): a rule's backend must serve
    EVERY member. A composite with any non-native-shaped member routes to the
    ast-grep wrapper, and FAILS CLOSED (ConfigurationError) when the wrapper is
    unavailable — never routed through a backend that only serves the first
    member. All-native composites and single-pattern rules delegate to the
    single-pattern selection on the first member.
    """
    if _rule_needs_ast_grep_wrapper(rule):
        if _check_backend_available("AstGrepWrapperBackend"):
            return _get_cached_backend("AstGrepWrapperBackend")
        from tensor_grep.core.pipeline import ConfigurationError

        raise ConfigurationError(
            "Explicit AST search requires AST dependencies: ast-grep wrapper "
            "backend is required for composite rule members but is not available"
            + _AST_GREP_REMEDIATION
        )
    return _select_ast_backend_for_pattern(
        base_config, _rule_member_patterns(rule)[0], backend_cache
    )


def scan_command(
    config: str | None = "sgconfig.yml",
    ruleset: str | None = None,
    inline_rules: str | None = None,
    path: str | None = None,
    apply: bool = False,
    json_mode: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
    policy: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "compact",
    max_tokens: int | None = None,
    include_evidence: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
    glob: list[str] | None = None,
    file_type: list[str] | None = None,
    max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
) -> int:
    from dataclasses import replace

    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult

    if inline_rules:
        yaml_mod, loader = _get_yaml()
        try:
            rules = yaml_mod.load(inline_rules, Loader=loader)
            if not isinstance(rules, list):
                rules = [rules]
            for rule in rules:
                if isinstance(rule, dict):
                    rule["language"] = normalize_ast_language(rule.get("language") or "python")
            root_dir = Path(path or ".").resolve()
            ensure_scan_not_broad(
                [str(root_dir)],
                globs=list(glob or []),
                file_types=list(file_type or []),
                max_depth=max_depth,
                allow_broad_generated_scan=allow_broad_generated_scan,
            )
            project_cfg = {
                "language": normalize_ast_language(rules[0].get("language", "python")),
                "root_dir": root_dir,
            }
            hints: dict[str, Any] = {}
            from tensor_grep.io.directory_scanner import DirectoryScanner

            cfg = SearchConfig(
                ast=True,
                ast_prefer_native=True,
                lang=cast(str, project_cfg["language"]),
                glob=list(glob or []) or None,
                file_type=list(file_type or []) or None,
                max_depth=max_depth,
            )
            scanner = DirectoryScanner(cfg)
            candidate_files, _, _ = _collect_candidate_files(
                scanner, [str(project_cfg["root_dir"])]
            )
            if not json_mode:
                print("Scanning project using inline rules...")
        except yaml_mod.YAMLError as exc:
            detail = str(exc).splitlines()[0] if str(exc).strip() else "parse error"
            print(f"Error: Invalid inline rules YAML: {detail}", file=sys.stderr)
            return 1
        except BroadScanRefusedError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except (AttributeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif ruleset:
        from tensor_grep.cli.rule_packs import resolve_rule_pack

        try:
            ruleset_meta, rules = resolve_rule_pack(ruleset, None)
            root_dir = Path(path or ".").resolve()
            ensure_scan_not_broad(
                [str(root_dir)],
                globs=list(glob or []),
                file_types=list(file_type or []),
                max_depth=max_depth,
                allow_broad_generated_scan=allow_broad_generated_scan,
            )
            project_cfg = {
                "config_path": f"builtin:{ruleset_meta['name']}",
                "root_dir": root_dir,
                "rule_dirs": [],
                "test_dirs": [],
                "language": ruleset_meta["language"],
            }
            hints = {}
            from tensor_grep.io.directory_scanner import DirectoryScanner

            cfg = SearchConfig(
                ast=True,
                ast_prefer_native=True,
                lang=cast(str, project_cfg["language"]),
                glob=list(glob or []) or None,
                file_type=list(file_type or []) or None,
                max_depth=max_depth,
            )
            scanner = DirectoryScanner(cfg)
            candidate_files, _, _ = _collect_candidate_files(
                scanner, [str(project_cfg["root_dir"])]
            )
            if not json_mode:
                print(
                    f"Scanning project using built-in ruleset {ruleset_meta['name']} ({ruleset_meta['language']})..."
                )
        except BroadScanRefusedError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            project_cfg, rules, candidate_files, _, hints = _load_ast_project_data(config)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if not json_mode:
            print(
                f"Scanning project using adaptive AST routing based on {project_cfg.get('config_path', config or 'sgconfig.yml')}..."
            )

    if not rules:
        print("Error: No valid rules found in configured rule directories.", file=sys.stderr)
        return 1

    cfg = SearchConfig(
        ast=True,
        ast_prefer_native=True,
        lang=cast(str, project_cfg["language"]),
        glob=list(glob or []) or None,
        file_type=list(file_type or []) or None,
        max_depth=max_depth,
    )
    root_dir = cast(Path, project_cfg["root_dir"])
    backend_names_used: set[str] = set()
    backend_hints = hints.get("backend_hints", {})

    wrapper_rules: list[dict[str, Any]] = []
    other_resolved: list[tuple[dict[str, Any], SearchConfig, Any]] = []

    for rule in rules:
        rule_cfg = cfg if rule["language"] == cfg.lang else replace(cfg, lang=rule["language"])
        backend_name = backend_hints.get(rule["id"])
        if backend_name and _check_backend_available(backend_name):
            backend = _get_cached_backend(backend_name)
        else:
            # M16 F3: rule-aware fallback — a composite with non-native members
            # must reach a backend that serves ALL members (or fail closed).
            backend = _select_ast_backend_for_rule(rule_cfg, rule)

        if type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(backend, "search_project"):
            wrapper_rules.append(rule)
        else:
            other_resolved.append((rule, rule_cfg, backend))

    wrapper_project_results: dict[str, SearchResult] | None = None
    if wrapper_rules:
        wrapper_backend = _get_cached_backend("AstGrepWrapperBackend")
        backend_names_used.add("AstGrepWrapperBackend")
        try:
            wrapper_project_results = wrapper_backend.search_project(
                str(root_dir), str(project_cfg.get("config_path", config or ""))
            )
        except Exception:
            # Fallback to individual search_many if search_project fails
            for rule in wrapper_rules:
                rule_cfg = (
                    cfg if rule["language"] == cfg.lang else replace(cfg, lang=rule["language"])
                )
                other_resolved.append((rule, rule_cfg, wrapper_backend))

    total_matches = 0
    matched_rules = 0
    findings = []
    import hashlib

    # Process wrapper results
    for rule in wrapper_rules:
        if wrapper_project_results is not None:
            result = wrapper_project_results.get(
                rule["id"], SearchResult(matches=[], total_files=0, total_matches=0)
            )
            rule_matches = result.total_matches
            total_matches += rule_matches
            if rule_matches > 0:
                matched_rules += 1

            matched_count = len(result.matched_file_paths)
            if matched_count == 0 and result.total_files > 0:
                matched_count = len({match.file for match in result.matches if match.file})

            if not json_mode:
                print(
                    f"[scan] rule={rule['id']} lang={rule['language']} "
                    f"matches={rule_matches} files={matched_count}"
                )

            files_list = list(result.matched_file_paths)
            if not files_list and result.total_files > 0:
                files_list = list({match.file for match in result.matches if match.file})

            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "rule_id": rule["id"],
                        "language": rule["language"],
                        "files": sorted(files_list),
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            ev_data: dict[str, list[dict[str, Any]]] = {}
            for m in result.matches:
                if m.file:
                    ev_data.setdefault(m.file, []).append({
                        "line_number": m.line_number,
                        "text": m.text[:max_evidence_snippet_chars],
                    })

            evidence = []
            if ev_data:
                for f, snips in ev_data.items():
                    item: dict[str, Any] = {"file": f, "match_count": len(snips)}
                    if include_evidence:
                        item["snippets"] = snips[:max_evidence_snippets_per_file]
                    evidence.append(item)
            elif files_list and result.total_matches > 0:
                if len(files_list) == 1:
                    item = {"file": files_list[0], "match_count": result.total_matches}
                    if include_evidence:
                        item["snippets"] = []  # best effort
                    evidence.append(item)
                else:
                    for f in files_list:
                        item = {"file": f, "match_count": 1}
                        if include_evidence:
                            item["snippets"] = []
                        evidence.append(item)

            findings.append({
                "rule_id": rule["id"],
                "language": rule["language"],
                "severity": rule.get("severity", "warning"),
                "message": rule.get("message", ""),
                "matches": rule_matches,
                "files": files_list,
                "fingerprint": fingerprint,
                "evidence": evidence,
            })

    # Process other results (native or individual wrapper)
    for rule, rule_cfg, backend in other_resolved:
        backend_names_used.add(type(backend).__name__)
        matched_files: set[str] = set()

        # M16 F1: composite (multi-pattern any-of) rules scan EVERY member and
        # count each matched AST NODE once across members, deduplicating by
        # node SPAN via `_match_node_identity` (file, start_byte, end_byte; the
        # same key the Rust scan core unions) — two distinct nodes on one line
        # each count, matching whole-config ast-grep's per-node `any` count.
        # Single-pattern rules keep the legacy per-node total accounting.
        member_patterns = _rule_member_patterns(rule)
        composite = len(member_patterns) > 1
        seen_identities: set[tuple[str, int, int]] = set()
        rule_matches = 0
        composite_ev_data: dict[str, list[dict[str, Any]]] = {}
        use_wrapper_many = type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(
            backend, "search_many"
        )

        for member_pattern in member_patterns:
            if use_wrapper_many:
                try:
                    result = cast(Any, backend).search_many(
                        [str(root_dir)], member_pattern, config=rule_cfg
                    )
                except RuntimeError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                if composite:
                    seen_identities.update(
                        _match_node_identity(match) for match in result.matches if match.file
                    )
                else:
                    rule_matches += result.total_matches
                matched_files.update(result.matched_file_paths)
                if not matched_files and result.total_files > 0:
                    matched_files.update(match.file for match in result.matches if match.file)
            else:
                for current_file in candidate_files:
                    try:
                        result = backend.search(current_file, member_pattern, config=rule_cfg)
                    except RuntimeError as exc:
                        print(f"Error: {exc}", file=sys.stderr)
                        return 1
                    if composite:
                        seen_identities.update(
                            _match_node_identity(match, fallback_file=current_file)
                            for match in result.matches
                        )
                    else:
                        rule_matches += result.total_matches
                    if result.total_files > 0 or result.total_matches > 0:
                        matched_files.add(current_file)

            member_ev: dict[str, list[dict[str, Any]]] = {}
            for m in result.matches:
                if m.file:
                    member_ev.setdefault(m.file, []).append({
                        "line_number": m.line_number,
                        "text": m.text[:max_evidence_snippet_chars],
                    })
            if composite:
                for f, snips in member_ev.items():
                    composite_ev_data.setdefault(f, []).extend(snips)
            else:
                # Legacy single-pattern semantics: evidence comes from the
                # (single) member result exactly as before.
                composite_ev_data = member_ev

        if composite:
            rule_matches = len(seen_identities)

        total_matches += rule_matches
        if rule_matches > 0:
            matched_rules += 1
        if not json_mode:
            print(
                f"[scan] rule={rule['id']} lang={rule['language']} "
                f"matches={rule_matches} files={len(matched_files)}"
            )

        files_list = list(matched_files)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "rule_id": rule["id"],
                    "language": rule["language"],
                    "files": sorted(files_list),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        evidence = []
        if composite_ev_data:
            for f, snips in composite_ev_data.items():
                item = {"file": f, "match_count": len(snips)}
                if include_evidence:
                    item["snippets"] = snips[:max_evidence_snippets_per_file]
                evidence.append(item)
        elif files_list and rule_matches > 0:
            if len(files_list) == 1:
                item = {"file": files_list[0], "match_count": rule_matches}
                if include_evidence:
                    item["snippets"] = []
                evidence.append(item)
            else:
                for f in files_list:
                    item = {"file": f, "match_count": 1}
                    if include_evidence:
                        item["snippets"] = []
                    evidence.append(item)

        findings.append({
            "rule_id": rule["id"],
            "language": rule["language"],
            "severity": rule.get("severity", "warning"),
            "message": rule.get("message", ""),
            "matches": rule_matches,
            "files": files_list,
            "fingerprint": fingerprint,
            "evidence": evidence,
        })

    if json_mode:
        payload: dict[str, Any] = {
            "version": 1,
            "schema_version": 1,
            "routing_backend": next(iter(backend_names_used))
            if backend_names_used
            else "AstGrepWrapperBackend",
            "routing_reason": "builtin-ruleset-scan"
            if ruleset
            else "inline-rules-scan"
            if inline_rules
            else "project-scan",
            "sidecar_used": False,
            "total_matches": total_matches,
            "matched_rules": matched_rules,
            "rule_count": len(rules),
            "backends": sorted(backend_names_used),
            "findings": findings,
        }
        if ruleset:
            payload["ruleset"] = ruleset

        _safe_stdout_line(json.dumps(payload))
        return 0

    print(
        "Scan completed. "
        f"rules={len(rules)} matched_rules={matched_rules} total_matches={total_matches} "
        f"backends={','.join(sorted(backend_names_used)) or 'none'}"
    )
    return 0


def test_command(config: str | None = "sgconfig.yml") -> int:
    from dataclasses import replace

    from tensor_grep.core.config import SearchConfig

    try:
        project_cfg, rules, _, test_data, _hints = _load_ast_project_data(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not rules:
        print("Error: No valid rules found in configured rule directories.", file=sys.stderr)
        return 1
    rules_by_id = {rule["id"]: rule for rule in rules}

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    backend_names_used: set[str] = set()
    rule_case_groups: dict[tuple[int, str, str], dict[str, Any]] = {}
    backend_cache: dict[tuple[str | None, str, bool, bool], Any] = {}

    total_cases = 0
    failures: list[str] = []
    snippet_cache: dict[tuple[str, str], str] = {}

    with TemporaryDirectory(
        prefix=".tg_test_session_", dir=project_cfg["root_dir"]
    ) as session_temp:
        # Resolve once for the session
        session_temp_path = Path(session_temp).resolve()

        for test_file_entry in test_data:
            test_file = test_file_entry["file"]
            cases = test_file_entry["cases"]

            for case in cases:
                case_id = str(case.get("id") or test_file_entry["stem"])
                linked_rule = case.get("ruleId")
                pattern = _extract_rule_pattern(case)
                try:
                    language = normalize_ast_language(case.get("language") or cfg.lang or "python")
                    if not pattern and isinstance(linked_rule, str) and linked_rule in rules_by_id:
                        pattern = rules_by_id[linked_rule]["pattern"]
                        language = normalize_ast_language(
                            case.get("language") or rules_by_id[linked_rule]["language"]
                        )
                except ValueError as exc:
                    failures.append(f"{test_file}:{case_id}: {exc}")
                    continue
                if not pattern:
                    failures.append(f"{test_file}:{case_id}: missing pattern or ruleId")
                    continue

                valid_snippets = _normalize_string_list(case.get("valid"), [])
                invalid_snippets = _normalize_string_list(case.get("invalid"), [])
                if not valid_snippets and not invalid_snippets:
                    failures.append(f"{test_file}:{case_id}: empty valid/invalid test lists")
                    continue

                total_cases += len(valid_snippets) + len(invalid_snippets)
                case_cfg = cfg if language == cfg.lang else replace(cfg, lang=language)

                # Use orchestration hint if available
                backend = _select_ast_backend_for_pattern(
                    case_cfg, pattern, backend_cache=backend_cache
                )

                backend_names_used.add(type(backend).__name__)

                # Unified batching for all backends that support search_many
                if hasattr(backend, "search_many"):
                    batch_key = (id(backend), pattern, language)
                    batch = rule_case_groups.setdefault(
                        batch_key,
                        {
                            "backend": backend,
                            "case_cfg": case_cfg,
                            "pattern": pattern,
                            "language": language,
                            "items": [],
                        },
                    )
                    items = cast("list[tuple[str, str, bool]]", batch["items"])
                    case_key = f"{test_file}:{case_id}"
                    items.extend((case_key, snippet, False) for snippet in valid_snippets)
                    items.extend((case_key, snippet, True) for snippet in invalid_snippets)
                else:
                    # Fallback for backends without search_many
                    try:
                        suffix = _suffix_for_language(language)
                        for expected_match, snippets in (
                            (False, valid_snippets),
                            (True, invalid_snippets),
                        ):
                            for snippet in snippets:
                                cache_key = (snippet, language)
                                if cache_key in snippet_cache:
                                    temp_name_str = snippet_cache[cache_key]
                                else:
                                    temp_name = session_temp_path / f"snip_{uuid4().hex}{suffix}"
                                    temp_name.write_text(snippet, encoding="utf-8")
                                    temp_name_str = str(temp_name)
                                    snippet_cache[cache_key] = temp_name_str

                                result = backend.search(temp_name_str, pattern, config=case_cfg)
                                has_match = bool(
                                    result.total_files > 0
                                    or result.total_matches > 0
                                    or result.matched_file_paths
                                )
                                if has_match != expected_match:
                                    failures.append(
                                        f"{test_file}:{case_id}: expected {'match' if expected_match else 'no match'}, got {'match' if has_match else 'no match'} for snippet {snippet!r}"
                                    )
                    except Exception as exc:
                        failures.append(f"{test_file}:{case_id}: backend error: {exc}")

        # Execute all batched tests
        if rule_case_groups:
            for batch in rule_case_groups.values():
                items = cast("list[tuple[str, str, bool]]", batch["items"])
                try:
                    match_results = _batch_search_snippets(
                        batch["backend"],
                        temp_dir_path=session_temp_path,
                        case_cfg=cast("SearchConfig", batch["case_cfg"]),
                        pattern=cast(str, batch["pattern"]),
                        language=cast(str, batch["language"]),
                        snippets=[snippet for _, snippet, _ in items],
                        snippet_cache=snippet_cache,
                    )

                    for (case_key, snippet, expected_match), has_match in zip(
                        items, match_results, strict=True
                    ):
                        if has_match != expected_match:
                            failures.append(
                                f"{case_key}: expected {'match' if expected_match else 'no match'}, got "
                                f"{'match' if has_match else 'no match'} for snippet {snippet!r}"
                            )
                except Exception as exc:
                    for case_key, _, _ in items:
                        failures.append(f"{case_key}: backend error: {exc}")

    print(
        f"Testing AST rules using {_describe_ast_backend_modes(backend_names_used)} "
        f"from {project_cfg['config_path']}..."
    )
    if failures:
        for failure in failures:
            print(f"[test] FAIL {failure}", file=sys.stderr)
        print(f"Rule tests failed. cases={total_cases} failures={len(failures)}", file=sys.stderr)
        return 1

    print(f"All tests passed. cases={total_cases}")
    return 0


def main_entry(argv: list[str] | None = None) -> None:
    # Manual fast path for scan and test to avoid argparse overhead
    if argv and len(argv) >= 1:
        # Check for --help or -h anywhere in the arguments
        if "--help" in argv or "-h" in argv:
            # Fall through to argparse for help display
            pass
        elif argv[0] == "scan" and (
            len(argv) == 1 or (len(argv) == 3 and argv[1] in ("--config", "-c"))
        ):
            config = "sgconfig.yml"
            if len(argv) >= 3:
                config = argv[2]
            raise SystemExit(scan_command(config=config))
        elif argv[0] == "test" and (
            len(argv) == 1 or (len(argv) == 3 and argv[1] in ("--config", "-c"))
        ):
            config = "sgconfig.yml"
            if len(argv) >= 3:
                config = argv[2]
            raise SystemExit(test_command(config=config))

    import argparse

    parser = argparse.ArgumentParser(add_help=True, prog="tg")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("positionals", nargs="*")
    run_parser.add_argument("--pattern", "-p", dest="pattern_option", default=None)
    run_parser.add_argument("--rewrite", "-r", default=None)
    run_parser.add_argument("--lang", "-l", default=None)
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--verify", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--checkpoint", action="store_true")
    run_parser.add_argument("--audit-manifest", default=None)
    run_parser.add_argument("--audit-signing-key", default=None)
    run_parser.add_argument("--lint-cmd", default=None)
    run_parser.add_argument("--test-cmd", default=None)
    run_parser.add_argument("--policy", default=None)
    run_parser.add_argument("--files-with-matches", action="store_true")
    run_parser.add_argument("--selector", default=None)
    run_parser.add_argument("--strictness", default=None)
    run_parser.add_argument("--stdin", action="store_true")
    run_parser.add_argument("--globs", action="append", default=None)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("path", nargs="?", default=".")
    scan_parser.add_argument("--config", "-c", default="sgconfig.yml")
    scan_parser.add_argument("--ruleset", default=None)
    scan_parser.add_argument("--inline-rules", default=None)
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.add_argument("--include-evidence-snippets", action="store_true")
    scan_parser.add_argument("--max-evidence-snippets-per-file", type=int, default=1)
    scan_parser.add_argument("--max-evidence-snippet-chars", type=int, default=120)
    scan_parser.add_argument("--glob", "-g", action="append", default=None)
    scan_parser.add_argument("--type", "-t", dest="file_type", action="append", default=None)
    scan_parser.add_argument("--max-depth", type=int, default=None)
    scan_parser.add_argument("--allow-broad-generated-scan", action="store_true")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--config", "-c", default="sgconfig.yml")

    new_parser = subparsers.add_parser("new")
    new_parser.add_argument("--config", "-c", default="sgconfig.yml")

    args = parser.parse_args(argv)
    if args.command == "run":
        positionals = list(args.positionals or [])
        if args.pattern_option:
            if len(positionals) > 1:
                parser.error("run --pattern accepts at most one positional PATH argument")
            pattern = args.pattern_option
            path = positionals[0] if positionals else None
        else:
            if len(positionals) > 2:
                parser.error("run accepts at most PATTERN and PATH positionals")
            pattern = positionals[0] if positionals else None
            path = positionals[1] if len(positionals) > 1 else None
        if not pattern:
            parser.error("run requires --pattern <PATTERN> or positional PATTERN")
        raise SystemExit(
            run_command(
                pattern,
                path,
                rewrite=args.rewrite,
                lang=args.lang,
                apply=args.apply,
                verify=args.verify,
                json_mode=args.json,
                checkpoint=args.checkpoint,
                audit_manifest=args.audit_manifest,
                audit_signing_key=args.audit_signing_key,
                lint_cmd=args.lint_cmd,
                test_cmd=args.test_cmd,
                policy=args.policy,
                files_with_matches=args.files_with_matches,
                selector=args.selector,
                strictness=args.strictness,
                stdin=args.stdin,
                globs=args.globs,
            )
        )
    if args.command == "scan":
        raise SystemExit(
            scan_command(
                config=args.config,
                ruleset=getattr(args, "ruleset", None),
                inline_rules=getattr(args, "inline_rules", None),
                path=getattr(args, "path", None),
                json_mode=getattr(args, "json", False),
                include_evidence=getattr(args, "include_evidence_snippets", False),
                max_evidence_snippets_per_file=getattr(args, "max_evidence_snippets_per_file", 1),
                max_evidence_snippet_chars=getattr(args, "max_evidence_snippet_chars", 120),
                glob=getattr(args, "glob", None),
                file_type=getattr(args, "file_type", None),
                max_depth=getattr(args, "max_depth", None),
                allow_broad_generated_scan=getattr(args, "allow_broad_generated_scan", False),
            )
        )
    if args.command == "test":
        raise SystemExit(test_command(config=args.config))
    if args.command == "new":
        # 'new' is handled by the full Typer CLI for now as it's not perf-critical
        from tensor_grep.cli.main import main_entry as full_main_entry

        full_main_entry()
    raise SystemExit(2)


def ast_info_command(*, json_output: bool = False) -> None:
    """List supported AST language identifiers."""
    import typer

    from tensor_grep.backends.ast_backend import get_supported_languages

    languages = get_supported_languages()
    if json_output:
        typer.echo(json.dumps({"languages": languages}))
        return

    typer.echo("Supported AST Languages:")
    for lang in languages:
        typer.echo(f"- {lang}")
