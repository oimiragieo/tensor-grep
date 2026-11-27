from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from tensor_grep.cli import predictive_test_selector as _predictive_test_selector
from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli.repo_map_cache import _mtime_aware_cache


def _is_clean_symbol_name(name: str) -> bool:
    return _repo_map.lang_registry.is_clean_symbol_name(name)


def _node_has_ancestor_type(node: Any, ancestor_types: set[str]) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type in ancestor_types:
            return True
        current = getattr(current, "parent", None)
    return False


def _line_span_from_offsets(source: str, start_offset: int, end_offset: int) -> tuple[int, int]:
    start_line = source.count("\n", 0, max(0, start_offset)) + 1
    normalized_end = max(0, end_offset - 1)
    end_line = source.count("\n", 0, normalized_end) + 1
    return start_line, max(start_line, end_line)


def _normalized_repo_root(repo_root: _repo_map.Path | str | None) -> _repo_map.Path | None:
    if repo_root is None:
        return None
    # Fix B: repo_root is constant across an entire caller_scan, but this is called on every
    # _js_ts_repo_context / _js_ts_resolve_exported_symbol invocation -- route through the
    # process-wide resolve() cache instead of re-walking the filesystem each time.
    return _repo_map.Path(
        _repo_map._resolved_path_str(str(_repo_map.Path(str(repo_root)).expanduser()))
    )


