"""AST-workflow rule/project-data loading helpers, split out of `ast_workflows.py`
(enterprise file-size campaign, Wave 1) to bring that module under the 1500-line
core limit.

None of the functions here are directly monkeypatched by tests (verified via
`scripts/monkeypatch_binding_audit.py --module cli.ast_workflows`), so they are
re-exported by value from `ast_workflows.py` (`from .ast_workflow_rules import
...`). What they DO consume from the facade (`_load_yaml_dict`,
`_normalize_string_list`, `_get_cache_dir`, `_PROJECT_DATA_CACHE_SCHEMA_VERSION`,
`_SUFFIX_CACHE`, `_fast_norm`, `_get_yaml`) stays defined in `ast_workflows.py`
because two of those globals (`_YAML_MODULE`/`_YAML_LOADER`) ARE monkeypatched
directly by tests as `ast_workflows._YAML_MODULE` / `ast_workflows._YAML_LOADER`.
This module therefore imports the FACADE MODULE (not `from ast_workflows import
X`) and reaches those symbols via late attribute lookup
(`ast_workflows._load_yaml_dict(...)`) so a test patch on the facade is always
visible here, and so the two-module import cycle (facade imports this module for
its 16 re-exports; this module needs the facade's yaml-cache helpers) resolves
without a partial-initialization ImportError.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from tensor_grep.backends.ast_backend import is_native_ast_language, normalize_ast_language
from tensor_grep.cli._index_lock import atomic_write_bytes

# NOTE: `tensor_grep.cli.ast_workflows` (the facade) is deliberately NOT imported
# at module level here. The facade imports this module for its re-exports, so a
# top-level `from tensor_grep.cli import ast_workflows` here creates a true
# import cycle: whichever module a caller imports FIRST ends up trying to read
# attributes off the OTHER one before it has finished executing (a
# partial-initialization ImportError). Each function below that needs a facade
# symbol imports `ast_workflows` locally on its own first line instead -- the
# import is cached after the first call, so this costs nothing at runtime, and
# it defers the attribute lookup until both modules are fully loaded.

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine
    from tensor_grep.io.directory_scanner import DirectoryScanner


def _load_ast_project_data(
    config_path: str | None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str], list[dict[str, Any]], dict[str, Any]]:
    """
    Load project config, rule specs, test files, and candidate files.
    Uses a unified JSON cache for maximum speed with robust invalidation.
    """
    from tensor_grep.cli import ast_workflows

    resolved_config = Path(config_path or "sgconfig.yml").resolve()
    if not resolved_config.exists():
        raise FileNotFoundError(
            f"Config file {resolved_config} not found. Use `tg new` to create one."
        )

    root_dir = resolved_config.parent
    cache_dir = ast_workflows._get_cache_dir(root_dir)
    cache_file = cache_dir / "project_data_v6.json"

    # Check unified cache
    if cache_file.exists():
        try:
            cache_mtime_ns = os.stat(cache_file).st_mtime_ns

            # Validation 1: Config file itself
            if cache_mtime_ns >= os.stat(resolved_config).st_mtime_ns:
                cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                validation = cached_data.get("validation_metadata", {})
                still_valid = True

                # Validation 1a (M16/F3): reject legacy-schema caches. Their
                # rule_specs lack composite members and severity/message even
                # when mtime-fresh; treat as a cache miss and rebuild from
                # source (same gate as the Rust writer, which owns this file).
                if (
                    cached_data.get("cache_schema_version")
                    != ast_workflows._PROJECT_DATA_CACHE_SCHEMA_VERSION
                ):
                    still_valid = False

                # Validation 2: Rule files
                rule_files = validation.get("rule_files", {})
                for rf_str, rf_mtime in rule_files.items():
                    try:
                        if os.stat(rf_str).st_mtime_ns > int(rf_mtime):
                            still_valid = False
                            break
                    except (OSError, ValueError, TypeError):
                        still_valid = False
                        break

                if still_valid:
                    # Validation 3: Test files
                    test_files = validation.get("test_files", {})
                    for tf_str, tf_mtime in test_files.items():
                        try:
                            if os.stat(tf_str).st_mtime_ns > int(tf_mtime):
                                still_valid = False
                                break
                        except (OSError, ValueError, TypeError):
                            still_valid = False
                            break

                if still_valid:
                    # Validation 4: Traversed directory mtimes (tree-wide)
                    tree_dirs = validation.get("tree_dirs", {})
                    for td_str, td_mtime in tree_dirs.items():
                        try:
                            if os.stat(td_str).st_mtime_ns > int(td_mtime):
                                still_valid = False
                                break
                        except (OSError, ValueError, TypeError):
                            still_valid = False
                            break

                if still_valid:
                    # Validation 5: Rule/Test directory mtimes (explicit config)
                    for rd in cached_data["project_cfg"].get("rule_dirs", []):
                        rd_path = os.path.join(str(root_dir), rd)
                        try:
                            if os.stat(rd_path).st_mtime_ns > cache_mtime_ns:
                                still_valid = False
                                break
                        except OSError:
                            pass
                    if still_valid:
                        for td in cached_data["project_cfg"].get("test_dirs", []):
                            td_path = os.path.join(str(root_dir), td)
                            try:
                                if os.stat(td_path).st_mtime_ns > cache_mtime_ns:
                                    still_valid = False
                                    break
                            except OSError:
                                pass

                if still_valid:
                    # Convert paths back to Path objects where needed
                    cached_data["project_cfg"]["config_path"] = Path(
                        cached_data["project_cfg"]["config_path"]
                    )
                    cached_data["project_cfg"]["root_dir"] = Path(
                        cached_data["project_cfg"]["root_dir"]
                    )
                    return (
                        cached_data["project_cfg"],
                        cached_data["rule_specs"],
                        cached_data.get("candidate_files", []),
                        cached_data.get("test_data", []),
                        cached_data.get("orchestration_hints", {}),
                    )
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    # Cache miss: load project config
    raw_cfg = ast_workflows._load_yaml_dict(resolved_config)
    project_cfg: dict[str, Any] = {
        "config_path": str(resolved_config),
        "root_dir": str(root_dir),
        "rule_dirs": ast_workflows._normalize_string_list(raw_cfg.get("ruleDirs"), ["rules"]),
        "test_dirs": ast_workflows._normalize_string_list(raw_cfg.get("testDirs"), ["tests"]),
        "language": normalize_ast_language(raw_cfg.get("language") or "python"),
    }

    # Load rule specs and track files
    rule_specs, _rule_files_meta = _load_rule_specs_and_meta(project_cfg)

    # Load test data and track files
    test_data, _test_files_meta = _load_test_data_and_meta(project_cfg)

    # File discovery (for scan)
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    scanner = DirectoryScanner(cfg)
    candidate_files, _, _ = _collect_candidate_files(scanner, [str(root_dir)])

    # Precompute orchestration hints
    orchestration_hints = _precompute_orchestration_hints(project_cfg, rule_specs, test_data)

    # Note: Python no longer saves to the unified cache.
    # Rust is the canonical owner and authoritative writer of project_data_v6.json.
    # Python remains a compatibility reader for sidecar/editor-plane tasks.

    # Ensure internal use gets Path objects
    project_cfg["config_path"] = resolved_config
    project_cfg["root_dir"] = root_dir
    return project_cfg, rule_specs, candidate_files, test_data, orchestration_hints


def _precompute_orchestration_hints(
    project_cfg: dict[str, Any], rule_specs: list[dict[str, str]], test_data: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    Precompute backend selection and wrapper batching hints to avoid repeated work on cache hits.
    """
    backend_hints = {}

    for rule in rule_specs:
        # M16 F3: composite-aware — a rule whose members need the ast-grep
        # wrapper must never be hinted to a backend that serves only the first
        # member (e.g. bare `alpha` -> native, which cannot serve `alpha(1)`).
        backend_name = _select_ast_backend_name_for_rule(rule, project_cfg["language"])
        backend_hints[rule["id"]] = backend_name

    return {
        "backend_hints": backend_hints,
    }


def _pattern_is_native_shaped(pattern: str) -> bool:
    """Shape-only native-query check (M16 F3): a bare identifier or a
    parenthesised s-expression is native-shaped; anything else (ast-grep DSL,
    metavariables, calls like `alpha(1)`) needs the ast-grep wrapper.
    Language/availability are deliberately NOT consulted here."""
    from tensor_grep.cli import ast_workflows

    stripped = pattern.strip()
    if not stripped:
        return False
    if stripped.startswith("("):
        return True
    if ast_workflows._SUPPORTED_NATIVE_PATTERN_RE is None:
        ast_workflows._SUPPORTED_NATIVE_PATTERN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    return bool(ast_workflows._SUPPORTED_NATIVE_PATTERN_RE.fullmatch(stripped))


def _rule_needs_ast_grep_wrapper(rule: Mapping[str, object]) -> bool:
    """M16 F3: True when a rule has MULTIPLE member patterns and ANY member is
    not native-shaped. A composite rule must never be routed through a backend
    that only serves its first member (e.g. a bare `alpha` first member routed
    to tree-sitter native, which cannot serve a DSL member like `alpha(1)`).
    Single-pattern rules keep the legacy single-pattern routing."""
    members = _rule_member_patterns(rule)
    return len(members) > 1 and any(not _pattern_is_native_shaped(m) for m in members)


def _select_ast_backend_name_for_rule(rule: Mapping[str, object], language: str) -> str:
    """Composite-aware backend NAME (M16 F3): a composite with any non-native
    member always names the ast-grep wrapper; an all-native composite is served
    by the same backend as its first member; single-pattern rules keep the
    legacy `_select_ast_backend_name_for_pattern` decision."""
    if _rule_needs_ast_grep_wrapper(rule):
        return "AstGrepWrapperBackend"
    return _select_ast_backend_name_for_pattern(_rule_member_patterns(rule)[0], language)


def _select_ast_backend_name_for_pattern(pattern: str, language: str) -> str:
    """
    Lightweight backend selection logic that doesn't instantiate anything.
    """
    from tensor_grep.cli import ast_workflows

    if ast_workflows._SUPPORTED_NATIVE_PATTERN_RE is None:
        ast_workflows._SUPPORTED_NATIVE_PATTERN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    stripped_pattern = pattern.strip()
    supports_native_pattern = bool(
        stripped_pattern
        and (
            stripped_pattern.startswith("(")
            or ast_workflows._SUPPORTED_NATIVE_PATTERN_RE.fullmatch(stripped_pattern)
        )
    )
    return (
        "AstBackend"
        if supports_native_pattern and is_native_ast_language(language)
        else "AstGrepWrapperBackend"
    )


def _load_rule_specs_and_meta(
    project_cfg: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    from tensor_grep.cli import ast_workflows

    ast_workflows._get_yaml()

    root_dir = Path(project_cfg["root_dir"])
    rule_dirs = cast("list[str]", project_cfg["rule_dirs"])
    default_language = cast("str", project_cfg["language"])

    specs: list[dict[str, str]] = []
    meta: dict[str, int] = {}
    for rule_file in _iter_yaml_files(root_dir, rule_dirs):
        meta[str(rule_file)] = os.stat(rule_file).st_mtime_ns
        payload = ast_workflows._load_yaml_dict(rule_file)

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for idx, item in enumerate(raw_rules):
                if not isinstance(item, dict):
                    continue
                member_patterns = _extract_rule_member_patterns(item)
                if not member_patterns:
                    continue
                spec = {
                    "id": str(item.get("id") or f"{rule_file.stem}-{idx + 1}"),
                    "pattern": member_patterns[0],
                    "language": normalize_ast_language(
                        item.get("language") or payload.get("language") or default_language
                    ),
                    "severity": str(item.get("severity") or payload.get("severity") or "warning"),
                    "message": str(item.get("message") or payload.get("message") or ""),
                }
                if len(member_patterns) > 1:
                    # M16: composite (multi-pattern any-of) rules carry ALL
                    # members; `pattern` stays the FIRST member so existing
                    # single-pattern consumers keep working.
                    spec["patterns"] = member_patterns  # type: ignore[assignment]
                specs.append(spec)
            continue

        member_patterns = _extract_rule_member_patterns(payload)
        if not member_patterns:
            continue
        spec = {
            "id": str(payload.get("id") or rule_file.stem),
            "pattern": member_patterns[0],
            "language": normalize_ast_language(payload.get("language") or default_language),
            "severity": str(payload.get("severity") or "warning"),
            "message": str(payload.get("message") or ""),
        }
        if len(member_patterns) > 1:
            # M16: composite members (see the rules-sequence branch above).
            spec["patterns"] = member_patterns  # type: ignore[assignment]
        specs.append(spec)

    return specs, meta


def _load_test_data_and_meta(
    project_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    from tensor_grep.cli import ast_workflows

    ast_workflows._get_yaml()

    root_dir = Path(project_cfg["root_dir"])
    test_dirs = cast("list[str]", project_cfg["test_dirs"])

    test_data = []
    meta: dict[str, int] = {}
    for test_file in _iter_yaml_files(root_dir, test_dirs):
        meta[str(test_file)] = os.stat(test_file).st_mtime_ns
        payload = ast_workflows._load_yaml_dict(test_file)
        raw_cases = payload.get("tests")
        cases = (
            [case for case in raw_cases if isinstance(case, dict)]
            if isinstance(raw_cases, list)
            else [payload]
        )
        test_data.append({"file": str(test_file), "stem": test_file.stem, "cases": cases})
    return test_data, meta


def _iter_yaml_files(base_dir: Path, rel_dirs: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for rel_dir in rel_dirs:
        target = (base_dir / rel_dir).resolve()
        if target.is_file() and target.suffix.lower() in {".yml", ".yaml"}:
            candidates.append(target)
            continue
        if not target.is_dir():
            continue
        for ext in ("*.yml", "*.yaml"):
            candidates.extend(target.rglob(ext))
    return sorted(set(candidates))


def _extract_rule_pattern(rule_data: dict[str, object]) -> str | None:
    direct = rule_data.get("pattern")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    rule_node = rule_data.get("rule")
    if isinstance(rule_node, dict):
        nested = rule_node.get("pattern")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()

    return None


def _extract_rule_member_patterns(rule_data: dict[str, object]) -> list[str] | None:
    """Extract the member patterns of a rule item (M16; Rust twin:
    `AstWorkflowOrchestrator::extract_rule_member_patterns` in
    `rust_core/src/backend_ast_workflow.rs`).

    Supported ast-grep rule-YAML shapes:
    - a flat ``pattern:`` STRING,
    - a ``pattern:`` LIST of strings,
    - a ``rule:`` mapping whose ``pattern`` is a string,
    - a ``rule:`` mapping whose ``any:`` sequence lists sub-rules (each
      sub-rule's ``pattern`` string, or its nested ``rule.pattern`` string).

    A composite member that does not carry exactly one extractable pattern
    fails the WHOLE rule closed (None) rather than under-matching. ``all:`` /
    ``not:`` composite bodies require same-node intersection semantics the
    per-pattern matchers cannot express; they also return None and the rule is
    dropped, exactly as the Rust twin drops them.
    """
    direct = rule_data.get("pattern")
    if isinstance(direct, str):
        trimmed = direct.strip()
        if trimmed:
            return [trimmed]
    elif isinstance(direct, list):
        members: list[str] = []
        for item in direct:
            if not isinstance(item, str) or not item.strip():
                return None
            members.append(item.strip())
        if members:
            return members

    rule_node = rule_data.get("rule")
    if isinstance(rule_node, dict):
        nested = rule_node.get("pattern")
        if isinstance(nested, str) and nested.strip():
            return [nested.strip()]
        any_members = rule_node.get("any")
        if isinstance(any_members, list) and any_members:
            members = []
            for member in any_members:
                if not isinstance(member, dict):
                    return None
                member_pattern: object = member.get("pattern")
                if not isinstance(member_pattern, str) or not member_pattern.strip():
                    inner_rule = member.get("rule")
                    if isinstance(inner_rule, dict):
                        member_pattern = inner_rule.get("pattern")
                    else:
                        member_pattern = None
                if not isinstance(member_pattern, str) or not member_pattern.strip():
                    return None
                members.append(member_pattern.strip())
            if members:
                return members
    return None


def _rule_member_patterns(rule: Mapping[str, object]) -> list[str]:
    """The member patterns a rule scans with (M16): `patterns` (composite
    any-of members, set by `_load_rule_specs_and_meta` when a rule carries more
    than one) when present, else the single `pattern`. Mirrors
    `ast_rule_member_patterns` in the Rust scan core.
    """
    patterns = rule.get("patterns")
    if isinstance(patterns, list) and patterns:
        return [str(pattern) for pattern in patterns]
    return [str(rule["pattern"])]


def _match_node_identity(
    match: MatchLine, fallback_file: str | None = None
) -> tuple[str, int, int]:
    """Stable AST-node identity for composite-rule union dedupe (M16 F1): the
    node BYTE SPAN (file, start_byte, end_byte) when the backend supplied one,
    else a per-(file, line) fallback (-1 sentinel). Only the SAME node matched
    by multiple members may be deduplicated — two distinct nodes on one line
    are distinct matches, matching whole-config ast-grep's per-node `any`
    semantics. Rust twin: the `(start_byte, end_byte)` spans the scan core
    unions per file.
    """
    file_path = match.file or fallback_file or ""
    if match.start_byte is not None and match.end_byte is not None:
        return (file_path, match.start_byte, match.end_byte)
    return (file_path, -1, match.line_number)


def _suffix_for_language(language: str) -> str:
    from tensor_grep.cli import ast_workflows

    return ast_workflows._SUFFIX_CACHE.get(language.lower(), ".py")


def _collect_candidate_files(
    scanner: DirectoryScanner, paths: list[str]
) -> tuple[list[str], set[str], set[str]]:
    ordered = []
    seen = set()
    tree_dirs = set()
    for p in paths:
        base_path = Path(p).resolve()
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
            if base_path.is_file():
                continue
            current_path = Path(current_file).resolve()
            try:
                current_path.relative_to(base_path)
            except ValueError:
                continue
            for parent in current_path.parents:
                tree_dirs.add(str(parent))
                if parent == base_path:
                    break
    return ordered, seen, tree_dirs


def _batch_search_snippets(
    backend: object,
    *,
    temp_dir_path: Path,
    case_cfg: SearchConfig,
    pattern: str,
    language: str,
    snippets: list[str],
    snippet_cache: dict[tuple[str, str], str],
) -> list[bool]:
    """
    Batch search snippets by writing them to disk once and using search_many.
    Works for both native and wrapper backends.
    """
    from tensor_grep.cli import ast_workflows

    if not snippets:
        return []

    suffix = _suffix_for_language(language)
    snippet_paths: list[str] = []

    # Use unique snippet names with uuid for maximum collision robustness
    for snippet in snippets:
        cache_key = (snippet, language)
        if cache_key in snippet_cache:
            snippet_paths.append(snippet_cache[cache_key])
            continue

        # Write unique snippet with uuid for maximum collision robustness.
        # H2 (#859 class ratchet): `temp_dir_path` is a caller-supplied PARAMETER, not created
        # inside this function -- unlike the sanctioned self-contained-temp-artifact shape
        # elsewhere in this codebase, confinement cannot be statically proven from a parameter
        # alone, so this must route through the anchored helper rather than a raw write_text
        # (which follows a destination symlink).
        snippet_path = temp_dir_path / f"snip_{uuid4().hex}{suffix}"
        atomic_write_bytes(snippet_path, snippet.encode("utf-8"))
        path_str = str(snippet_path)
        snippet_cache[cache_key] = path_str
        snippet_paths.append(path_str)

    # Use explicit paths to avoid DirectoryScanner overhead inside the backend
    result = cast(Any, backend).search_many(snippet_paths, pattern, config=case_cfg)

    # Resolve matches against the written paths. We use fast string normalization.
    matched_paths = {ast_workflows._fast_norm(p) for p in result.matched_file_paths}
    matched_paths.update(
        ast_workflows._fast_norm(match.file) for match in result.matches if match.file
    )

    return [ast_workflows._fast_norm(p) in matched_paths for p in snippet_paths]


def _describe_ast_backend_mode(backend_name: str) -> str:
    if backend_name == "AstBackend":
        return "native AST matching"
    if backend_name == "AstGrepWrapperBackend":
        return "ast-grep structural matching"
    return backend_name


def _describe_ast_backend_modes(backend_names: set[str]) -> str:
    if not backend_names:
        return "adaptive AST routing"
    if len(backend_names) == 1:
        return _describe_ast_backend_mode(next(iter(backend_names)))
    return "adaptive AST routing"


def _inject_run_json_fields(json_str: str, mode: str) -> str:
    """Ensure every ``tg run --json`` payload carries a consistent envelope.

    Four distinct output shapes exist in ``run_command``:
      - ``"search"``       - plain AST search result (total_matches present)
      - ``"rewrite-plan"`` - preview of pending edits (total_matches absent in some paths)
      - ``"apply"``        - applied-edit result (total_matches absent in some paths)
      - ``"stdin"``        - search over piped stdin input

    This helper is called on *all four* paths to guarantee that
    ``version``, ``schema_version``, ``mode``, and ``total_matches``
    are **always** present as top-level keys so consumers can key on them
    without a KeyError.  Existing keys are **never** renamed or removed;
    only missing keys are backfilled.

    M3: batch-rewrite payloads (mode ``"rewrite-plan"`` / ``"apply"``) come from
    ``execute_rewrite_plan_json`` / ``execute_rewrite_apply_json`` in
    ``mcp_server.py`` and may lack ``mode`` and ``total_matches``.
    """
    try:
        payload = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return json_str
    if not isinstance(payload, dict):
        return json_str
    payload.setdefault("version", 1)
    payload.setdefault("schema_version", 1)
    payload.setdefault("mode", mode)
    payload.setdefault("total_matches", 0)
    return json.dumps(payload)


def _safe_stdout_line(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        payload = f"{text}\n".encode("utf-8", errors="replace")
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
            flush = getattr(buffer, "flush", None)
            if callable(flush):
                flush()
            return
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        escaped_text = f"{text}\n".encode(encoding, errors="backslashreplace").decode(
            encoding, errors="ignore"
        )
        sys.stdout.write(escaped_text)
        flush = getattr(sys.stdout, "flush", None)
        if callable(flush):
            flush()


def _warn_windows_single_quote_pattern(pattern: str) -> None:
    if os.name != "nt":
        return
    stripped = pattern.strip()
    if len(stripped) >= 2 and stripped.startswith("'") and stripped.endswith("'"):
        print(
            "No AST matches found. cmd.exe treats single quotes literally; use double quotes "
            "in cmd.exe or run this pattern from PowerShell/Git Bash where single quotes quote "
            "literal text.",
            file=sys.stderr,
        )


# CEO#6(b): static idiom catalog for zero-match `tg run` remediation. Deliberately NOT a
# "did-you-mean X" correction against the user's actual pattern -- a guessed correction could
# be actively wrong and mislead the caller (AGENTS.md's Backend Fail-Closed / honest-empty
# discipline); a static shape catalog can never be wrong, only unhelpful.
_AST_RUN_REMEDIATION_IDIOMS: tuple[str, ...] = (
    "def $NAME($$$ARGS): $$$BODY",
    "function $NAME($$$) { $$$ }",
)


def _ast_run_remediation_lines(pattern: str, lang: str | None) -> list[str]:
    """Static/heuristic remediation lines for a zero-match ``tg run`` (scope: ``tg run`` only)."""
    lines = [
        "No AST matches found. Common idiom shapes: " + " | ".join(_AST_RUN_REMEDIATION_IDIOMS),
        "Run `tg ast-info` to list supported LANGUAGES.",
    ]
    if "$" not in pattern:
        lines.append(
            "The pattern has no metavariable ($) -- add one like $NAME to match "
            "structurally varying code."
        )
    if not lang:
        lines.append(
            "No --lang was passed -- pass --lang <language> to parse against the right grammar."
        )
    return lines


def _emit_ast_run_remediation(
    pattern: str,
    lang: str | None,
    *,
    json_payload: dict[str, Any] | None = None,
) -> None:
    """Emit static/heuristic remediation for a zero-match ``tg run``.

    SCOPE: ``tg run`` ONLY -- callers must never call this from ``scan_command``, where a
    0-finding scan is a clean pass (exit 0), not a failure state the way a 0-match ``tg run``
    search is. Text output modes pass ``json_payload=None`` and get the hint on STDERR (stdout
    stays clean/parseable); ``--json`` mode passes the payload dict about to be serialized and
    gets an ADDITIVE ``"remediation"`` key -- every existing envelope key
    (version/schema_version/mode/total_matches/...) is left untouched.
    """
    lines = _ast_run_remediation_lines(pattern, lang)
    if json_payload is not None:
        json_payload["remediation"] = {"hints": lines}
        return
    print("\n".join(lines), file=sys.stderr)
