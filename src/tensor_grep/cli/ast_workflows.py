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

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner


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


def _load_test_case_payloads(
    project_cfg: dict[str, object],
) -> list[tuple[Path, list[dict[str, object]]]]:
    root_dir = cast(Path, project_cfg["root_dir"])
    test_dirs = cast(list[str], project_cfg["test_dirs"])

    payloads: list[tuple[Path, list[dict[str, object]]]] = []
    for test_file in _iter_yaml_files(root_dir, test_dirs):
        payload = _load_yaml_dict(test_file)
        raw_cases = payload.get("tests")
        cases = (
            [case for case in raw_cases if isinstance(case, dict)]
            if isinstance(raw_cases, list)
            else [payload]
        )
        payloads.append((test_file, cases))
    return payloads


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
    temp_root: Path | None = None,
) -> list[bool]:
    if not snippets:
        return []

    suffix = _suffix_for_language(language)
    if temp_root is None:
        with TemporaryDirectory(prefix=".tg_rule_test_batch_") as temp_dir:
            return _search_ast_test_snippets_with_wrapper(
                backend,
                root_dir=root_dir,
                case_cfg=case_cfg,
                pattern=pattern,
                language=language,
                snippets=snippets,
                temp_root=Path(temp_dir),
            )

    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        snippet_names: list[str] = []
        for index, snippet in enumerate(snippets):
            snippet_name = f"case_{index}{suffix}"
            snippet_path = temp_root / snippet_name
            snippet_path.write_text(snippet, encoding="utf-8")
            snippet_names.append(snippet_name)

        result = cast(Any, backend).search_many([str(temp_root)], pattern, config=case_cfg)

        def _match_name(raw_path: str) -> str:
            return raw_path.replace("\\", "/").rsplit("/", 1)[-1]

        matched_names = {_match_name(path) for path in result.matched_file_paths if path}
        matched_names.update(_match_name(match.file) for match in result.matches if match.file)
        return [snippet_name in matched_names for snippet_name in snippet_names]
    finally:
        for snippet_file in temp_root.iterdir():
            snippet_file.unlink(missing_ok=True)
        temp_root.rmdir()


def _search_ast_test_batches_with_wrapper_project(
    batches: list[dict[str, object]],
) -> list[list[bool]]:
    from tempfile import TemporaryDirectory

    if not batches:
        return []

    with TemporaryDirectory(prefix=".tg_rule_test_project_") as temp_dir:
        temp_root = Path(temp_dir)
        rules_dir = temp_root / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        config_path = temp_root / "sgconfig.yml"
        config_path.write_text("ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8")

        snippet_names_by_rule: list[list[str]] = []
        rule_ids: list[str] = []

        for batch_index, batch in enumerate(batches):
            rule_id = f"batch-{batch_index}"
            rule_ids.append(rule_id)
            language = cast(str, batch["language"])
            pattern = cast(str, batch["pattern"])
            snippets = cast(list[str], batch["snippets"])

            rule_file = rules_dir / f"{rule_id}.yml"
            rule_file.write_text(
                "\n".join([
                    f"id: {rule_id}",
                    f"language: {language}",
                    "rule:",
                    "  pattern: |",
                    *[f"    {line}" for line in pattern.splitlines()],
                    "",
                ]),
                encoding="utf-8",
            )

            suffix = _suffix_for_language(language)
            snippet_dir = temp_root / f"cases_{batch_index}"
            snippet_dir.mkdir(parents=True, exist_ok=True)
            snippet_names: list[str] = []
            for snippet_index, snippet in enumerate(snippets):
                snippet_name = f"case_{snippet_index}{suffix}"
                (snippet_dir / snippet_name).write_text(snippet, encoding="utf-8")
                snippet_names.append(snippet_name)
            snippet_names_by_rule.append(snippet_names)

        backend = cast(Any, batches[0]["backend"])
        grouped_results = backend.search_project(str(temp_root), str(config_path))

        output: list[list[bool]] = []
        for rule_id, snippet_names in zip(rule_ids, snippet_names_by_rule, strict=True):
            result = grouped_results.get(rule_id)
            if result is None:
                output.append([False for _ in snippet_names])
                continue

            def _match_name(raw_path: str) -> str:
                return raw_path.replace("\\", "/").rsplit("/", 1)[-1]

            matched_names = {_match_name(path) for path in result.matched_file_paths if path}
            matched_names.update(_match_name(match.file) for match in result.matches if match.file)
            output.append([snippet_name in matched_names for snippet_name in snippet_names])
        return output


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
        from tensor_grep.io.directory_scanner import DirectoryScanner

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

    resolved_rules: list[tuple[dict[str, str], SearchConfig, ComputeBackend]] = []
    for rule in rules:
        rule_cfg = replace(cfg, lang=rule["language"])
        backend = _select_ast_backend_for_pattern(rule_cfg, rule["pattern"], backend_cache)
        resolved_rules.append((rule, rule_cfg, backend))

    wrapper_project_backend: Any | None = None
    wrapper_project_results: dict[str, SearchResult] | None = None
    if resolved_rules:
        selected_backends = [backend for _, _, backend in resolved_rules]
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
    for rule, rule_cfg, backend in resolved_rules:
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
            from tensor_grep.io.directory_scanner import DirectoryScanner

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

    root_dir = cast(Path, project_cfg["root_dir"])
    test_payloads = _load_test_case_payloads(project_cfg)
    if not test_payloads:
        print("Error: No test files found in configured test directories.", file=sys.stderr)
        return 1

    cfg = SearchConfig(ast=True, ast_prefer_native=True, lang=cast(str, project_cfg["language"]))
    backend_cache: dict[tuple[str | None, str, bool], ComputeBackend] = {}
    resolved_case_cache: dict[tuple[str, str], tuple[SearchConfig, ComputeBackend]] = {}
    backend_names_used: set[str] = set()
    wrapper_case_groups: dict[tuple[int, str, str], dict[str, object]] = {}
    resolved_rules_by_id: dict[str, tuple[str, str, SearchConfig, ComputeBackend]] = {}

    for rule in rules:
        rule_cfg = replace(cfg, lang=rule["language"])
        rule_backend = _select_ast_backend_for_pattern(rule_cfg, rule["pattern"], backend_cache)
        resolved_rules_by_id[rule["id"]] = (
            rule["pattern"],
            rule["language"],
            rule_cfg,
            rule_backend,
        )

    total_cases = 0
    failures: list[str] = []
    for test_file, cases in test_payloads:
        for case in cases:
            case_id = str(case.get("id") or test_file.stem)
            linked_rule = case.get("ruleId")
            pattern = _extract_rule_pattern(case)
            language = str(case.get("language") or cfg.lang or "python")
            case_cfg: SearchConfig
            backend: ComputeBackend
            if not pattern and isinstance(linked_rule, str) and linked_rule in resolved_rules_by_id:
                rule_pattern, rule_language, rule_cfg, rule_backend = resolved_rules_by_id[
                    linked_rule
                ]
                pattern = rule_pattern
                language = str(case.get("language") or rule_language)
                if language == rule_language:
                    case_cfg = rule_cfg
                    backend = rule_backend
                else:
                    cache_key = (pattern, language)
                    cached = resolved_case_cache.get(cache_key)
                    if cached is None:
                        case_cfg = replace(cfg, lang=language)
                        backend = _select_ast_backend_for_pattern(case_cfg, pattern, backend_cache)
                        resolved_case_cache[cache_key] = (case_cfg, backend)
                    else:
                        case_cfg, backend = cached
            if not pattern:
                failures.append(f"{test_file}:{case_id}: missing pattern or ruleId")
                continue

            if pattern and not (
                isinstance(linked_rule, str) and linked_rule in resolved_rules_by_id
            ):
                cache_key = (pattern, language)
                cached = resolved_case_cache.get(cache_key)
                if cached is None:
                    case_cfg = replace(cfg, lang=language)
                    backend = _select_ast_backend_for_pattern(case_cfg, pattern, backend_cache)
                    resolved_case_cache[cache_key] = (case_cfg, backend)
                else:
                    case_cfg, backend = cached

            valid_snippets = _normalize_string_list(case.get("valid"), [])
            invalid_snippets = _normalize_string_list(case.get("invalid"), [])
            if not valid_snippets and not invalid_snippets:
                failures.append(f"{test_file}:{case_id}: empty valid/invalid test lists")
                continue

            total_cases += len(valid_snippets) + len(invalid_snippets)
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

    wrapper_batches = list(wrapper_case_groups.values())
    if wrapper_batches:
        try:
            grouped_match_results = _search_ast_test_batches_with_wrapper_project([
                {
                    "backend": batch["backend"],
                    "pattern": batch["pattern"],
                    "language": batch["language"],
                    "snippets": [
                        snippet
                        for _, snippet, _ in cast(list[tuple[str, str, bool]], batch["items"])
                    ],
                }
                for batch in wrapper_batches
            ])
        except Exception as exc:
            for batch in wrapper_batches:
                for case_key, _, _ in cast(list[tuple[str, str, bool]], batch["items"]):
                    failures.append(f"{case_key}: backend error: {exc}")
        else:
            for batch, match_results in zip(wrapper_batches, grouped_match_results, strict=True):
                items = cast(list[tuple[str, str, bool]], batch["items"])
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
    run_parser.add_argument("path", nargs="?")
    run_parser.add_argument("--rewrite", "-r", default=None)
    run_parser.add_argument("--lang", "-l", default=None)
    run_parser.add_argument("--config", "-c", default="sgconfig.yml")

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--config", "-c", default="sgconfig.yml")

    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("--config", "-c", default="sgconfig.yml")

    args = parser.parse_args(argv)
    if args.command == "run":
        raise SystemExit(
            run_command(
                args.pattern,
                args.path,
                rewrite=args.rewrite,
                lang=args.lang,
                config=args.config,
            )
        )
    if args.command == "scan":
        raise SystemExit(scan_command(args.config))
    if args.command == "test":
        raise SystemExit(test_command(args.config))
    raise SystemExit(2)
