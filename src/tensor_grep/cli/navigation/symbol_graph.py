from __future__ import annotations

import json
from functools import partial
from typing import Any, cast

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_fast_cache as _fast_cache
from tensor_grep.cli import repo_map_graph as _repo_map_graph


def build_repo_map(
    path: str | _repo_map.Path = ".",
    *,
    max_repo_files: int | None = None,
    extra_files: list[_repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
    exclude_files: frozenset[_repo_map.Path] = frozenset(),
) -> dict[str, Any]:
    """Build repository context, optionally excluding exact lexically absolute file paths."""
    root = _repo_map._resolved_input_path(path)
    if not _repo_map._path_exists_with_receipt(root):
        raise FileNotFoundError(f"Path not found: {root}")
    normalized_max_repo_files = max(1, int(max_repo_files)) if max_repo_files is not None else None
    exclude_files = frozenset(
        _repo_map.Path(_repo_map.os.path.abspath(current)) for current in exclude_files
    )
    walk_options: dict[str, Any] = {"exclude_files": exclude_files} if exclude_files else {}

    with _repo_map._profiling_phase(_profiling_collector, "repo_map_build"):
        context_root = root if _repo_map._is_dir_with_receipt(root) else root.parent
        _repo_map._clear_all_source_caches()
        _repo_map._load_gitignore_matcher.cache_clear()
        _repo_map._prime_all_language_repo_contexts(context_root)
        generation = _repo_map.SymbolGeneration(
            _fast_cache.canonical_symbol_cache_root(
                root, _repo_map._infer_project_root, _repo_map.authenticated_content_root()
            ),
            scope=root,
            max_bytes=_repo_map._max_parse_bytes(),
            ignore_policy=sorted(_repo_map._SKIP_DIR_NAMES),
            deadline=deadline_monotonic,
        )
        payload = _repo_map._envelope(root)
        # Walking and parsing share the deadline; either phase can make the result partial.
        repo_walk_deadline_hit = _repo_map._DeadlineBreakFlag()
        # Unreadable subtrees must remain visible independently of count/time bounds.
        repo_walk_unreadable_hit = _repo_map._UnreadablePathFlag()
        if _profiling_collector is None:
            all_files = _repo_map._iter_repo_files(
                root,
                max_files=normalized_max_repo_files,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=repo_walk_deadline_hit,
                unreadable_hit=repo_walk_unreadable_hit,
                **walk_options,
            )
        else:
            all_files = _repo_map._iter_repo_files(
                root,
                max_files=normalized_max_repo_files,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=repo_walk_deadline_hit,
                _profiling_collector=_profiling_collector,
                unreadable_hit=repo_walk_unreadable_hit,
                **walk_options,
            )
        capped_file_count = len(all_files)
        if extra_files:
            seen_files = {str(_repo_map._safe_resolve(current)) for current in all_files}
            for extra_file in extra_files:
                if (
                    exclude_files
                    and _repo_map.Path(_repo_map.os.path.abspath(extra_file)) in exclude_files
                ):
                    continue
                normalized_extra_file = _repo_map._safe_resolve(extra_file)
                if str(normalized_extra_file) not in seen_files:
                    all_files.append(normalized_extra_file)
                    seen_files.add(str(normalized_extra_file))
        context_files = [
            current
            for current in all_files
            if _repo_map._is_repo_context_file(current, context_root)
        ]
        tests = [str(current) for current in context_files if _repo_map._is_test_file(current)]
        non_test_source_files = [
            str(current) for current in context_files if not _repo_map._is_test_file(current)
        ]
        source_files = non_test_source_files or tests

        imports: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        # Preserve parsed results on timeout; a truncated walk is partial before parsing starts.
        deadline_hit = repo_walk_deadline_hit.hit
        files_scanned = 0
        for current in context_files:
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                deadline_hit = True
                break
            if _profiling_collector is None:
                current_imports, current_symbols = generation.product(
                    current, partial(_repo_map._imports_and_symbols_for_path, current)
                )
            else:
                current_imports, current_symbols = generation.product(
                    current,
                    partial(
                        _repo_map._imports_and_symbols_for_path,
                        current,
                        _profiling_collector=_profiling_collector,
                    ),
                )
            if current_imports:
                imports.append({
                    "file": str(current),
                    "imports": current_imports,
                    "provenance": _repo_map._symbol_navigation_provenance_for_path(str(current)),
                })
            symbols.extend(current_symbols)
            files_scanned += 1

        payload["files"] = source_files
        payload["symbols"] = symbols
        payload["imports"] = imports
        payload["tests"] = tests
        payload["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
        if normalized_max_repo_files is not None:
            _capped = capped_file_count >= normalized_max_repo_files
            _cause = (
                _repo_map._scan_limit_cause(
                    all_files, context_root, capped_file_count, normalized_max_repo_files
                )
                if _capped
                else "project-files"
            )
            _truncated = _capped and _cause == "project-files"
            payload["scan_limit"] = {
                "max_repo_files": normalized_max_repo_files,
                "scanned_files": capped_file_count,
                # possibly_truncated is True only when project (non-vendor) files
                # were dropped; kept for back-compat but see truncation_cause for
                # the full picture.
                "possibly_truncated": _truncated,
                "truncation_cause": _cause if _capped else None,
                # Use _capped: unreadable-path has no dropped-project flag but still needs a remedy.
                **({"budget_remediable": _repo_map.budget_remediable(_cause)} if _capped else {}),
            }
            payload["scan_remediation"] = (
                _repo_map._SCAN_LIMIT_TRUNCATED_REMEDIATION if _truncated else None
            )
        # Keep count and deadline limits distinct: their remedies differ.
        if deadline_hit:
            payload["partial"] = True
            payload["deadline_limit"] = {
                "deadline_exceeded": True,
                "files_scanned": files_scanned,
                "files_total": len(context_files),
            }
        # More budget cannot fix unreadable paths; omit this separate field on complete scans.
        if repo_walk_unreadable_hit.hit:
            payload["unreadable_paths"] = {
                "count": repo_walk_unreadable_hit.count,
                "sample": list(repo_walk_unreadable_hit.sample),
            }
        generation.finish(
            payload,
            complete=not (
                deadline_hit
                or repo_walk_unreadable_hit.hit
                or payload.get("scan_limit", {}).get("possibly_truncated")
            ),
        )
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_repo_map_incremental(
    previous_map: dict[str, Any],
    changeset: dict[str, Any],
    *,
    max_repo_files: int | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Reconcile contents when notifications lack a trustworthy freshness receipt.

    Session changesets currently derive from metadata, without watcher sequence or
    overflow guarantees. Treat them as hints, never as authority to skip content
    checks. Whole-file products still reuse unchanged content across refreshes.
    """
    return _repo_map.build_repo_map(
        str(previous_map.get("path", ".")),
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
    )


def build_repo_map_json(
    path: str | _repo_map.Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
) -> str:
    payload = _repo_map.build_repo_map(path, max_repo_files=max_repo_files)
    return json.dumps(
        _repo_map.apply_repo_map_output_limits(payload, max_files=max_files), indent=2
    )


def _enclosing_class_for_definition(
    definition: dict[str, Any],
    all_symbols: list[dict[str, Any]],
) -> str | None:
    """Return the name of the innermost class that contains *definition*, or None.

    Looks through *all_symbols* for class-kind entries in the same file whose
    ``start_line``/``end_line`` span contains the definition's line.  When
    multiple classes nest (rare but possible), the one with the largest
    ``start_line`` (i.e. innermost) is returned.
    """
    def_file = str(definition.get("file", ""))
    def_line = int(definition.get("line", 0) or 0)
    best: dict[str, Any] | None = None
    for sym in all_symbols:
        if str(sym.get("kind", "")) != "class":
            continue
        if str(sym.get("file", "")) != def_file:
            continue
        sym_start = int(sym.get("start_line", sym.get("line", 0)) or 0)
        sym_end = int(sym.get("end_line", sym_start) or sym_start)
        if sym_start <= def_line <= sym_end:
            if best is None or sym_start > int(best.get("start_line", best.get("line", 0)) or 0):
                best = sym
    return str(best["name"]) if best is not None else None


def _definition_confidence_score(definition: dict[str, Any], symbol: str) -> float:
    """Return a [0.0, 1.0] confidence score for a definition entry.

    All definitions returned by ``build_symbol_defs_from_map`` already pass an
    exact-name filter, so this function starts at 1.0 and applies small
    downward adjustments for signals that indicate lower fidelity:

    * LSP-proof entries get a slight boost (capped at 1.0) â€” they have
      cross-validated provenance.
    * Heuristic / regex-backed provenance gets a small penalty.
    * The definition is in a test file (path contains "test") â€” mild penalty,
      as a matching symbol in test code is less likely to be the canonical def.
    """
    score = 1.0
    provenance = str(definition.get("provenance", "")).lower()
    if "lsp" in provenance and "fallback" not in provenance:
        score = min(1.0, score + 0.05)
    if "heuristic" in provenance or provenance == "regex-heuristic":
        score -= 0.1
    file_str = str(definition.get("file", "")).lower().replace("\\", "/")
    if "/test" in file_str or file_str.startswith("test"):
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 3)


def build_symbol_defs(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_tests: int | None = None,
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    # CLI consistency fix : `--deadline` used to be undefined on `tg defs`
    # (Click "No such option" exit-2), unlike its true siblings build_symbol_refs/_callers/_impact.
    # build_repo_map already accepts deadline_monotonic, so this is the same thin thread-through
    # those wrappers use -- `_copy_partial_signal` below guarantees the signal reaches the top-level
    # output even though `build_symbol_defs_from_map`'s `dict(repo_map)` copy already carries it.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    payload = _repo_map.build_repo_map(
        path, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic
    )
    # audit C1/C2: this call used to be BARE, dropping the deadline entirely --
    # build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol scan (repo_map.py:3812)
    # already accepts deadline_monotonic (#203), it just never received one from here, so a `tg
    # defs --deadline N` request ran that stage-1 scan fully unbounded (dogfood: `--deadline 40`
    # -> 113.5s, exit 0, partial:null). main.py's daemon gate skips the warm fast path whenever
    # --deadline is set, so THIS cold wrapper is the only path an explicit-deadline caller exercises.
    result = _repo_map.build_symbol_defs_from_map(
        payload,
        symbol,
        semantic_provider=semantic_provider,
        max_tests=max_tests,
        deadline_monotonic=deadline_monotonic,
    )
    _repo_map._copy_partial_signal(result, payload)
    # C1 defense-in-depth: mirrors build_context_pack's #642-style return-time backstop
    # (repo_map.py:8380-8392) -- a final absolute wall-clock recheck so ANY stage inside
    # build_symbol_defs_from_map (instrumented or not) can never silently return exit 0 once the
    # caller's --deadline budget is gone, regardless of which internal loop actually consumed the
    # time. Does NOT set `partial_reason` -- that field is reserved for the
    # agent/context/context-render/edit-plan family (docs/CONTRACTS.md); the symbol commands'
    # existing convention (impact/refs/callers/blast-radius fold-ins above) is partial +
    # deadline_limit only.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic
    )
    _repo_map._stamp_partial_status(
        result, emit_reason=False, deadline_exceeded=deadline_exceeded_at_return
    )
    return result


def build_symbol_defs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    max_tests: int | None = None,
    deadline_monotonic: float | None = None,
    include_tests: bool = True,
) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["routing_reason"] = "symbol-defs"
    _repo_map._copy_partial_signal(payload, repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(current) for current in repo_map.get("symbols", [])]
    payload["imports"] = [dict(current) for current in repo_map.get("imports", [])]
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    native_definitions = [
        {
            **dict(current),
            "provenance": _repo_map._symbol_navigation_provenance_for_path(str(current["file"])),
        }
        for current in payload["symbols"]
        if str(current["name"]) == symbol
    ]
    native_definitions.sort(
        key=lambda item: (
            str(item["file"]),
            int(item["line"]),
            str(item["kind"]),
            str(item["name"]),
        )
    )
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    definitions = [dict(current) for current in native_definitions]
    external_definitions: list[dict[str, Any]] = []
    fallback_used = False
    if normalized_provider != "native":
        external_definitions = _repo_map._external_definitions(
            _repo_map._repo_map_root_dir(repo_map),
            symbol,
            native_definitions,
            repo_map=repo_map,
        )
        if normalized_provider == "lsp":
            proof_definitions = [
                dict(current)
                for current in external_definitions
                if _repo_map._is_lsp_proof_row(current)
            ]
            fallback_used = not bool(proof_definitions)
            definitions = proof_definitions or definitions
        else:
            definitions = _repo_map._dedupe_definition_rows([*external_definitions, *definitions])

    # L3: Enrich each definition with `class` (enclosing class name or null)
    # and `score` (confidence signal).  These are additive fields â€” existing
    # keys are not renamed or removed.
    all_symbols_for_class_lookup = list(repo_map.get("symbols", []))
    for definition in definitions:
        if "class" not in definition:
            definition["class"] = _repo_map._enclosing_class_for_definition(
                definition, all_symbols_for_class_lookup
            )
        if "score" not in definition:
            definition["score"] = _repo_map._definition_confidence_score(definition, symbol)

    definition_files = [str(current["file"]) for current in definitions]
    # Root-cause fix (audit #96): defs used to shallow-copy the WHOLE-REPO test manifest into
    # `tests` (every _is_test_file up to the repo-map scan cap), regardless of relevance -- the
    # "69KB for a 1-symbol answer" bug. Route through the SAME relevance filter callers/impact
    # already use so defs stops dumping the manifest, then cap it BEFORE `related_paths` derives
    # below (a leak-back through the second field is the same bug one layer down).
    # task #203: thread the (now-optional) deadline into this sibling test-relevance scan --
    # _relevant_tests_for_symbol already supports deadline_monotonic/deadline_hit (used by
    # impact/callers' own fold-in), but defs never passed either, so a warm-daemon `defs` request
    # on a repo with many tests ran this loop fully unbounded even when a caller supplied a
    # deadline. Mirrors build_context_pack_from_map's own_deadline_hit pattern (repo_map.py:13744).
    defs_related_tests_deadline_hit = _repo_map._DeadlineBreakFlag()
    if include_tests:
        payload["tests"] = _repo_map._relevant_tests_for_symbol(
            repo_map,
            symbol,
            definition_files,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=defs_related_tests_deadline_hit,
        )
        _repo_map._apply_symbol_field_output_limit(payload, field_name="tests", max_count=max_tests)
    else:
        payload["tests"] = []
    if defs_related_tests_deadline_hit.hit:
        payload["partial"] = True
        _repo_map._stamp_partial_status(payload, deadline_exceeded=True, emit_reason=False)
    related_paths = []
    for current in [*definition_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-defs"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["files"] = sorted(dict.fromkeys(definition_files))
    definition_file_set = set(payload["files"])
    compact_symbols = [
        current
        for current in payload["symbols"]
        if str(current.get("name", "")) == symbol
        and (not definition_file_set or str(current.get("file", "")) in definition_file_set)
    ]
    if not compact_symbols and definitions:
        compact_symbols = [dict(current) for current in definitions]
    payload["symbols"] = _repo_map._dedupe_symbol_records(compact_symbols)
    payload["imports"] = [
        current
        for current in payload["imports"]
        if str(current.get("file", "")) in definition_file_set
    ]
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "strong"
    payload["semantic_provider"] = normalized_provider
    for definition in definitions:
        if _repo_map._is_lsp_proof_row(definition):
            definition["lsp_proof"] = True
    lsp_proof_count = _repo_map._lsp_proof_row_count(definitions)
    if normalized_provider != "native" and lsp_proof_count == 0 and native_definitions:
        fallback_used = True
    payload["provider_agreement"] = _repo_map._merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=len(native_definitions),
        lsp_count=lsp_proof_count,
        merged_count=len(definitions),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _repo_map._provider_status_snapshot(
        _repo_map._repo_map_root_dir(repo_map),
        semantic_provider=normalized_provider,
        languages=_repo_map._provider_languages_for_symbol(repo_map, symbol, definitions),
        fallback_used=fallback_used,
    )
    _repo_map._attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    coverage_gap_deadline_hit = _repo_map._DeadlineBreakFlag()
    if not definitions:
        payload["no_match"] = True
        payload["message"] = f"No exact definition found for symbol {symbol!r}."
        scan_limit = payload.get("scan_limit")
        if isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated"):
            payload["message"] += (
                f" The broad scan stopped after {scan_limit.get('scanned_files')} files; "
                "narrow PATH or raise --max-repo-files."
            )
            _repo_map._mark_result_incomplete(
                payload, remediation=_repo_map._SCAN_LIMIT_TRUNCATED_REMEDIATION
            )
        elif _repo_map._scan_did_not_finish(payload):
            # Task 332: the DEADLINE arm of the same fact. This branch used to not exist, so a
            # deadline-truncated defs lookup emitted the BARE sentence above -- a flat "No exact
            # definition found" over a scan that read 0 files -- and skipped
            # `_mark_result_incomplete` entirely, because both were gated on the file cap alone.
            # The count arm above stays first and unchanged: when the cap IS the binding
            # constraint its message names the right knob, and a shared branch would have to
            # pick one knob for both causes.
            payload["message"] += (
                " The scan stopped at the --deadline before finishing; "
                "raise --deadline or narrow PATH."
            )
            _repo_map._mark_result_incomplete(
                payload, remediation=_repo_map._DEADLINE_TRUNCATED_REMEDIATION
            )
        # F13 fix: unlike refs/callers (which compute `resolution_gaps` over the files they
        # actually scan), defs' own no_match path used to return bare -- indistinguishable from
        # "the symbol genuinely does not exist" even when the real cause is a grammar-missing
        # fail-closed language (e.g. a Go-only symbol with tree_sitter_go not installed). Attach
        # the same honesty-floor gap here so a defs-only caller gets the hint too.
        gap_files, gap_tests = _repo_map._repo_map_file_and_test_universe(repo_map)
        resolution_gaps = _repo_map._language_coverage_gaps_for_universe(
            [*gap_files, *gap_tests],
            _repo_map._repo_map_root_dir(repo_map),
            deadline_monotonic=deadline_monotonic,
            deadline_hit=coverage_gap_deadline_hit,
        )
        payload["resolution_gaps"] = resolution_gaps
        _repo_map.apply_coverage_gap_incompleteness(payload, resolution_gaps, answer_empty=True)
        if resolution_gaps:
            gap_hint = "; ".join(
                f"{int(gap['files_affected'])} {gap['language']} file(s): {gap['remediation']}"
                for gap in resolution_gaps
            )
            payload["message"] += f" Coverage gap detected: {gap_hint}"
        # Bounded near-misses only: this payload also reaches MCP/LSP/context consumers.
        from tensor_grep.cli.symbol_suggestions import bounded_candidates

        payload["candidate_symbols"] = bounded_candidates(symbol, repo_map.get("symbols", []))
        payload["files"] = []
        payload["symbols"] = []
        payload["imports"] = []
        payload["tests"] = []
        payload["related_paths"] = []
        payload["graph_completeness"] = "empty"
    else:
        _repo_map.attach_found_answer_gaps(
            payload,
            repo_map,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=coverage_gap_deadline_hit,
        )
    if coverage_gap_deadline_hit.hit:
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        _repo_map._stamp_partial_status(payload, deadline_exceeded=True, emit_reason=False)
    return payload


def build_symbol_defs_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_defs(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )


def build_symbol_source(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg source` (Click "No such
    # option" exit-2) even though its true sibling build_symbol_defs already had it -- same thin,
    # additive thread-through build_repo_map already accepts (mirrors #581's build_symbol_defs).
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # audit C1/C2: thread the same deadline into the _from_map core -- previously
    # dropped here even though build_symbol_source already computed it (dogfood: `tg source
    # --deadline` overran by 47.0s). build_symbol_source_from_map gains deadline_monotonic below
    # (site 6); this cold wrapper is site 7.
    result = _repo_map.build_symbol_source_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    _repo_map._copy_partial_signal(result, repo_map)
    return result


def build_symbol_source_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    include_tests: bool = True,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: `deadline_monotonic` is a NEW parameter here (site 6) -- this
    # function previously had no deadline awareness at all, so its bare build_symbol_defs_from_map
    # call below silently ran the stage-1 related-tests scan unbounded regardless of any caller's
    # --deadline. Defaults to None -> byte-identical for every pre-existing call site that does not
    # pass it (mirrors the documented _iter_repo_files convention, repo_map.py:993-998).
    # `_copy_partial_signal(payload, defs_payload)` below already folds defs_payload's partial
    # signal into this function's own fresh envelope, so threading the deadline is the whole fix.
    defs_payload = _repo_map.build_symbol_defs_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        include_tests=include_tests,
    )
    default_agreement, default_status = _repo_map._default_provider_metadata(
        _repo_map._repo_map_root_dir(repo_map),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    sources: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    with _repo_map._profiling_phase(_profiling_collector, "source_extraction"):
        for definition in defs_payload["definitions"]:
            current_path = _repo_map.Path(str(definition["file"]))
            if str(current_path) in seen_files:
                continue
            seen_files.add(str(current_path))
            current_sources = _repo_map._python_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() in _repo_map._JS_TS_SUFFIXES:
                current_sources = _repo_map._js_ts_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() in _repo_map._RUST_SUFFIXES:
                current_sources = _repo_map._rust_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() == ".go":
                current_sources = _repo_map.lang_go.go_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() in _repo_map._JAVA_SUFFIXES:
                current_sources = _repo_map._java_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() == ".php":
                current_sources = _repo_map.lang_php.php_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() == ".cs":
                current_sources = _repo_map.lang_csharp.csharp_parser_symbol_sources(
                    current_path, symbol
                )
            if not current_sources and current_path.suffix.lower() == ".c":
                current_sources = _repo_map.lang_c.c_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix.lower() in _repo_map._CPP_SUFFIXES:
                current_sources = _repo_map.lang_cpp.cpp_parser_symbol_sources(current_path, symbol)
            if not current_sources:
                current_sources = _repo_map._regex_symbol_sources(current_path, symbol)
            sources.extend(current_sources)

    related_paths: list[str] = []
    for current in [*defs_payload["files"], *defs_payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _repo_map._envelope(_repo_map.Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-source"
    payload["symbol"] = symbol
    payload["definitions"] = defs_payload["definitions"]
    payload["files"] = defs_payload["files"]
    payload["symbols"] = defs_payload["symbols"]
    payload["imports"] = defs_payload["imports"]
    payload["tests"] = defs_payload["tests"]
    payload["related_paths"] = related_paths
    payload["sources"] = sources
    if defs_payload.get("no_match"):
        payload["no_match"] = True
        payload["message"] = str(defs_payload.get("message", "No exact definition found."))
    payload["semantic_provider"] = _repo_map._normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    if "candidate_symbols" in defs_payload:
        payload["candidate_symbols"] = defs_payload["candidate_symbols"]
    _repo_map._copy_lsp_evidence_status(payload, defs_payload)
    _repo_map._copy_scan_limit(payload, defs_payload)
    _repo_map._copy_partial_signal(payload, defs_payload)
    _repo_map.copy_coverage_gap_state(payload, defs_payload)
    _repo_map.apply_answer_gaps(payload, "source")
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_symbol_source_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_source(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def build_symbol_impact(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # capability P0-6 step 3: convert the relative --deadline once to an ABSOLUTE monotonic timestamp so
    # the underlying repo scan can bound itself and return partial results.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    payload = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = _repo_map.build_symbol_impact_from_map(
        payload,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
        _profiling_collector=_profiling_collector,
    )
    _repo_map._copy_partial_signal(
        result, payload
    )  # guarantee the deadline signal reaches the top-level output
    return result


def build_symbol_impact_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg impact
    # --deadline 1` -> 85.4s). `_copy_partial_signal(payload, defs_payload)` below already folds
    # defs_payload's partial signal into this function's own return, so threading the deadline
    # here is the whole fix at this site.
    defs_payload = _repo_map.build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    default_agreement, default_status = _repo_map._default_provider_metadata(
        _repo_map._repo_map_root_dir(repo_map),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    if defs_payload.get("no_match"):
        payload = dict(defs_payload)
        payload["routing_reason"] = "symbol-impact"
        payload["preferred_command"] = "blast-radius"
        payload["preferred_command_reason"] = (
            "impact is a fast file-level planning signal; "
            "blast-radius adds caller_tree, blast_radius_score, and call-graph depth "
            "for precise change-impact analysis â€” use blast-radius when you need "
            "caller attribution or a scored propagation graph"
        )
        payload["trust_level"] = "planning-signal"
        payload["file_matches"] = []
        payload["file_summaries"] = []
        payload["test_matches"] = []
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _repo_map._coverage_summary(payload)
        payload["provider_agreement"] = dict(default_agreement)
        payload["provider_status"] = dict(default_status)
        _repo_map._copy_lsp_evidence_status(payload, defs_payload)
        return _repo_map._attach_profiling(payload, _profiling_collector)
    # task #103 Fix 2: thread the shared deadline into context-pack's own symbol-scoring loop too
    # -- previously unbounded even when this same deadline_monotonic already gated the sibling
    # scans just below (the #52 fix (loop C) comment).
    context_pack_deadline_hit = _repo_map._DeadlineBreakFlag()
    context_pack_test_scan_counts = _repo_map._TestScanCounts()
    context_payload = _repo_map.build_context_pack_from_map(
        repo_map,
        symbol,
        # opt10 #3: `_test_source_limit` is DELIBERATELY NOT APPLIED HERE, unlike refs/callers.
        # The original justification -- "impact keys its by-path lookup off `related_tests`, a
        # SEPARATE independently-computed list, so a tighter source list cannot change any output
        # impact actually returns" -- is FALSE, and was falsified by measurement (adversarial gate
        # on #904, reproduced independently with a same-ceiling control proving determinism):
        #
        #   unbounded  score=23  reasons=[path, filename, test-graph, graph-centrality]  conf=strong
        #   ceiling=1  score= 2  reasons=[path]                                          conf=weak
        #
        # `related_tests` selects WHICH rows appear; it does not make their VALUES independent.
        # `test_matches_by_path` is built FROM `context_payload["test_matches"]` just below and
        # supplies the score/reasons/provenance/association that this payload RETURNS -- so the
        # bound silently downgraded `association.confidence` on any repo past the ceiling.
        # The same probe shows refs and callers are genuinely unaffected (they really do read only
        # `test_matches[:1]`), which is why they keep the limit and this call site does not.
        # Impact stays bounded by the --deadline gate, which is honest: it stamps `partial`.
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
        _test_scan_counts=context_pack_test_scan_counts,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop C): build_symbol_impact_from_map previously threaded NO deadline at all into
    # either of its two sibling scans below, even when a caller (build_symbol_impact,
    # build_symbol_blast_radius_from_map) already had one in scope -- both loops mirror the
    # equivalent callers-scan seams (task #61) which already guard the same helpers.
    preferred_definition_deadline_hit = _repo_map._DeadlineBreakFlag()
    preferred_definition_files = _repo_map._preferred_definition_files(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=preferred_definition_deadline_hit,
    )
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    unselected_definition_files = {
        str(current)
        for current in defs_payload["files"]
        if str(current) not in set(definition_files)
    }

    impacted_files: list[str] = []
    import_files = [str(entry["file"]) for entry in context_payload["imports"]]
    for current in [*definition_files, *context_payload["files"], *import_files]:
        if current in unselected_definition_files:
            continue
        if current not in impacted_files:
            impacted_files.append(current)

    related_tests_deadline_hit = _repo_map._DeadlineBreakFlag()
    related_graph_uncertainty: dict[str, dict[str, Any]] = {}
    related_tests = _repo_map._relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        fallback_tests=list(context_payload.get("tests", [])),
        deadline_monotonic=deadline_monotonic,
        deadline_hit=related_tests_deadline_hit,
        _test_scan_counts=context_pack_test_scan_counts,
        _profiling_collector=_profiling_collector,
        _graph_uncertainty=related_graph_uncertainty,
    )
    # impact's own DEDICATED --max-tests (design #96 item 2): cap BEFORE related_paths/test_matches
    # derive below (both are indexed off `related_tests`), or the omitted tests leak back in via
    # the second field. `payload` does not exist yet at this point, so cap through a scratch dict.
    impact_tests_limit_scratch: dict[str, Any] = {"tests": related_tests}
    _repo_map._apply_symbol_field_output_limit(
        impact_tests_limit_scratch, field_name="tests", max_count=max_tests
    )
    related_tests = cast(list[str], impact_tests_limit_scratch["tests"])
    impact_tests_output_limit = impact_tests_limit_scratch.get("output_limit")

    definition_file_set = set(definition_files)
    file_matches_by_path: dict[str, dict[str, Any]] = {}
    for item in context_payload.get("file_matches", []):
        item_path = str(item["path"])
        item_reasons = list(item["reasons"])
        if item_path not in definition_file_set:
            item_reasons = [
                reason for reason in item_reasons if reason not in {"definition", "symbol"}
            ]
        file_matches_by_path[item_path] = {
            "path": item_path,
            "score": int(item["score"]),
            "reasons": item_reasons,
            "provenance": _repo_map._provenance_from_reasons(item_reasons),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
        }
    for current in definition_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "definition" not in entry["reasons"]:
            entry["reasons"].append("definition")
    for current in import_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "import" not in entry["reasons"]:
            entry["reasons"].append("import")

    test_matches_by_path: dict[str, dict[str, Any]] = {
        str(item["path"]): {
            "path": str(item["path"]),
            "score": int(item["score"]),
            "reasons": list(item["reasons"]),
            "provenance": list(item.get("provenance", [])),
            **({"association": dict(item["association"])} if "association" in item else {}),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
        }
        for item in context_payload.get("test_matches", [])
    }
    for current in related_tests:
        test_matches_by_path.setdefault(
            str(current),
            {
                "path": str(current),
                "score": 1,
                "reasons": ["test-graph"],
                "provenance": _repo_map._provenance_from_reasons(["test-graph"]),
                "association": {
                    "edge_kind": "import-graph",
                    "confidence": "moderate",
                    "provenance": _repo_map._provenance_from_reasons(["test-graph"]),
                },
            },
        )

    related_paths: list[str] = []
    for current in [*impacted_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _repo_map._envelope(_repo_map.Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-impact"
    payload["symbol"] = symbol
    payload["preferred_command"] = "blast-radius"
    payload["preferred_command_reason"] = (
        "impact is a fast file-level planning signal; "
        "blast-radius adds caller_tree, blast_radius_score, and call-graph depth "
        "for precise change-impact analysis â€” use blast-radius when you need "
        "caller attribution or a scored propagation graph"
    )
    payload["trust_level"] = "planning-signal"
    payload["definitions"] = definitions
    payload["files"] = impacted_files
    payload["file_matches"] = [file_matches_by_path[str(current)] for current in impacted_files]
    payload["file_summaries"] = _repo_map._file_summaries(
        repo_map.get("symbols", []), impacted_files
    )
    payload["tests"] = related_tests
    payload["test_matches"] = [test_matches_by_path[str(current)] for current in related_tests]
    if impact_tests_output_limit is not None:
        payload["output_limit"] = impact_tests_output_limit
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _repo_map._ranking_quality(
        payload["file_matches"], payload["test_matches"]
    )
    payload["coverage_summary"] = _repo_map._coverage_summary(payload)
    payload["semantic_provider"] = _repo_map._normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    _repo_map._copy_lsp_evidence_status(payload, defs_payload)
    # #52 fix (loop C): fold THIS function's own sibling-loop deadline signals (preferred-
    # definition scoring + related-test matching, both unbounded before this fix) into partial --
    # mirrors the callers/blast-radius fold-in pattern (task #61) so a --deadline-truncated impact
    # result is never silently reported as complete.
    # task #103 Fix 2: context_pack_deadline_hit joins the same fold-in -- the context-pack
    # symbol-scoring loop is a THIRD sibling scan sharing this deadline_monotonic.
    if (
        preferred_definition_deadline_hit.hit
        or related_tests_deadline_hit.hit
        or context_pack_deadline_hit.hit
    ):
        payload["partial"] = True
        payload["deadline_limit"] = {"deadline_exceeded": True}
        # opt10 #3: attribute WHICH stage ran out of budget. `deadline_exceeded` is a SHARED
        # boolean any of this module's time.monotonic readers can set, so it cannot name the
        # stage. Additive, and only when _context_tests actually ran -- mirrors the sibling
        # scanned/total pairs already in this payload. See docs/CONTRACTS.md.
        if context_pack_test_scan_counts.total:
            payload["deadline_limit"]["test_candidates_scanned"] = (
                context_pack_test_scan_counts.scanned
            )
            payload["deadline_limit"]["test_candidates_total"] = context_pack_test_scan_counts.total
    _repo_map._copy_scan_limit(payload, defs_payload)
    _repo_map._copy_partial_signal(payload, defs_payload)
    _repo_map.copy_coverage_gap_state(payload, defs_payload)
    impact_uncertain_paths = _repo_map_graph.uncertain_paths(
        context_payload.get("file_matches", []), related_graph_uncertainty
    )
    _repo_map_graph.add_uncertainty(
        _repo_map, payload, impact_uncertain_paths, _repo_map._repo_map_root_dir(repo_map)
    )
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_symbol_impact_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_impact(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )
