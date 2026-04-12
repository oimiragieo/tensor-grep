from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

# Global caches
_YAML_MODULE: Any = None
_YAML_LOADER: Any = None
_BACKEND_AVAILABILITY: dict[str, bool] = {}
_SUPPORTED_NATIVE_PATTERN_RE = None
_CACHED_BACKENDS: dict[str, Any] = {}
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


def _get_yaml() -> tuple[Any, Any]:
    global _YAML_MODULE, _YAML_LOADER
    if _YAML_MODULE is None:
        import yaml

        _YAML_MODULE = yaml
        _YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return _YAML_MODULE, _YAML_LOADER


def _load_yaml_dict(path: Path) -> dict[str, object]:
    yaml_mod, loader = _get_yaml()
    with path.open(encoding="utf-8") as handle:
        loaded = yaml_mod.load(handle, Loader=loader) or {}
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


def _load_ast_project_data(
    config_path: str | None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str], list[dict[str, Any]], dict[str, Any]]:
    """
    Load project config, rule specs, test files, and candidate files.
    Uses a unified JSON cache for maximum speed with robust invalidation.
    """
    resolved_config = Path(config_path or "sgconfig.yml").resolve()
    if not resolved_config.exists():
        raise FileNotFoundError(
            f"Config file {resolved_config} not found. Use `tg new` to create one."
        )

    root_dir = resolved_config.parent
    cache_dir = _get_cache_dir(root_dir)
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
    raw_cfg = _load_yaml_dict(resolved_config)
    project_cfg: dict[str, Any] = {
        "config_path": str(resolved_config),
        "root_dir": str(root_dir),
        "rule_dirs": _normalize_string_list(raw_cfg.get("ruleDirs"), ["rules"]),
        "test_dirs": _normalize_string_list(raw_cfg.get("testDirs"), ["tests"]),
        "language": str(raw_cfg.get("language") or "python"),
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
    candidate_files, _, tree_dirs = _collect_candidate_files(scanner, [str(root_dir)])

    # Collect mtimes for traversed directories
    tree_dirs_meta = {}
    for d in tree_dirs:
        try:
            tree_dirs_meta[d] = os.stat(d).st_mtime_ns
        except OSError:
            pass

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
        backend_name = _select_ast_backend_name_for_pattern(
            rule["pattern"], project_cfg["language"]
        )
        backend_hints[rule["id"]] = backend_name

    return {
        "backend_hints": backend_hints,
    }


def _select_ast_backend_name_for_pattern(pattern: str, language: str) -> str:
    """
    Lightweight backend selection logic that doesn't instantiate anything.
    """
    global _SUPPORTED_NATIVE_PATTERN_RE
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
    # Default policy: prefer native if pattern matches simple shape, otherwise wrapper.
    return "AstBackend" if supports_native_pattern else "AstGrepWrapperBackend"


def _load_rule_specs_and_meta(
    project_cfg: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, int]]:
    _get_yaml()

    root_dir = Path(project_cfg["root_dir"])
    rule_dirs = cast("list[str]", project_cfg["rule_dirs"])
    default_language = cast("str", project_cfg["language"])

    specs: list[dict[str, str]] = []
    meta: dict[str, int] = {}
    for rule_file in _iter_yaml_files(root_dir, rule_dirs):
        meta[str(rule_file)] = os.stat(rule_file).st_mtime_ns
        payload = _load_yaml_dict(rule_file)

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for idx, item in enumerate(raw_rules):
                if not isinstance(item, dict):
                    continue
                pattern = _extract_rule_pattern(item)
                if not pattern:
                    continue
                specs.append({
                    "id": str(item.get("id") or f"{rule_file.stem}-{idx + 1}"),
                    "pattern": pattern,
                    "language": str(
                        item.get("language") or payload.get("language") or default_language
                    ),
                })
            continue

        pattern = _extract_rule_pattern(payload)
        if not pattern:
            continue
        specs.append({
            "id": str(payload.get("id") or rule_file.stem),
            "pattern": pattern,
            "language": str(payload.get("language") or default_language),
        })

    return specs, meta