def _dedupe_labels(labels: list[str]) -> list[str]:
    ordered: list[str] = []
    for label in labels:
        normalized = str(label).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _parse_js_ts_tsconfig(root: _repo_map.Path) -> dict[str, Any]:
    tsconfig_path = root / "tsconfig.json"
    payload: dict[str, Any] = {
        "exists": False,
        "base_url": None,
        "paths": [],
    }
    if not _repo_map._is_file_with_receipt(tsconfig_path):
        return payload

    try:
        parsed = json.loads(_repo_map._read_text_with_receipt(tsconfig_path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return payload

    compiler_options = parsed.get("compilerOptions", {})
    if not isinstance(compiler_options, dict):
        return payload

    payload["exists"] = True
    base_url = compiler_options.get("baseUrl")
    if isinstance(base_url, str) and base_url.strip():
        payload["base_url"] = str(_repo_map._resolved_input_path(root / base_url))

    raw_paths = compiler_options.get("paths", {})
    if isinstance(raw_paths, dict):
        normalized_paths: list[dict[str, Any]] = []
        for pattern, targets in raw_paths.items():
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            if isinstance(targets, str):
                target_list = [targets]
            elif isinstance(targets, list):
                target_list = [str(target) for target in targets if isinstance(target, str)]
            else:
                continue
            if not target_list:
                continue
            normalized_paths.append({"pattern": pattern, "targets": target_list})
        payload["paths"] = normalized_paths
    return payload


def _prime_js_ts_repo_context(root: _repo_map.Path) -> dict[str, Any]:
    normalized_root = _repo_map._resolved_input_path(root)
    key = str(normalized_root)
    context = {
        "root": key,
        "tsconfig": _repo_map._parse_js_ts_tsconfig(normalized_root),
        "re_export_cache": {},
    }
    return _repo_map._remember_repo_context(_repo_map._JS_TS_REPO_CONTEXTS, key, context)


def _expand_js_ts_tsconfig_target(
    module_name: str,
    pattern: str,
    target: str,
) -> str | None:
    if "*" not in pattern:
        return target if module_name == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not module_name.startswith(prefix):
        return None
    if suffix and not module_name.endswith(suffix):
        return None
    token_end = len(module_name) - len(suffix) if suffix else len(module_name)
    token = module_name[len(prefix) : token_end]
    return target.replace("*", token, 1)


def _split_top_level_list(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    brace_depth = 0
    for char in text:
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        if char == "," and brace_depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _is_valid_rust_use_path(path: str) -> bool:
    """Return ``True`` when ``path`` is a syntactically valid Rust ``use`` path.

    The ``use ... ;`` regex used to collect bindings is intentionally permissive
    (``DOTALL``) and can latch onto the word ``use`` inside a doc comment such as
    ``/// We only use the trigram index ... ;``. Those false positives produce
    module names containing whitespace/newlines that later blow up
    ``Path.with_suffix`` with ``empty name`` ``ValueError``s. A genuine use path is
    a ``::``-separated list of Rust identifiers (plus ``*`` for globs), so reject
    anything else here.
    """

    candidate = path.strip()
    if not candidate:
        return False
    segments = candidate.split("::")
    return all(_repo_map._RUST_USE_SEGMENT_RE.match(segment.strip()) for segment in segments)


def _flatten_rust_use_items(expression: str, prefix: str = "") -> list[str]:
    normalized = expression.strip()
    if not normalized:
        return []

    brace_index = normalized.find("{")
    if brace_index < 0:
        return [f"{prefix}::{normalized}".strip(":") if prefix else normalized]

    prefix_part = normalized[:brace_index].rstrip(":").strip()
    combined_prefix = prefix
    if prefix_part:
        combined_prefix = f"{prefix}::{prefix_part}".strip(":") if prefix else prefix_part

    closing_index = normalized.rfind("}")
    if closing_index < 0:
        return [combined_prefix] if combined_prefix else []
    inner = normalized[brace_index + 1 : closing_index]

    flattened: list[str] = []
    for item in _repo_map._split_top_level_list(inner):
        if item == "self":
            if combined_prefix:
                flattened.append(combined_prefix)
            continue
        if "{" in item:
            flattened.extend(_repo_map._flatten_rust_use_items(item, combined_prefix))
            continue
        flattened.append(f"{combined_prefix}::{item}".strip(":") if combined_prefix else item)
    return flattened


def _build_rust_module_tree(
    entry_path: _repo_map.Path,
    *,
    _prefix: tuple[str, ...] = (),
    _visited: set[str] | None = None,
) -> dict[str, str]:
    normalized_entry = _repo_map._resolved_input_path(entry_path)
    visited = set() if _visited is None else set(_visited)
    current_key = str(normalized_entry)
    if current_key in visited:
        return {}
    visited.add(current_key)

    try:
        source = _repo_map._read_text_with_receipt(normalized_entry)
    except (OSError, UnicodeDecodeError):
        return {}

    module_tree: dict[str, str] = {}
    for module_name in _repo_map._rust_mod_declarations(source):
        module_file = _repo_map._rust_module_file_for_declaration(normalized_entry, module_name)
        if module_file is None:
            continue
        module_parts = (*_prefix, module_name)
        module_tree["::".join(module_parts)] = str(module_file)
        module_tree.update(
            _repo_map._build_rust_module_tree(
                module_file,
                _prefix=module_parts,
                _visited=visited,
            )
        )
    return module_tree


def _normalize_rust_crate_name(name: str) -> str:
    return str(name).strip().replace("-", "_")


def _parse_rust_workspace_members(root: _repo_map.Path) -> dict[str, Any]:
    cargo_toml = root / "Cargo.toml"
    payload: dict[str, Any] = {
        "exists": False,
        "members": {},
    }
    if not _repo_map._is_file_with_receipt(cargo_toml):
        return payload

    try:
        parsed = tomllib.loads(_repo_map._read_text_with_receipt(cargo_toml))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return payload

    workspace = parsed.get("workspace", {})
    if not isinstance(workspace, dict):
        return payload

    raw_members = workspace.get("members", [])
    member_patterns = [raw_members] if isinstance(raw_members, str) else raw_members
    if not isinstance(member_patterns, list):
        return payload

    members: dict[str, str] = {}
    for member_pattern in member_patterns:
        if not isinstance(member_pattern, str) or not member_pattern.strip():
            continue
        has_glob = any(token in member_pattern for token in {"*", "?", "["})
        member_dirs = (
            _repo_map._glob_dirs_with_receipt(root, member_pattern)
            if has_glob
            else [root / member_pattern]
        )
        for member_dir in member_dirs:
            normalized_dir = _repo_map._resolved_input_path(member_dir)
            member_cargo = normalized_dir / "Cargo.toml"
            if not _repo_map._is_file_with_receipt(member_cargo):
                continue
            crate_name = _repo_map._normalize_rust_crate_name(normalized_dir.name)
            try:
                member_parsed = tomllib.loads(_repo_map._read_text_with_receipt(member_cargo))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                member_parsed = {}
            package = member_parsed.get("package", {})
            if isinstance(package, dict):
                package_name = package.get("name")
                if isinstance(package_name, str) and package_name.strip():
                    crate_name = _repo_map._normalize_rust_crate_name(package_name)
            entry_path = _repo_map._resolved_input_path(normalized_dir / "src" / "lib.rs")
            if not _repo_map._is_file_with_receipt(entry_path):
                fallback_entry = _repo_map._resolved_input_path(normalized_dir / "src" / "main.rs")
                if _repo_map._is_file_with_receipt(fallback_entry):
                    entry_path = fallback_entry
                else:
                    continue
            members[crate_name] = str(entry_path)
    if members:
        payload["exists"] = True
        payload["members"] = members
    return payload


def _prime_rust_repo_context(root: _repo_map.Path) -> dict[str, Any]:
    normalized_root = _repo_map._resolved_input_path(root)
    key = str(normalized_root)
    context = {
        "root": key,
        "workspace": _repo_map._parse_rust_workspace_members(normalized_root),
        "mod_tree_cache": {},
    }
    return _repo_map._remember_repo_context(_repo_map._RUST_REPO_CONTEXTS, key, context)


def _extract_rust_impl_block(source: str, start_index: int) -> str:
    depth = 0
    block_start = -1
    for index in range(start_index, len(source)):
        current = source[index]
        if current == "{":
            if depth == 0:
                block_start = index + 1
            depth += 1
        elif current == "}":
            if depth == 0:
                break
            depth -= 1
            if depth == 0 and block_start >= 0:
                return source[block_start:index]
    return ""


def _definition_module_parts(path: str) -> list[str]:
    # audit #81 #11: case-folding every path segment made `Foo.py` match `foo.py` on a
    # case-SENSITIVE filesystem (Linux CI/prod) -> wrong-file attribution in reverse-import
    # edges. Gate the fold on Windows only, mirroring `_definition_file_dedupe_key` (below),
    # which already conditions its case-handling on `os.name == "nt"` for the same reason.
    raw_parts = [part for part in _repo_map.Path(path).with_suffix("").parts if part]
    if _repo_map.os.name == "nt":
        raw_parts = [part.lower() for part in raw_parts]
    parts = [part for part in raw_parts if part not in {".", ".."} and not part.endswith(":\\")]
    if not parts:
        return []
    # The __init__/index/mod magic-name check stays case-insensitive on every platform: these
    # are real on-disk filenames that are always already lowercase by language convention
    # (CPython only ever treats a literal `__init__.py` as a package initializer), so comparing
    # case-insensitively here does not reintroduce the false-edge risk the fold above removed.
    if parts[-1].lower() in {"__init__", "index", "mod"} and len(parts) > 1:
        parts = parts[:-1]
    return parts


def _normalized_module_parts(module_name: str) -> list[str]:
    # audit #81 #11: see _definition_module_parts above -- same Windows-only case-fold gate.
    raw_parts = [part for part in re.split(r"[^A-Za-z0-9_]+", module_name) if part]
    parts = [part.lower() for part in raw_parts] if _repo_map.os.name == "nt" else raw_parts
    while parts and parts[0] in {"crate", "self", "super"}:
        parts = parts[1:]
    return parts


def _module_path_matches_definition(module_name: str, definition_path: str) -> bool:
    module_parts = _repo_map._normalized_module_parts(module_name)
    definition_parts = _repo_map._definition_module_parts(definition_path)
    if not module_parts or not definition_parts:
        return False
    return definition_parts[-len(module_parts) :] == module_parts


@_mtime_aware_cache(maxsize=2048)  # Fix A: mtime+size in key; replaces per-call read+parse
def _file_imports_symbol_from_definition(
    path_str: str,
    symbol: str,
    definition_path: str,
    repo_root: _repo_map.Path | str | None = None,
) -> bool:
    file_path = _repo_map.Path(path_str)
    try:
        source = _repo_map._read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return False
    spec = _repo_map.lang_registry.spec_for_path(file_path)
    if spec is None or spec.file_imports_symbol_from_definition is None:
        return False
    return spec.file_imports_symbol_from_definition(
        file_path, source, symbol, definition_path, repo_root
    )


def _import_names_reference_symbol_definition(
    import_names: list[str],
    *,
    importer_path: _repo_map.Path,
    symbol: str,
    definition_path: str,
) -> bool:
    normalized_symbol = symbol.strip()
    if not normalized_symbol:
        return False

    for raw_import_name in import_names:
        import_name = str(raw_import_name).strip()
        if not import_name:
            continue
        variants = [import_name]
        rust_like = import_name.replace("::", ".")
        if rust_like != import_name:
            variants.append(rust_like)

        for candidate in variants:
            if candidate.endswith(f".{normalized_symbol}") or candidate.endswith(".*"):
                module_name = candidate.rsplit(".", 1)[0]
                if _repo_map._module_path_matches_definition(module_name, definition_path):
                    return True
            if importer_path.suffix.lower() == ".py" and _repo_map._module_path_matches_definition(
                candidate,
                definition_path,
            ):
                return True
    return False


def _direct_validation_import_count_from_repo_map(
    *,
    tests: list[str],
    imports_by_file: dict[str, list[str]],
    symbol: str,
    definition_path: str,
) -> int:
    count = 0
    for test_path in tests[: _repo_map._DIRECT_VALIDATION_TEST_SCAN_LIMIT]:
        test_file = _repo_map.Path(str(test_path))
        if _repo_map._import_names_reference_symbol_definition(
            imports_by_file.get(str(test_path), []),
            importer_path=test_file,
            symbol=symbol,
            definition_path=definition_path,
        ):
            count += 1
    return count


def _import_update_target(
    file_path: _repo_map.Path,
    symbol: str,
    definition_path: str,
    repo_root: _repo_map.Path | str | None = None,
) -> dict[str, Any] | None:
    if str(_repo_map._resolved_input_path(file_path)) == str(
        _repo_map._resolved_input_path(definition_path)
    ):
        return None
    spec = _repo_map.lang_registry.spec_for_path(file_path)
    if spec is None or spec.import_update_target is None:
        return None
    return spec.import_update_target(file_path, symbol, definition_path, repo_root)


def _source_line_text(path: _repo_map.Path, line_number: int) -> str:
    if line_number <= 0:
        return ""
    try:
        lines = _repo_map.lang_registry.split_source_lines(
            _repo_map.lang_registry.read_source_text(path)
        )
    except OSError:
        return ""
    return lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""


def _import_graph_resolution_confidence(provenance: str) -> float:
    if provenance == "parser-backed":
        return 0.95
    if provenance in {"heuristic", "regex-heuristic"}:
        return 0.65
    return 0.75


def _build_import_graph_consumers_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    definition_files: list[str],
    *,
    bounded_files: list[_repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> list[dict[str, Any]]:
    if not definition_files:
        return []
    repo_root = _repo_map._repo_map_root_dir(repo_map)
    definition_file_set = {str(current) for current in definition_files}
    files = (
        bounded_files if bounded_files is not None else _repo_map._repo_map_file_universe(repo_map)
    )
    consumers: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    with _repo_map._profiling_phase(_profiling_collector, "import_graph_consumers"):
        for current in files:
            # task #61: this loop re-walks the same up-to-CEILING file set the caller-scan main
            # loop just bounded, but ran AFTER it with no deadline check of its own -- for a
            # central symbol the main loop finished inside budget while THIS sibling loop pushed
            # wall-clock well past --deadline (profiled: `callers ... --deadline 10` -> ~25s).
            # Mirror the main loop's check (repo_map.py caller-scan, build_symbol_callers_from_map)
            # exactly, using the SAME shared deadline value.
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            current_file = str(current)
            if current_file in definition_file_set:
                continue
            if _repo_map.lang_registry.spec_for_path(current) is None:
                continue
            if not _repo_map._file_may_import_symbol_definition(current, definition_files):
                continue
            for definition_file in definition_files:
                target = _repo_map._import_update_target(
                    current, symbol, definition_file, repo_root
                )
                if target is None:
                    continue
                line = int(target.get("start_line", 0) or 0)
                end_line = int(target.get("end_line", line) or line)
                module = str(target.get("module", "") or "")
                provenance = str(target.get("provenance", "heuristic") or "heuristic")
                key = (current_file, line, end_line, definition_file, module)
                if key in seen:
                    continue
                seen.add(key)
                consumers.append({
                    "file": current_file,
                    "line": line,
                    "end_line": end_line,
                    "text": _repo_map._source_line_text(current, line),
                    "kind": "import-consumer",
                    "edge_kind": "reverse-import",
                    "definition_file": definition_file,
                    "module": module,
                    "provenance": provenance,
                    "resolution_confidence": _repo_map._import_graph_resolution_confidence(
                        provenance
                    ),
                })
    consumers.sort(
        key=lambda item: (
            str(item["file"]),
            int(item.get("line", 0) or 0),
            str(item.get("module", "")),
            str(item.get("definition_file", "")),
        )
    )
    return consumers


def _preferred_definition_files(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> list[str]:
    repo_root = _repo_map._repo_map_root_dir(repo_map)
    definitions = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("name")) == symbol
    ]
    definition_files = list(dict.fromkeys(str(current["file"]) for current in definitions))
    if len(definition_files) <= 1:
        return definition_files

    scores = dict.fromkeys(definition_files, 0)
    for current in _repo_map._repo_map_file_universe(repo_map):
        # task #61: this loop iterates the FULL repo-map universe (NOT the bounded caller-scan
        # file set) with no deadline check -- unbounded even when the caller-scan main loop this
        # function feeds into is correctly bounded. Mirror the same deadline check.
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        current_path = str(current)
        if current_path in scores:
            continue
        for definition_file in definition_files:
            if _repo_map._file_imports_symbol_from_definition(
                current,
                symbol,
                definition_file,
                repo_root,
            ):
                scores[definition_file] += 2 if _repo_map._is_test_file(current) else 1

    preferred = [current for current, score in scores.items() if score > 0]
    return preferred or definition_files


def _relevant_tests_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definition_files: list[str],
    *,
    caller_files: list[str] | None = None,
    fallback_tests: list[str] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _test_scan_counts: _repo_map._TestScanCounts | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
    _graph_uncertainty: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    repo_root = _repo_map._repo_map_root_dir(repo_map)
    tests = [str(current) for current in repo_map.get("tests", [])]
    caller_set = set(caller_files or [])
    symbol_candidates = [symbol]
    for definition_file in definition_files:
        definition_symbol = next(
            (
                current
                for current in repo_map.get("symbols", [])
                if str(current.get("file")) == str(definition_file)
                and str(current.get("name")) == symbol
            ),
            None,
        )
        if not definition_symbol:
            continue
        if str(definition_file).endswith(".rs"):
            owner_type = _repo_map._rust_impl_owner_type(
                str(definition_file),
                int(definition_symbol.get("line", definition_symbol.get("start_line", 0)) or 0),
            )
            if owner_type and owner_type not in symbol_candidates:
                symbol_candidates.append(owner_type)
    if caller_files:
        source_files = list(
            dict.fromkeys([*(str(current) for current in caller_files or []), *definition_files])
        )
        all_files = [str(current) for current in repo_map.get("files", [])]
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in repo_map.get("imports", [])
        }
        # #691 gate NIT-1 (#222 residual, still-reachable cold path): this function already
        # declares `deadline_monotonic`/`deadline_hit` (used by the direct_definition_tests loop
        # below) but left THESE three whole-repo graph calls un-gated -- the identical ~n^2.2 BFS
        # `_reverse_import_distances` bounded elsewhere in this module. `build_symbol_callers_
        # from_map` reaches this exact block with BOTH `caller_files=` (non-None, so this branch
        # runs) AND a real `deadline_monotonic` (the `tg callers --deadline SYMBOL` budget), so a
        # high-fan-in symbol could still blow the whole-repo BFS/reverse-index cost this PR exists
        # to bound. Thread the two kwargs already in scope, same per-item-inside-the-expensive-
        # loop shape as every other call site this PR fixed.
        reverse_importers = _repo_map._reverse_importers(
            all_files,
            imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        graph_uncertainty: dict[str, dict[str, Any]] = {}
        graph_candidate_statuses: dict[str, dict[str, Any]] = {}
        file_distances = _repo_map._reverse_import_distances(
            source_files,
            all_files,
            imports_by_file,
            repo_root=repo_root,
            uncertain_candidates=graph_uncertainty,
            candidate_statuses=graph_candidate_statuses,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        for target, importers in reverse_importers.items():
            reverse_importers[target] = {
                importer
                for importer in importers
                if graph_candidate_statuses.get(importer, {}).get("status") != "nonedge"
            }
        graph_scores = _repo_map._personalized_reverse_import_pagerank(
            source_files,
            all_files,
            reverse_importers,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        file_scores: dict[str, int] = {}
        for current in source_files:
            file_scores[current] = max(file_scores.get(current, 0), 5)
        for current, depth in file_distances.items():
            file_scores[current] = max(file_scores.get(current, 0), max(1, 5 - int(depth)))
        test_graph_targets = list(dict.fromkeys([*source_files, *file_distances]))[
            : _repo_map._GRAPH_PAGERANK_SEED_FILE_LIMIT
        ]

        test_matches = _repo_map._context_tests(
            source_files,
            tests,
            _repo_map._query_terms(symbol),
            imports_by_file,
            file_distances,
            graph_scores,
            file_scores,
            raw_query=symbol,
            candidate_statuses=graph_candidate_statuses,
            test_graph_targets=test_graph_targets,
            repo_root=repo_root,
            # opt10 #3: this call's `source_files` (caller_files + definition_files) is the fully
            # CONSUMED copy -- `test_matches` here feeds `ranked`/`ordered` in full just below, not
            # test_matches[:1] -- so only the deadline gate is threaded (already in scope as this
            # function's own params, used by the sibling loops below); _test_source_limit is
            # deliberately NOT applied at this call site.
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            # Adversarial gate on #904: this scan was UNCOUNTED, so a budget that expired here
            # still reported scanned == total -- the attribution exonerating the stage that
            # actually stopped, which is the failure the counter exists to prevent. It is also
            # the likelier one to trip: `_test_source_limit` is deliberately not applied above,
            # so it is the more expensive of the two.
            _test_scan_counts=_test_scan_counts,
        )
        if _graph_uncertainty is not None:
            _graph_uncertainty.update(graph_uncertainty)
        direct_definition_tests = []
        for current in tests:
            # #52 fix (loop B): this loop re-walks the FULL tests list with no deadline check --
            # unbounded even when the caller-scan/impact seams that feed it are correctly bounded
            # (dominant cause of the 23x --deadline overrun on a high-fan-out symbol like "main").
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            path = _repo_map.Path(current)
            if any(
                _repo_map._file_imports_symbol_from_definition(
                    path,
                    candidate_symbol,
                    definition_file,
                    repo_root,
                )
                for candidate_symbol in symbol_candidates
                for definition_file in definition_files
            ):
                direct_definition_tests.append(current)

        ranked = [str(current["path"]) for current in test_matches]
        ordered: list[str] = []
        for current in [*direct_definition_tests, *ranked]:
            if current not in ordered:
                ordered.append(current)
        if ordered:
            return ordered
    related: list[str] = []
    for current in tests:
        # #52 fix (loop B): same unbounded-full-tests-list hazard as the direct_definition_tests
        # loop above (this branch runs whenever `caller_files` is falsy, or the ranked/direct
        # branch above found nothing) -- guard it identically.
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        if current in caller_set:
            related.append(current)
            continue
        path = _repo_map.Path(current)
        if any(
            _repo_map._file_imports_symbol_from_definition(
                path,
                candidate_symbol,
                definition_file,
                repo_root,
            )
            for candidate_symbol in symbol_candidates
            for definition_file in definition_files
        ):
            related.append(current)
    if related:
        return related
    if fallback_tests:
        return list(dict.fromkeys(str(current) for current in fallback_tests))
    return []


def _related_test_stem(stem: str) -> str:
    normalized = stem.lower()
    for suffix in (".test", ".spec"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.removeprefix("test_")


def _discover_validation_tests_for_primary_file(
    scoped_root: str | _repo_map.Path,
    primary_file: str | None,
    *,
    primary_symbol_name: str | None,
    query: str,
    limit: int,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    unreadable_hit: _repo_map._UnreadablePathFlag | None = None,
) -> list[str]:
    return _predictive_test_selector.discover_and_rank_tests_for_primary_file(
        scoped_root,
        primary_file,
        primary_symbol_name=primary_symbol_name,
        query=query,
        limit=limit,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
        unreadable_hit=unreadable_hit,
        repo_map_mod=_repo_map,
    )


def _dedupe_symbol_records(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for current in symbols:
        line = int(current.get("line", current.get("start_line", 0)) or 0)
        end_line = int(current.get("end_line", line) or line)
        key = (
            str(current.get("file", "")),
            line,
            end_line,
            str(current.get("kind", "")),
            str(current.get("name", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(current))
    deduped.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return deduped


def _python_references_and_calls(
    path: _repo_map.Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix.lower() != ".py":
        return [], []

    try:
        source = _repo_map.lang_registry.read_source_text(path)
        tree = _repo_map._cached_ast_parse(source)
    except (OSError, SyntaxError):
        return [], []

    lines = _repo_map.lang_registry.split_source_lines(source)
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    # Manual recursive walk (equivalent to ast.NodeVisitor's generic_visit dispatch, which the
    # prior Visitor class relied on) so we can thread `parent` + `in_annotation` context down to
    # the match sites for ref_kind classification -- ast.NodeVisitor gives no parent access.
    def _walk(
        node: _repo_map.ast.AST, parent: _repo_map.ast.AST | None, in_annotation: bool
    ) -> None:
        if isinstance(node, _repo_map.ast.Name) and node.id == symbol:
            references.append({
                "name": symbol,
                "kind": "reference",
                "ref_kind": _repo_map._python_classify_ref_kind(
                    node, parent, in_annotation=in_annotation
                ),
                "file": str(path),
                "line": node.lineno,
                "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            })
        elif isinstance(node, _repo_map.ast.Attribute) and node.attr == symbol:
            references.append({
                "name": symbol,
                "kind": "reference",
                "ref_kind": _repo_map._python_classify_ref_kind(
                    node, parent, in_annotation=in_annotation
                ),
                "file": str(path),
                "line": node.lineno,
                "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            })
        elif isinstance(node, _repo_map.ast.Call):
            matched = False
            if isinstance(node.func, _repo_map.ast.Name) and node.func.id == symbol:
                matched = True
            elif isinstance(node.func, _repo_map.ast.Attribute) and node.func.attr == symbol:
                matched = True
            if matched:
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.lineno,
                    "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                })

        for field_name, value in _repo_map.ast.iter_fields(node):
            if isinstance(node, _repo_map.ast.Call) and field_name in ("args", "keywords"):
                # F19 fix (audit #63): a Call's arguments are runtime VALUES, not type syntax,
                # even when the call itself sits inside a type-annotation subtree (e.g.
                # `Annotated[int, validate(LIMIT)]`) -- only the callee should keep the
                # enclosing annotation context, and that's moot anyway since the parent-is-Call
                # precedence above always wins for the callee regardless of in_annotation.
                child_in_annotation = False
            else:
                child_in_annotation = in_annotation or field_name in ("annotation", "returns")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, _repo_map.ast.AST):
                        _walk(item, node, child_in_annotation)
            elif isinstance(value, _repo_map.ast.AST):
                _walk(value, node, child_in_annotation)

    _walk(tree, None, False)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls
