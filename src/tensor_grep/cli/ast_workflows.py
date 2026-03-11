from __future__ import annotations

import argparse
import re
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from tensor_grep.core.result import SearchResult
from tensor_grep.io.directory_scanner import DirectoryScanner

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig


def _load_yaml_dict(path: Path) -> dict[str, object]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
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


def _load_sg_project_config(config_path: str | None) -> dict[str, object]:
    resolved = Path(config_path or "sgconfig.yml").resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file {resolved} not found. Use `tg new` to create one.")

    raw = _load_yaml_dict(resolved)
    return {
        "config_path": resolved,
        "root_dir": resolved.parent,
        "rule_dirs": _normalize_string_list(raw.get("ruleDirs"), ["rules"]),
        "test_dirs": _normalize_string_list(raw.get("testDirs"), ["tests"]),
        "language": str(raw.get("language") or "python"),
    }


def _iter_yaml_files(base_dir: Path, rel_dirs: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for rel_dir in rel_dirs:
        target = (base_dir / rel_dir).resolve()
        if target.is_file() and target.suffix.lower() in {".yml", ".yaml"}:
            candidates.append(target)
            continue
        if not target.is_dir():
            continue
        candidates.extend(sorted(target.rglob("*.yml")))
        candidates.extend(sorted(target.rglob("*.yaml")))
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


def _load_rule_specs(project_cfg: dict[str, object]) -> list[dict[str, str]]:
    root_dir = cast(Path, project_cfg["root_dir"])
    rule_dirs = cast(list[str], project_cfg["rule_dirs"])
    default_language = cast(str, project_cfg["language"])

    specs: list[dict[str, str]] = []
    for rule_file in _iter_yaml_files(root_dir, rule_dirs):
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

    return specs


def _suffix_for_language(language: str) -> str:
    normalized = language.lower()
    if normalized in {"js", "javascript"}:
        return ".js"
    if normalized in {"ts", "typescript"}:
        return ".ts"
    return ".py"


def _collect_candidate_files(
    scanner: DirectoryScanner, paths: list[str]
) -> tuple[list[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen


def _search_ast_test_snippets_with_wrapper(
    backend: object,
    *,
    root_dir: Path,
    case_cfg: SearchConfig,
    pattern: str,
    language: str,
    snippets: list[str],
) -> list[bool]:
    if not snippets:
        return []

    suffix = _suffix_for_language(language)
    with TemporaryDirectory(prefix=".tg_rule_test_batch_", dir=root_dir) as temp_dir:
        temp_root = Path(temp_dir)
        snippet_paths: list[Path] = []
        for index, snippet in enumerate(snippets):
            snippet_path = temp_root / f"case_{index}{suffix}"
            snippet_path.write_text(snippet, encoding="utf-8")
            snippet_paths.append(snippet_path)

        result = cast(Any, backend).search_many([str(temp_root)], pattern, config=case_cfg)

        def _resolve_match_path(raw_path: str) -> Path:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                return candidate.resolve()
            return (temp_root / candidate).resolve()

        matched_paths = {_resolve_match_path(path) for path in result.matched_file_paths}
        matched_paths.update(
            _resolve_match_path(match.file) for match in result.matches if match.file
        )
        return [snippet_path.resolve() in matched_paths for snippet_path in snippet_paths]


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
) -> int:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult

    del rewrite, config  # reserved for parity; rewrite stays unimplemented in fast path

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
        scanner = DirectoryScanner(cfg)
        candidate_files, _ = _collect_candidate_files(scanner, [search_path])
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


def _select_ast_backend_for_pattern(
    base_config: SearchConfig,
    pattern: str,
    backend_cache: dict[tuple[str | None, str, bool], ComputeBackend] | None = None,
) -> ComputeBackend:
    from tensor_grep.backends.ast_backend import AstBackend
    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.core.pipeline import Pipeline

    stripped_pattern = pattern.strip()
    supports_native_pattern = bool(
        stripped_pattern
        and (
            stripped_pattern.startswith("(")
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped_pattern)
        )
    )
    pattern_kind = (
        "native" if base_config.ast_prefer_native and supports_native_pattern else "wrapper"
    )
    cache_key = (base_config.lang, pattern_kind, base_config.ast_prefer_native)
    if backend_cache is not None and cache_key in backend_cache:
        return backend_cache[cache_key]

    backend: ComputeBackend
    if Pipeline.__module__ == "tensor_grep.core.pipeline":
        ast_backend = AstBackend()
        ast_wrapper = AstGrepWrapperBackend()
        if pattern_kind == "native":
            if ast_backend.is_available():
                backend = ast_backend
            elif ast_wrapper.is_available():
                backend = ast_wrapper
            else:
                backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()
        else:
            if ast_wrapper.is_available():
                backend = ast_wrapper
            elif ast_backend.is_available():
                backend = ast_backend
            else:
                backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()
    else:
        backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()

    if backend_cache is not None:
        backend_cache[cache_key] = backend
    return backend


def run_command(pattern: str, path: str | None = None, lang: str | None = None) -> int:
    from tensor_grep.core.config import SearchConfig

    target_path = path or "."
    cfg = SearchConfig(ast=True, lang=lang or "python")
    backend = _select_ast_backend_for_pattern(cfg, pattern, backend_cache={})
    backend_name = type(backend).__name__

    print(f"Executing {_describe_ast_backend_mode(backend_name)} run...")
    if backend_name == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
        result = cast(Any, backend).search_many([target_path], pattern, config=cfg)
    else:
        result = backend.search(target_path, pattern, config=cfg)

    for match in result.matches:
        line = match.line_content.rstrip("\n")
        print(f"{match.file}:{match.line_number}:{line}")

    print(
        f"Run completed. matches={result.total_matches} files={result.total_files} "
        f"backend={backend_name}"
    )
    return 0


def scan_command(config: str | None = "sgconfig.yml") -> int:
    from tensor_grep.core.config import SearchConfig

    try:
        project_cfg = _load_sg_project_config(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rules = _load_rule_specs(project_cfg)
    if not rules:
        print("Error: No valid rules found in configured rule directories.", file=sys.stderr)
        return 1

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    root_dir = cast(Path, project_cfg["root_dir"])
    scanner: DirectoryScanner | None = None
    candidate_files: list[str] | None = None
    backend_cache: dict[tuple[str | None, str, bool], ComputeBackend] = {}
    backend_names_used: set[str] = set()

    print(f"Scanning project using adaptive AST routing based on {project_cfg['config_path']}...")

    wrapper_project_backend: Any | None = None
    wrapper_project_results: dict[str, SearchResult] | None = None
    if rules:
        selected_backends = [
            _select_ast_backend_for_pattern(
                replace(cfg, lang=rule["language"]), rule["pattern"], backend_cache
            )
            for rule in rules
        ]
        if (
            selected_backends
            and all(hasattr(backend, "search_project") for backend in selected_backends)
            and hasattr(selected_backends[0], "search_project")
        ):
            wrapper_project_backend = selected_backends[0]
            backend_names_used.add(type(wrapper_project_backend).__name__)
            try:
                wrapper_project_results = cast(Any, wrapper_project_backend).search_project(
                    str(root_dir), str(project_cfg["config_path"])
                )
            except Exception:
                wrapper_project_results = None

    total_matches = 0
    matched_rules = 0
    for rule in rules:
        rule_cfg = replace(cfg, lang=rule["language"])
        backend = _select_ast_backend_for_pattern(rule_cfg, rule["pattern"], backend_cache)
        backend_names_used.add(type(backend).__name__)
        matched_files: set[str] = set()
        if wrapper_project_results is not None and wrapper_project_backend is backend:
            result = wrapper_project_results.get(
                rule["id"], SearchResult(matches=[], total_files=0, total_matches=0)
            )
            rule_matches = result.total_matches
            matched_files.update(result.matched_file_paths)
            if not matched_files and result.total_files > 0:
                matched_files.update(match.file for match in result.matches if match.file)
        elif type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
            result = cast(Any, backend).search_many(
                [str(root_dir)], rule["pattern"], config=rule_cfg
            )
            rule_matches = result.total_matches
            matched_files.update(result.matched_file_paths)
            if not matched_files and result.total_files > 0:
                matched_files.update(match.file for match in result.matches if match.file)
        else:
            if scanner is None:
                scanner = DirectoryScanner(cfg)
            if candidate_files is None:
                candidate_files, _ = _collect_candidate_files(scanner, [str(root_dir)])
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
    from tensor_grep.core.config import SearchConfig

    try:
        project_cfg = _load_sg_project_config(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rules = _load_rule_specs(project_cfg)
    if not rules:
        print("Error: No valid rules found in configured rule directories.", file=sys.stderr)
        return 1
    rules_by_id = {rule["id"]: rule for rule in rules}

    root_dir = cast(Path, project_cfg["root_dir"])
    test_dirs = cast(list[str], project_cfg["test_dirs"])
    test_files = _iter_yaml_files(root_dir, test_dirs)
    if not test_files:
        print("Error: No test files found in configured test directories.", file=sys.stderr)
        return 1

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    backend_cache: dict[tuple[str | None, str, bool], ComputeBackend] = {}
    backend_names_used: set[str] = set()
    wrapper_case_groups: dict[tuple[int, str, str], dict[str, object]] = {}

    total_cases = 0
    failures: list[str] = []
    for test_file in test_files:
        payload = _load_yaml_dict(test_file)
        raw_cases = payload.get("tests")
        cases = (
            [case for case in raw_cases if isinstance(case, dict)]
            if isinstance(raw_cases, list)
            else [payload]
        )

        for case in cases:
            case_id = str(case.get("id") or test_file.stem)
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
            case_cfg = replace(cfg, lang=language)
            backend = _select_ast_backend_for_pattern(case_cfg, pattern, backend_cache)
            backend_names_used.add(type(backend).__name__)

            if type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(
                backend, "search_many"
            ):
                batch_key = (id(backend), pattern, language)
                batch = wrapper_case_groups.setdefault(
                    batch_key,
                    {
                        "backend": backend,
                        "root_dir": root_dir,
                        "case_cfg": case_cfg,
                        "pattern": pattern,
                        "language": language,
                        "items": [],
                    },
                )
                items = cast(list[tuple[str, str, bool]], batch["items"])
                case_key = f"{test_file}:{case_id}"
                items.extend((case_key, snippet, False) for snippet in valid_snippets)
                items.extend((case_key, snippet, True) for snippet in invalid_snippets)
                continue

            try:
                evaluated_snippets = []
                for expected_match, snippets in ((False, valid_snippets), (True, invalid_snippets)):
                    for snippet in snippets:
                        temp_name = (
                            root_dir
                            / f".tg_rule_test_{uuid4().hex}{_suffix_for_language(language)}"
                        )
                        temp_name.write_text(snippet, encoding="utf-8")
                        try:
                            result = backend.search(str(temp_name), pattern, config=case_cfg)
                            evaluated_snippets.append((
                                f"{test_file}:{case_id}",
                                snippet,
                                expected_match,
                                bool(
                                    result.total_files > 0
                                    or result.total_matches > 0
                                    or result.matched_file_paths
                                ),
                            ))
                        finally:
                            temp_name.unlink(missing_ok=True)
            except Exception as exc:
                failures.append(f"{test_file}:{case_id}: backend error: {exc}")
                continue

            for case_key, snippet, expected_match, has_match in evaluated_snippets:
                if has_match != expected_match:
                    expectation = "match" if expected_match else "no match"
                    failures.append(
                        f"{case_key}: expected {expectation}, got "
                        f"{'match' if has_match else 'no match'} for snippet {snippet!r}"
                    )

    for batch in wrapper_case_groups.values():
        items = cast(list[tuple[str, str, bool]], batch["items"])
        try:
            match_results = _search_ast_test_snippets_with_wrapper(
                batch["backend"],
                root_dir=cast(Path, batch["root_dir"]),
                case_cfg=cast("SearchConfig", batch["case_cfg"]),
                pattern=cast(str, batch["pattern"]),
                language=cast(str, batch["language"]),
                snippets=[snippet for _, snippet, _ in items],
            )
        except Exception as exc:
            for case_key, _, _ in items:
                failures.append(f"{case_key}: backend error: {exc}")
            continue
        for (case_key, snippet, expected_match), has_match in zip(
            items, match_results, strict=True
        ):
            if has_match != expected_match:
                expectation = "match" if expected_match else "no match"
                failures.append(
                    f"{case_key}: expected {expectation}, got "
                    f"{'match' if has_match else 'no match'} for snippet {snippet!r}"
                )

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
    parser = argparse.ArgumentParser(add_help=True, prog="tg")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("pattern")
<<<<<<< HEAD
    run_parser.add_argument("path", nargs="?", default=".")
    run_parser.add_argument("--lang", "-l", default=None)
=======
    run_parser.add_argument("path", nargs="?")
    run_parser.add_argument("--rewrite", "-r", default=None)
    run_parser.add_argument("--lang", "-l", default=None)
    run_parser.add_argument("--config", "-c", default="sgconfig.yml")
>>>>>>> 740dc83 (perf(ast): add direct workflow and project scan fast paths)

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--config", "-c", default="sgconfig.yml")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--config", "-c", default="sgconfig.yml")

    args = parser.parse_args(argv)
    if args.command == "run":
<<<<<<< HEAD
        raise SystemExit(run_command(args.pattern, args.path, args.lang))
=======
        raise SystemExit(
            run_command(
                args.pattern,
                args.path,
                rewrite=args.rewrite,
                lang=args.lang,
                config=args.config,
            )
        )
>>>>>>> 740dc83 (perf(ast): add direct workflow and project scan fast paths)
    if args.command == "scan":
        raise SystemExit(scan_command(args.config))
    if args.command == "test":
        raise SystemExit(test_command(args.config))
    raise SystemExit(2)