def _load_test_data_and_meta(
    project_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _get_yaml()

    root_dir = Path(project_cfg["root_dir"])
    test_dirs = cast("list[str]", project_cfg["test_dirs"])

    test_data = []
    meta: dict[str, int] = {}
    for test_file in _iter_yaml_files(root_dir, test_dirs):
        meta[str(test_file)] = os.stat(test_file).st_mtime_ns
        payload = _load_yaml_dict(test_file)
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


def _suffix_for_language(language: str) -> str:
    return _SUFFIX_CACHE.get(language.lower(), ".py")


def _collect_candidate_files(
    scanner: DirectoryScanner, paths: list[str]
) -> tuple[list[str], set[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen, set()


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
    if not snippets:
        return []

    suffix = _suffix_for_language(language)
    snippet_paths: list[str] = []

    # Use a counter for faster filename generation than full UUIDs
    counter = len(snippet_cache)

    for snippet in snippets:
        cache_key = (snippet, language)
        if cache_key in snippet_cache:
            snippet_paths.append(snippet_cache[cache_key])
            continue

        counter += 1
        # Write unique snippet
        snippet_path = temp_dir_path / f"snip_{counter}{suffix}"
        snippet_path.write_text(snippet, encoding="utf-8")
        path_str = str(snippet_path)
        snippet_cache[cache_key] = path_str
        snippet_paths.append(path_str)

    # Use explicit paths to avoid DirectoryScanner overhead inside the backend
    result = cast(Any, backend).search_many(snippet_paths, pattern, config=case_cfg)

    # Resolve matches against the written paths. We use fast string normalization.
    matched_paths = {_fast_norm(p) for p in result.matched_file_paths}
    matched_paths.update(_fast_norm(match.file) for match in result.matches if match.file)

    return [_fast_norm(p) in matched_paths for p in snippet_paths]


def _describe_ast_backend_mode(backend_name: str) -> str:
    if backend_name == "AstBackend":
        return "GPU-Accelerated GNNs"
    if backend_name == "AstGrepWrapperBackend":
        return "ast-grep structural matching"
    return backend_name


def _describe_ast_backend_modes(backend_names: set[str]) -> str:
    if not backend_names:
        return "adaptive AST routing"
    if len(backend_names) == 1:
        return _describe_ast_backend_mode(next(iter(backend_names)))
    return "adaptive AST routing"


def run_command(
    pattern: str,
    path: str | None = None,
    *,
    rewrite: str | None = None,
    lang: str | None = None,
    config: str | None = "sgconfig.yml",
    apply: bool = False,
    verify: bool = False,
    json_output: bool = False,
    checkpoint: bool = False,
    audit_manifest: str | None = None,
    audit_signing_key: str | None = None,
    lint_cmd: str | None = None,
    test_cmd: str | None = None,
    policy: str | None = None,
) -> int:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult

    if policy is not None and not apply:
        print("--policy requires --apply.", file=sys.stderr)
        return 1
    if (
        verify or checkpoint or audit_manifest or audit_signing_key or lint_cmd or test_cmd
    ) and not apply:
        print(
            "--verify, --checkpoint, --audit-manifest, --audit-signing-key, --lint-cmd, and "
            "--test-cmd require --apply.",
            file=sys.stderr,
        )
        return 1
    if apply:
        if rewrite is None:
            print("--apply requires --rewrite.", file=sys.stderr)
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
        )
        print(rewrite_json)
        return exit_code

    del rewrite, config, json_output, audit_manifest, audit_signing_key, lint_cmd, test_cmd, policy

    search_path = path or "."
    cfg = SearchConfig(ast=True, ast_prefer_native=False, lang=lang, query_pattern=pattern)
    backend = _select_ast_backend_for_pattern(cfg, pattern)
    backend_name = type(backend).__name__
    print(f"Executing {_describe_ast_backend_mode(backend_name)} run...")

    if backend_name not in {"AstBackend", "AstGrepWrapperBackend"}:
        print(
            "Warning: AstBackend not available (requires torch_geometric/tree_sitter). "
            "Falling back to CPU regex.",
            file=sys.stderr,
        )

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    if backend_name == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
        result = cast(Any, backend).search_many([search_path], pattern, config=cfg)
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

    from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

    print(RipgrepFormatter().format(all_results))
    return 0


def _get_cached_backend(name: str) -> Any:
    if name not in _CACHED_BACKENDS:
        if name == "AstBackend":
            from tensor_grep.backends.ast_backend import AstBackend

            _CACHED_BACKENDS[name] = AstBackend()
        elif name == "AstGrepWrapperBackend":
            from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

            _CACHED_BACKENDS[name] = AstGrepWrapperBackend()
    return _CACHED_BACKENDS[name]


def _check_backend_available(name: str) -> bool:
    if name not in _BACKEND_AVAILABILITY:
        _BACKEND_AVAILABILITY[name] = _get_cached_backend(name).is_available()
    return _BACKEND_AVAILABILITY[name]


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
        if _check_backend_available("AstBackend"):
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


def scan_command(config: str | None = "sgconfig.yml") -> int:
    from dataclasses import replace

    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult

    try:
        project_cfg, rules, candidate_files, _, hints = _load_ast_project_data(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not rules:
        print("Error: No valid rules found in configured rule directories.", file=sys.stderr)
        return 1

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    root_dir = cast("Path", project_cfg["root_dir"])
    backend_names_used: set[str] = set()
    backend_hints = hints.get("backend_hints", {})

    print(f"Scanning project using adaptive AST routing based on {project_cfg['config_path']}...")

    # Group rules by backend to maximize search_project usage
    wrapper_rules: list[dict[str, str]] = []
    other_resolved: list[tuple[dict[str, str], SearchConfig, Any]] = []

    for rule in rules:
        rule_cfg = cfg if rule["language"] == cfg.lang else replace(cfg, lang=rule["language"])
        backend_name = backend_hints.get(rule["id"])
        if backend_name and _check_backend_available(backend_name):
            backend = _get_cached_backend(backend_name)
        else:
            backend = _select_ast_backend_for_pattern(rule_cfg, rule["pattern"])

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
                str(root_dir), str(project_cfg["config_path"])
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

            print(
                f"[scan] rule={rule['id']} lang={rule['language']} "
                f"matches={rule_matches} files={matched_count}"
            )

    # Process other results (native or individual wrapper)
    scanner = None
    for rule, rule_cfg, backend in other_resolved:
        backend_names_used.add(type(backend).__name__)
        matched_files: set[str] = set()

        if type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
            result = cast(Any, backend).search_many(
                [str(root_dir)], rule["pattern"], config=rule_cfg
            )
            rule_matches = result.total_matches
            matched_files.update(result.matched_file_paths)
            if not matched_files and result.total_files > 0:
                matched_files.update(match.file for match in result.matches if match.file)
        else:
            if scanner is None:
                from tensor_grep.io.directory_scanner import DirectoryScanner

                scanner = DirectoryScanner(cfg)

            rule_matches = 0
            for current_file in candidate_files:
                result = backend.search(current_file, rule["pattern"], config=rule_cfg)
                rule_matches += result.total_matches
                if result.total_files > 0 or result.total_matches > 0:
                    matched_files.add(current_file)

        total_matches += rule_matches
        if rule_matches > 0:
            matched_rules += 1
        print(
            f"[scan] rule={rule['id']} lang={rule['language']} "
            f"matches={rule_matches} files={len(matched_files)}"
        )

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
        project_cfg, rules, _, test_data, hints = _load_ast_project_data(config)
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
    backend_hints = hints.get("backend_hints", {})

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
                language = str(case.get("language") or cfg.lang or "python")
                if not pattern and isinstance(linked_rule, str) and linked_rule in rules_by_id:
                    pattern = rules_by_id[linked_rule]["pattern"]
                    language = str(case.get("language") or rules_by_id[linked_rule]["language"])
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
                backend_name = backend_hints.get(linked_rule) if linked_rule else None
                if not backend_name:
                    backend_name = _select_ast_backend_name_for_pattern(pattern, language)

                if _check_backend_available(backend_name):
                    backend = _get_cached_backend(backend_name)
                else:
                    backend = _select_ast_backend_for_pattern(case_cfg, pattern)

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
                                    temp_name = (
                                        session_temp_path / f"snip_{len(snippet_cache) + 1}{suffix}"
                                    )
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
        elif argv[0] == "scan":
            config = "sgconfig.yml"
            if len(argv) >= 3 and argv[1] in ("--config", "-c"):
                config = argv[2]
            raise SystemExit(scan_command(config))
        elif argv[0] == "test":
            config = "sgconfig.yml"
            if len(argv) >= 3 and argv[1] in ("--config", "-c"):
                config = argv[2]
            raise SystemExit(test_command(config))

    import argparse

    parser = argparse.ArgumentParser(add_help=True, prog="tg")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("pattern")
    run_parser.add_argument("path", nargs="?")
    run_parser.add_argument("--rewrite", "-r", default=None)
    run_parser.add_argument("--lang", "-l", default=None)
    run_parser.add_argument("--config", "-c", default="sgconfig.yml")
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--verify", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--checkpoint", action="store_true")
    run_parser.add_argument("--audit-manifest", default=None)
    run_parser.add_argument("--audit-signing-key", default=None)
    run_parser.add_argument("--lint-cmd", default=None)
    run_parser.add_argument("--test-cmd", default=None)
    run_parser.add_argument("--policy", default=None)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--config", "-c", default="sgconfig.yml")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--config", "-c", default="sgconfig.yml")

    new_parser = subparsers.add_parser("new")
    new_parser.add_argument("--config", "-c", default="sgconfig.yml")

    args = parser.parse_args(argv)
    if args.command == "run":
        raise SystemExit(
            run_command(
                args.pattern,
                args.path,
                rewrite=args.rewrite,
                lang=args.lang,
                config=args.config,
                apply=args.apply,
                verify=args.verify,
                json_output=args.json,
                checkpoint=args.checkpoint,
                audit_manifest=args.audit_manifest,
                audit_signing_key=args.audit_signing_key,
                lint_cmd=args.lint_cmd,
                test_cmd=args.test_cmd,
                policy=args.policy,
            )
        )
    if args.command == "scan":
        raise SystemExit(scan_command(args.config))
    if args.command == "test":
        raise SystemExit(test_command(args.config))
    if args.command == "new":
        # 'new' is handled by the full Typer CLI for now as it's not perf-critical
        from tensor_grep.cli.main import main_entry as full_main_entry

        full_main_entry()
    raise SystemExit(2)
