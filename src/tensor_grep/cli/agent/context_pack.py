from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_graph as _repo_map_graph
from tensor_grep.cli import repo_map_ranking as _repo_map_ranking


@lru_cache(maxsize=16384)
def _module_aliases_for_path(path: str) -> frozenset[str]:
    # Pure function of the path STRING (no file I/O) â€” safe to cache unconditionally, no mtime
    # key needed. The reverse-import graph / PageRank calls this in tight loops (~1.4M calls for
    # ~unique-file inputs on a depth-2 blast-radius), so memoization collapses it to one build
    # per distinct path. frozenset return keeps the cached value immutable (all callers iterate
    # it or .update() FROM it; none mutate it).
    #
    # DELIBERATELY does NOT emit a directory-index file's PARENT-DIR alias -- that lives in the
    # reverse-importers-ONLY `_reverse_importer_extra_aliases` (below). This helper is SHARED by
    # substring/exact RANKING (`_import_graph_bonus`, `_test_import_bonus`, `_test_graph_score`)
    # and by BLAST-RADIUS scope expansion (set-intersection ~:11542) + the test-coverage gate
    # (substring match ~:16732). Giving a top-level `pkg/__init__.py` a bare "pkg" alias HERE
    # would make editing that (often empty) init pull most of the repo into `tg blast-radius`
    # and mark nearly every test covered (adversarial-review finding, 2026-07-16). Keep it
    # byte-stable for those consumers; widen only the reverse-importers prefilter.
    current = _repo_map.Path(path)
    aliases = {current.stem.lower()}
    parts = [part.lower() for part in current.with_suffix("").parts]
    if parts:
        aliases.add(".".join(parts))
    if len(parts) > 1:
        aliases.add(".".join(parts[-2:]))
    return frozenset(alias for alias in aliases if alias)


@lru_cache(maxsize=16384)
def _reverse_importer_extra_aliases(path: str) -> frozenset[str]:
    """Extra alias a DIRECTORY-INDEX file earns ONLY for the reverse-importers candidate
    prefilter (`_reverse_importers`) -- deliberately NOT folded into the SHARED
    `_module_aliases_for_path` (see its note for why that helper must stay byte-stable).

    A bare relative specifier that names a DIRECTORY -- `require('./router')` /
    `import ... from './router'` (JS/TS) or `from . import router` where `router` is a subpackage
    (Python) -- resolves, by Node's/Python's own directory-index convention, to
    `router/index.{js,ts,mjs,cjs}` or `router/__init__.py`. That specifier normalizes to the
    PARENT DIRECTORY name ("router"), which `_module_aliases_for_path` never emits (every alias
    it builds is anchored on the file's OWN stem -- "index"/"__init__" -- never its parent dir).
    Without this alias the directory-index importer never enters the coarse prefilter as a
    candidate, so it never reaches the precise per-candidate CONFIRM step
    (`_js_ts_module_matches_definition` / `_python_module_matches_definition`).

    PRECISION -- this alias WIDENS the prefilter; it is NOT a proof of an edge (the CONFIRM step
    still gates every candidate, and every `_reverse_importers` consumer either confirms edges
    (`tg importers`) or feeds PageRank SCORING, never raw membership). For a RELATIVE specifier
    the confirm resolves the exact path, so `./router` -> a real `router/index.js` is an exact
    edge and `./routerX` is rejected. For a BARE (non-relative) specifier the JS/TS confirm falls
    through to `_module_path_matches_definition` (:3214) -- a path-SUFFIX compare that itself
    strips the index/__init__ magic name -- so a bare npm-style `import X from 'react'` WILL
    match a local `src/react/index.ts` at the pre-existing 0.2 "partial-resolution" confidence.
    That is INTENTIONAL (correct in pnpm/yarn workspace monorepos where `react` is a real local
    package dir; a deliberate false-positive only for the rare npm-package-name-vs-local-dir
    collision) and is exactly the existing bare-suffix heuristic this alias feeds -- NOT a new
    exactness claim. Pinned by
    test_build_file_importers_bare_specifier_matches_local_directory_index_package.
    """
    current = _repo_map.Path(path)
    if current.stem.lower() not in _repo_map._DIRECTORY_INDEX_STEMS:
        return frozenset()
    parts = [part.lower() for part in current.with_suffix("").parts]
    if len(parts) < 2:
        return frozenset()
    parent = parts[-2]
    return frozenset({parent}) if parent else frozenset()


def _import_alias_candidates(import_name: str) -> set[str]:
    lowered = import_name.lower().strip()
    if not lowered:
        return set()
    normalized = re.sub(r"[^a-z0-9_]+", ".", lowered).strip(".")
    parts = [part for part in normalized.split(".") if part]
    candidates = {lowered, normalized}
    candidates.update(parts)
    for start in range(len(parts)):
        for end in range(start + 2, len(parts) + 1):
            candidates.add(".".join(parts[start:end]))
    return {candidate for candidate in candidates if candidate}


def _import_graph_bonus(
    file_path: str,
    dependency_aliases: dict[str, frozenset[str]],
    imports_by_file: dict[str, list[str]],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(file_path, []):
        lowered = import_name.lower()
        for aliases in dependency_aliases.values():
            if any(alias and alias in lowered for alias in aliases):
                bonus += 4
                break
    return bonus


def _reverse_import_distances(
    seed_files: list[str],
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
    *,
    repo_root: _repo_map.Path | str | None = None,
    trusted_seed_files: set[str] | None = None,
    uncertain_candidates: dict[str, dict[str, Any]] | None = None,
    candidate_statuses: dict[str, dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, int]:
    return _repo_map_graph.reverse_import_distances(
        _repo_map,
        seed_files,
        all_files,
        imports_by_file,
        repo_root=repo_root,
        trusted_seed_files=trusted_seed_files,
        uncertain_candidates=uncertain_candidates,
        candidate_statuses=candidate_statuses,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
        _profiling_collector=_profiling_collector,
    )


def _reverse_importers(
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
    *,
    include_directory_index_aliases: bool = False,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, set[str]]:
    # `include_directory_index_aliases` is opt-in and defaults OFF so this function stays
    # byte-behaviour-identical to origin/main for its RANKING consumers -- the blast-radius
    # (:16587) and context/agent-capsule (:3836) callers feed its output into
    # `_personalized_reverse_import_pagerank` (a SCORING signal), and even a widened reverse edge
    # there measurably reorders pinned `dependent_files` output
    # (test_python_termui_symbols_prefer_depth_one_dependents). ONLY the `tg importers`
    # reverse-resolution (`build_file_importers_from_map`) passes True, because ONLY it runs the
    # per-candidate CONFIRM step (`_confirm_import_edges`) that turns the widened prefilter back
    # into exact edges. See `_reverse_importer_extra_aliases` for the alias rationale.
    #
    # #222 residual fix: this function's own direct cost is comparatively cheap and scales close
    # to linearly (measured ~0.02s/0.08s/0.15s at 2k/6k/12k files on the same synthetic tree that
    # exposed ``_reverse_import_distances``' quadratic cost above) -- NOT the dominant residual --
    # but it sits in the exact same unconditional, un-gated call block in `_build_context_pack_
    # from_map` immediately after that function. Without a check here, a deadline tripped INSIDE
    # `_reverse_import_distances` would still let this whole second whole-repo pass run
    # unbounded afterward. Same per-item-in-the-expensive-loop shape as every sibling; optional,
    # default `None`, byte-identical no-op for the 3 other pre-existing call sites.
    with _repo_map._profiling_phase(_profiling_collector, "graph_construction"):
        alias_to_files: dict[str, set[str]] = {}
        for current in all_files:
            for alias in _repo_map._module_aliases_for_path(current):
                alias_to_files.setdefault(alias, set()).add(current)
            if include_directory_index_aliases:
                # Directory-index recall (express@4.21.1 dogfood): also nominate a directory's
                # `index.*`/`__init__.py` under its PARENT-DIR alias, so a bare relative specifier
                # (`require('./router')` / `from . import router`) reaches the CONFIRM step.
                for alias in _repo_map._reverse_importer_extra_aliases(current):
                    alias_to_files.setdefault(alias, set()).add(current)
        reverse: dict[str, set[str]] = {current: set() for current in all_files}
        for importer in all_files:
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return reverse
            for import_name in imports_by_file.get(importer, []):
                for alias in _repo_map._import_alias_candidates(import_name):
                    for current in alias_to_files.get(alias, set()):
                        if current == importer:
                            continue
                        reverse[current].add(importer)
        return reverse


def _personalized_reverse_import_pagerank(
    seed_files: list[str],
    all_files: list[str],
    reverse_importers: dict[str, set[str]],
    *,
    alpha: float = 0.85,
    iterations: int = 12,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, float]:
    """dogfood finding 1: this 12-iteration loop ran fully UNBOUNDED even when a caller already
    had a deadline_monotonic in scope (a real ``tg agent``/``tg codemap`` whole-repo call could
    run well past --deadline here alone). ``deadline_monotonic``/``deadline_hit`` follow the same
    ``_DeadlineBreakFlag`` readback contract every other deadline-scoped sibling loop in this
    module uses -- checked at the ITERATION boundary (not mid-iteration) so a hit is always a
    clean iteration count, never a half-applied update.

    On expiry this ABANDONS to ``{}`` rather than returning the last-completed iteration's
    partial ranks: every existing caller already treats a missing/zero graph score as "no
    centrality signal" via ``.get(x, 0.0)`` (see ``_build_context_pack_from_map`` and
    ``_relevant_tests_for_symbol``), so ``{}`` is a deterministic, already-handled degrade --
    never a silently-incomplete ranking presented as complete.

    The per-node ``sorted(reverse_importers.get(current))`` is hoisted OUT of the iteration loop
    below: ``reverse_importers`` never changes across iterations, so the pre-fix code recomputed
    the same sort up to 12x per node for nothing. Free, additive speedup, independent of the
    deadline fix above -- proven numerically identical to the pre-hoist shape by
    ``test_pagerank_hoisted_sort_matches_reference_computation``.
    """
    with _repo_map._profiling_phase(_profiling_collector, "graph_pagerank"):
        if not seed_files:
            return {}

        all_file_set = set(all_files)
        seen_seeds: set[str] = set()
        unique_seeds: list[str] = []
        for current in seed_files:
            if current not in all_file_set or current in seen_seeds:
                continue
            seen_seeds.add(current)
            unique_seeds.append(current)
            if len(unique_seeds) >= _repo_map._GRAPH_PAGERANK_SEED_FILE_LIMIT:
                break
        if not unique_seeds:
            return {}

        seed_set = set(unique_seeds)
        seed_weight = 1.0 / len(unique_seeds)
        personalization = {
            current: (seed_weight if current in seed_set else 0.0) for current in all_files
        }
        ranks = dict(personalization)
        sorted_outgoing = {
            current: sorted(reverse_importers.get(current, set())) for current in all_files
        }
        for _ in range(iterations):
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return {}
            updated = {current: (1.0 - alpha) * personalization[current] for current in all_files}
            for current in all_files:
                outgoing = sorted_outgoing[current]
                if outgoing:
                    share = alpha * ranks[current] / len(outgoing)
                    for importer in outgoing:
                        updated[importer] = updated.get(importer, 0.0) + share
                    continue
                spill = alpha * ranks[current] / len(unique_seeds)
                for seed in unique_seeds:
                    updated[seed] = updated.get(seed, 0.0) + spill
            ranks = updated
        return {current: rank for current, rank in ranks.items() if rank > 0.0}


def _test_import_bonus(
    test_path: str,
    source_tokens: set[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
) -> int:
    return _repo_map_graph.test_import_bonus(
        _repo_map, test_path, source_tokens, imports_by_file, file_distances
    )


def _test_graph_score(
    test_path: str,
    source_files: list[str],
    imports_by_file: dict[str, list[str]],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
) -> float:
    return _repo_map_graph.test_graph_score(
        _repo_map, test_path, source_files, imports_by_file, graph_scores, file_scores
    )


def _context_tests(
    source_files: list[str],
    tests: list[str],
    terms: list[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
    *,
    raw_query: str | None = None,
    candidate_statuses: dict[str, dict[str, Any]] | None = None,
    test_graph_targets: list[str] | None = None,
    repo_root: _repo_map.Path | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _test_scan_counts: _repo_map._TestScanCounts | None = None,
) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    allow_unrelated_framework_scan = (
        len(tests) <= _repo_map._FRAMEWORK_TEST_PATTERN_SMALL_TEST_LIMIT
    )
    source_stems = {_repo_map.Path(current).stem.lower() for current in source_files}
    source_tokens = _repo_map._source_tokens(source_files)
    # opt10 #3: attribute WHICH stage ran out of budget -- `partial` alone cannot (see
    # `_TestScanCounts`). `total` is stamped BEFORE the loop so a deadline firing on the first
    # iteration still reports an honest denominator instead of 0/0.
    if _test_scan_counts is not None:
        # `+=`, NOT `=`. A single entry point can reach this function MORE THAN ONCE
        # (`build_symbol_callers_from_map` hits both this call and the one in
        # `_relevant_tests_for_symbol`), and the pair must stay a coherent fraction across all of
        # them. Assigning would let a completed first scan be overwritten by a second, hiding the
        # stop; naively summing `scanned` against a single-scan `total` would report
        # scanned > total. Summing BOTH keeps `scanned <= total` an invariant and still shows
        # `scanned < total` whenever any scan stopped early.
        _test_scan_counts.total += len(tests)
    for current in tests:
        # opt10 #3: this whole-repo `for current in tests:` scan was the one loop feeding
        # _build_context_pack_from_map/_relevant_tests_for_symbol that never checked
        # deadline_monotonic at all -- unbounded even under --deadline. Mirrors the identical
        # per-item idiom every sibling loop in this module already uses (e.g. the #691/#222-fixed
        # loops at repo_map.py:3997-4000, :4028-4031, and _build_context_pack_from_map's own
        # symbol-scoring loop at :8218-8221) -- checked at the iteration boundary, folds into the
        # caller's existing partial/deadline_limit signal via the shared _DeadlineBreakFlag. A None
        # deadline_monotonic (the default at both call sites when no --deadline is in effect) short-
        # circuits this check, so the loop runs exactly as before: a byte-identical no-op.
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        if _test_scan_counts is not None:
            _test_scan_counts.scanned += 1
        score = _repo_map._score_file_path(current, terms)
        reasons: list[str] = []
        if score > 0:
            reasons.append("path")
        stem = _repo_map.Path(current).stem.lower().removeprefix("test_")
        if stem in source_stems:
            score += 2
            reasons.append("filename")
        graph_targets = test_graph_targets if test_graph_targets is not None else source_files
        root = repo_root or _repo_map._infer_project_root(
            _repo_map.Path(graph_targets[0]) if graph_targets else _repo_map.Path.cwd()
        )
        import_bonus, confirmed_targets, confirmed_imports = _repo_map_graph.scored_test_imports(
            _repo_map,
            current,
            graph_targets[: _repo_map._GRAPH_PAGERANK_SEED_FILE_LIMIT],
            root,
            imports_by_file.get(current, []),
            source_tokens,
            file_distances,
            candidate_statuses=candidate_statuses or {},
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
        score += import_bonus
        if import_bonus > 0:
            reasons.append("heuristic-import" if not confirmed_targets else "test-graph")
        graph_score = (
            _repo_map._test_graph_score(
                current, confirmed_targets, confirmed_imports, graph_scores, file_scores
            )
            if confirmed_targets
            else 0.0
        )
        framework_bonus = (
            _repo_map._framework_test_pattern_bonus(current, terms, raw_query=raw_query)
            if score > 0 or graph_score > 0.0 or allow_unrelated_framework_scan
            else 0
        )
        score += framework_bonus
        if framework_bonus > 0:
            reasons.append("framework-pattern")
        if graph_score > 0.0:
            reasons.append("graph-centrality")
            score += max(1, round(graph_score * 10))
        if score > 0:
            if not confirmed_targets and import_bonus > 0:
                edge_kind = "heuristic-import"
            elif import_bonus > 0 and stem in source_stems:
                edge_kind = "hybrid"
            elif import_bonus > 0:
                edge_kind = "import-graph"
            elif framework_bonus > 0:
                edge_kind = "framework-pattern"
            elif stem in source_stems:
                edge_kind = "filename"
            else:
                edge_kind = "path"
            if not confirmed_targets and import_bonus > 0:
                confidence = "weak"
            elif import_bonus > 0 or graph_score > 0.0:
                confidence = "strong" if import_bonus > 0 and graph_score > 0.0 else "moderate"
            elif framework_bonus > 0:
                confidence = "moderate" if framework_bonus >= 3 else "weak"
            else:
                confidence = "weak"
            match = _repo_map._match_record(
                current, score, reasons, graph_score if graph_score > 0.0 else None
            )
            match["association"] = {
                "edge_kind": edge_kind,
                "confidence": confidence,
                "provenance": _repo_map._provenance_from_reasons(reasons),
            }
            related.append(match)
    related.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return related


def _build_context_pack_from_map(
    payload: dict[str, Any],
    query: str,
    *,
    auto_deweight: bool = True,
    _test_source_limit: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _test_scan_counts: _repo_map._TestScanCounts | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    with _repo_map._profiling_phase(_profiling_collector, "context_scoring"):
        terms = _repo_map._query_terms(query)
        symbol_terms = _repo_map._symbol_query_terms(query)
        query_language_hints = _repo_map._query_language_hints(query)
        all_symbols = [dict(symbol) for symbol in payload["symbols"]]
        # Task #254 heuristic 2: computed ONCE per scoring pass (query-independent), then reused
        # for every `_score_symbol` call below instead of re-deriving it per symbol.
        non_test_definition_names = _repo_map._non_test_definition_names(payload["symbols"])
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in payload["imports"]
        }
        file_scores = {
            str(current): _repo_map._score_file_path(str(current), terms)
            for current in payload["files"]
        }
        file_reasons: dict[str, list[str]] = {}
        for current, score in file_scores.items():
            if score > 0:
                _repo_map._append_reason(file_reasons, current, "path")
            file_name_bonus = _repo_map._query_file_name_hint_score(current, query)
            if file_name_bonus > 0:
                file_scores[current] = file_scores.get(current, 0) + file_name_bonus
                _repo_map._append_reason(file_reasons, current, "filename")

        scored_symbols: list[dict[str, Any]] = []
        for symbol in payload["symbols"]:
            # task #103: this loop iterates every symbol in the scanned repo -- the single
            # largest repo-size-proportional loop in context-pack construction (profiled at
            # ~13% of a `tg callers`/`tg impact` call) -- and ran fully unbounded regardless of
            # --deadline before this fix. Mirror the same pre-iteration deadline check
            # _preferred_definition_files/_relevant_tests_for_symbol already use.
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            score = _repo_map._score_symbol(
                symbol, symbol_terms, non_test_definition_names=non_test_definition_names
            )
            if score <= 0:
                continue
            scored_symbol = dict(symbol)
            scored_symbol["score"] = score
            current_path = str(scored_symbol["file"])
            if _repo_map._is_test_file(_repo_map.Path(current_path)):
                continue
            symbol_name_score = _repo_map._score_text_terms(
                str(scored_symbol["name"]), symbol_terms
            )
            symbol_name = str(scored_symbol["name"])
            exact_query_match = _repo_map._symbol_name_matches_query_exactly(symbol_name, query)
            bridge_query_match = _repo_map._symbol_name_matches_query_bridge(symbol_name, query)
            covered_query_match = _repo_map._symbol_name_terms_cover_query(
                symbol_name, symbol_terms
            )
            if symbol_name_score > 0 and (
                exact_query_match or bridge_query_match or covered_query_match
            ):
                if exact_query_match:
                    scored_symbol["exact_query_match"] = True
                elif bridge_query_match:
                    scored_symbol["bridge_query_match"] = True
                elif covered_query_match:
                    scored_symbol["covered_query_match"] = True
                _repo_map._append_reason(file_reasons, current_path, "definition")
                _repo_map._append_reason(file_reasons, current_path, "symbol")
            scored_symbols.append(scored_symbol)
        scored_symbols.sort(key=_repo_map._symbol_rank_key)
        for symbol in scored_symbols:
            current = str(symbol["file"])
            exact_symbol_bonus = 36 if bool(symbol.get("exact_query_match")) else 0
            bridge_symbol_bonus = 8 if bool(symbol.get("bridge_query_match")) else 0
            covered_symbol_bonus = 24 if bool(symbol.get("covered_query_match")) else 0
            file_scores[current] = (
                file_scores.get(current, 0)
                + int(symbol["score"]) * 3
                + exact_symbol_bonus
                + bridge_symbol_bonus
                + covered_symbol_bonus
            )

        scored_imports: list[dict[str, Any]] = []
        for entry in payload["imports"]:
            if _repo_map._is_test_file(_repo_map.Path(str(entry["file"]))):
                continue
            score = _repo_map._score_import_entry(entry, terms)
            if score <= 0:
                continue
            scored_entry = dict(entry)
            scored_entry["provenance"] = str(
                entry.get(
                    "provenance",
                    _repo_map._symbol_navigation_provenance_for_path(str(entry["file"])),
                )
            )
            scored_entry["score"] = score
            scored_imports.append(scored_entry)
        scored_imports.sort(key=lambda item: (-int(item["score"]), str(item["file"])))
        for entry in scored_imports:
            current = str(entry["file"])
            file_scores[current] = file_scores.get(current, 0) + int(entry["score"]) * 2
            _repo_map._append_reason(file_reasons, current, "import")

        source_candidates: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in source_candidates:
                source_candidates.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in source_candidates:
                source_candidates.append(current)
        for current, score in sorted(
            file_scores.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        ):
            if score > 0 and current not in source_candidates:
                source_candidates.append(current)
        if not source_candidates:
            source_candidates = [str(current) for current in payload["files"]]
        source_candidates = source_candidates[: _repo_map._SOURCE_FALLBACK_SCAN_LIMIT]
        for current_path in source_candidates:
            source_score = _repo_map._score_file_source_terms(current_path, terms)
            if source_score <= 0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + source_score * 8
            _repo_map._append_reason(file_reasons, current_path, "source")

        dependency_seed_files: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in dependency_seed_files:
                dependency_seed_files.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in dependency_seed_files:
                dependency_seed_files.append(current)
        if not dependency_seed_files:
            dependency_seed_files = [
                path
                for path in payload["files"]
                if _repo_map._score_file_path(str(path), terms) > 0
            ]

        all_files = [str(current) for current in payload["files"]]
        dependency_aliases = {
            current: _repo_map._module_aliases_for_path(current)
            for current in dependency_seed_files
        }
        # #222 (real-workspace-scale residual of #220/#669/#671): these two whole-repo graph
        # passes were the ONE un-gated post-deadline tail consumer left in this function -- every
        # other sibling stage here (the symbol-scoring loop above, pagerank below, both
        # `_detect_vendored_subtrees` calls) already honors `deadline_monotonic`. Reusing the SAME
        # `deadline_hit` flag this function already threads into pagerank keeps `context_pack_
        # assembly` in the caller's `assembly_stages_skipped` list honestly attributed to "some
        # stage in this pack build," not a new, over-precise label this signal alone can't support.
        graph_uncertainty: dict[str, dict[str, Any]] = {}
        graph_candidate_statuses: dict[str, dict[str, Any]] = {}
        trusted_seed_files = {str(current["file"]) for current in scored_symbols}
        file_distances = _repo_map._reverse_import_distances(
            dependency_seed_files,
            all_files,
            imports_by_file,
            repo_root=_repo_map._repo_map_root_dir(payload),
            trusted_seed_files=trusted_seed_files,
            uncertain_candidates=graph_uncertainty,
            candidate_statuses=graph_candidate_statuses,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        reverse_importers = _repo_map._reverse_importers(
            all_files,
            imports_by_file,
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
        for current in payload["files"]:
            # #222 residual fix (found via OLD-vs-NEW re-profile of the fix above): a THIRD
            # unconditional whole-repo loop calling `_import_graph_bonus` directly -- cost is
            # O(len(payload["files"]) x len(dependency_aliases)), and `dependency_aliases` is NOT
            # always small: a query term that fuzzy-matches many files' import strings (e.g. a
            # short/common token) can pull hundreds-to-thousands of files into `dependency_seed_
            # files`. Re-profiled after earlier deadline fixes at 12,000 files: 93s of 102s,
            # 208M `_import_graph_bonus` genexpr evaluations across only 10,006 outer calls. Same
            # per-item check, same shared `deadline_hit` flag as its two siblings above.
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            current_path = str(current)
            if current_path in dependency_seed_files:
                continue
            if graph_candidate_statuses.get(current_path, {}).get("status") == "nonedge":
                continue
            import_graph_bonus = _repo_map._import_graph_bonus(
                current_path,
                dependency_aliases,
                imports_by_file,
            )
            file_scores[current_path] = file_scores.get(current_path, 0) + import_graph_bonus
            if import_graph_bonus > 0:
                _repo_map._append_reason(
                    file_reasons,
                    current_path,
                    "heuristic-import"
                    if graph_candidate_statuses.get(current_path, {}).get("status")
                    not in {"origin", "confirmed"}
                    else "import-graph",
                )
            if current_path in file_distances:
                file_scores[current_path] = file_scores.get(current_path, 0) + max(
                    1, 5 - file_distances[current_path]
                )
                _repo_map._append_reason(file_reasons, current_path, "import-graph")
        for current_path in graph_uncertainty:
            if graph_candidate_statuses.get(current_path, {}).get("status") in {
                "origin",
                "confirmed",
            }:
                continue
            file_scores[current_path] = max(1, file_scores.get(current_path, 0))
            _repo_map._append_reason(file_reasons, current_path, "heuristic-import")
        graph_seed_files: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in graph_seed_files:
                graph_seed_files.append(current)
        if not graph_seed_files:
            graph_seed_files = list(dependency_seed_files)
        weak_graph_targets = [
            path
            for path, status in graph_candidate_statuses.items()
            if status.get("status") == "uncertain"
        ]
        test_graph_targets = list(
            dict.fromkeys([*trusted_seed_files, *file_distances, *weak_graph_targets])
        )[: _repo_map._GRAPH_PAGERANK_SEED_FILE_LIMIT]

        # dogfood finding 1: deadline_monotonic/deadline_hit were already in scope in this
        # function (threaded into the symbol-scoring loop above) but never forwarded to pagerank
        # -- this 12-iteration whole-repo-file loop ran fully unbounded even past --deadline.
        graph_scores = _repo_map._personalized_reverse_import_pagerank(
            graph_seed_files,
            all_files,
            reverse_importers,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        for current_path in trusted_seed_files | set(file_distances):
            graph_score = graph_scores.get(current_path, 0.0)
            if graph_score <= 0.0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + max(
                1, round(graph_score * 10)
            )
            _repo_map._append_reason(file_reasons, current_path, "graph-centrality")

        if query_language_hints:
            for current_path, score in list(file_scores.items()):
                if score <= 0:
                    continue
                if _repo_map._path_matches_query_language_hints(current_path, query_language_hints):
                    file_scores[current_path] = score + 20
                    _repo_map._append_reason(file_reasons, current_path, "language")

        explicit_symbol_intent = any(
            bool(symbol.get("exact_query_match") or symbol.get("bridge_query_match"))
            for symbol in scored_symbols
        )
        if not explicit_symbol_intent:
            with _repo_map._profiling_phase(_profiling_collector, "direct_validation_scoring"):
                for symbol in scored_symbols[: _repo_map._DIRECT_VALIDATION_SYMBOL_SCAN_LIMIT]:
                    current_path = str(symbol["file"])
                    if _repo_map._is_test_file(_repo_map.Path(current_path)):
                        continue
                    direct_test_count = _repo_map._direct_validation_import_count_from_repo_map(
                        tests=[str(test_path) for test_path in payload["tests"]],
                        imports_by_file=imports_by_file,
                        symbol=str(symbol["name"]),
                        definition_path=current_path,
                    )
                    if direct_test_count <= 0:
                        continue
                    file_scores[current_path] = file_scores.get(current_path, 0) + min(
                        64,
                        40 + direct_test_count * 8,
                    )
                    _repo_map._append_reason(
                        file_reasons, current_path, "validation-direct-definition"
                    )

        # Auto de-weight (never hard-exclude) auto-detected vendor/skill/generated CODE subtrees
        # (#55 PR6) -- same import-island heuristic `tg orient` applies, reused here so `tg agent`'s
        # primary-target ranking benefits too. Local import avoids a module-level circular import
        # (orient_capsule imports this module), mirroring the existing `_apply_ignore_globs` reuse
        # a few lines up in `build_context_render`.
        deweighted_trees: dict[str, dict[str, Any]] = {}
        if auto_deweight:
            from tensor_grep.cli.orient_capsule import _DEWEIGHT_FACTOR, _detect_vendored_subtrees

            deweighted_trees = _detect_vendored_subtrees(
                payload, deadline_monotonic=deadline_monotonic, deadline_hit=deadline_hit
            )
            if deweighted_trees:
                tree_roots = list(deweighted_trees.keys())
                for current_path, score in list(file_scores.items()):
                    if score <= 0:
                        continue
                    candidate = _repo_map.Path(current_path)
                    for tree_root in tree_roots:
                        try:
                            candidate.relative_to(tree_root)
                        except ValueError:
                            continue
                        file_scores[current_path] = max(0, round(score * _DEWEIGHT_FACTOR))
                        _repo_map._append_reason(
                            file_reasons, current_path, "vendored-subtree-deweighted"
                        )
                        break

        scored_files = [(score, path) for path, score in file_scores.items() if score > 0]
        scored_files.sort(key=lambda item: (-item[0], item[1]))
        _repo_map_ranking.promote_exact_declaration_files(
            scored_files, scored_symbols, file_reasons
        )
        _repo_map._promote_filename_phrase(
            scored_files,
            scored_symbols,
            payload=payload,
            query=query,
            query_language_hints=query_language_hints,
            deweighted_trees=deweighted_trees,
            file_reasons=file_reasons,
        )
        ranked_files = [path for _, path in scored_files]
        file_matches = [
            _repo_map._match_record(path, score, file_reasons.get(path, []), graph_scores.get(path))
            for score, path in scored_files
        ]
        if not ranked_files:
            for symbol in scored_symbols:
                current = str(symbol["file"])
                if current not in ranked_files:
                    ranked_files.append(current)
            for entry in scored_imports:
                current = str(entry["file"])
                if current not in ranked_files:
                    ranked_files.append(current)
        test_source_files = (
            ranked_files[: max(1, _test_source_limit)]
            if _test_source_limit is not None
            else ranked_files
        )
        test_matches = _repo_map._context_tests(
            test_source_files,
            payload["tests"],
            terms,
            imports_by_file,
            file_distances,
            graph_scores,
            file_scores,
            raw_query=query,
            candidate_statuses=graph_candidate_statuses,
            test_graph_targets=test_graph_targets,
            repo_root=_repo_map._repo_map_root_dir(payload),
            # opt10 #3: thread this function's own already-in-scope deadline through to
            # _context_tests's now-gated loop -- the shared _DeadlineBreakFlag means an early break
            # here folds into the SAME partial signal the symbol-scoring loop above already sets
            # (own_deadline_hit.hit, checked by build_context_pack_from_map's caller below).
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _test_scan_counts=_test_scan_counts,
        )
        ranked_tests = [str(item["path"]) for item in test_matches]

        related_paths = []
        for current in ranked_files:
            related_paths.append(current)
        for current in ranked_tests:
            if current not in related_paths:
                related_paths.append(current)

    payload["routing_reason"] = "context-pack"
    payload["query"] = query
    payload["files"] = ranked_files
    payload["file_matches"] = file_matches
    payload["file_summaries"] = _repo_map._file_summaries(all_symbols, ranked_files)
    payload["symbols"] = scored_symbols
    payload["imports"] = scored_imports
    payload["tests"] = ranked_tests
    payload["test_matches"] = test_matches
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _repo_map._ranking_quality(file_matches, test_matches)
    payload["coverage_summary"] = _repo_map._coverage_summary(payload)
    if graph_uncertainty:
        _repo_map_graph.add_uncertainty(
            _repo_map, payload, list(graph_uncertainty), _repo_map._repo_map_root_dir(payload)
        )
    # Only emit when non-empty. An empty `deweighted_trees` still costs ~20 bytes in every
    # context-pack payload, and (since compaction records dropped keys in omitted_keys) omitting it
    # saves nothing -- enough to tip the macOS llm-compact token-budget test, whose long temp paths
    # leave a ~5-byte margin under the < 9000 ceiling, over the edge (#525 CI). Consumers treat a
    # missing key as "nothing de-weighted".
    if deweighted_trees:
        payload["deweighted_trees"] = [
            {"path": tree_path, "reasons": list(info["reasons"])}
            for tree_path, info in sorted(deweighted_trees.items())
        ]
    return payload


def _estimate_payload_tokens(payload: dict[str, Any]) -> int:
    return _repo_map._estimate_tokens(json.dumps(payload, ensure_ascii=False))


def build_context_pack(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
    max_tokens: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # Match map/agent/blast-radius: when the caller does not bound the scan,
    # default to DEFAULT_AGENT_REPO_MAP_LIMIT so context never walks an entire
    # workspace (which previously made `tg context` hang indefinitely on large
    # trees that lack a --max-repo-files default at the CLI layer).
    if max_repo_files is None:
        max_repo_files = _repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    # CLI consistency fix : `--deadline` used to be undefined on `tg context`
    # (Click "No such option" exit-2). Converted ONCE to an absolute monotonic budget (mirrors
    # build_symbol_impact's capability P0-6 step-3 pattern) and shared across both the repo-map build AND
    # the symbol-scoring loop below, so a slow ranking pass on a huge repo cannot itself blow past
    # the requested budget after the walk/parse phase already finished inside it.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    payload = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    context_payload = _repo_map.build_context_pack_from_map(
        payload,
        query,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    limited = _repo_map.apply_repo_map_output_limits(context_payload, max_files=max_files)
    result = _repo_map._apply_context_token_budget(limited, max_tokens)
    # #642 gate nit-1 fast-follow: mirrors build_context_render_from_map's own return-time
    # catch-all (added by this same PR) verbatim. `tg context` has no edit-plan-seed tail, but
    # still calls apply_repo_map_output_limits/_apply_context_token_budget AFTER build_context_
    # pack_from_map's own own_deadline_hit fold already ran, so it shares the same missing
    # "regardless of stage" recheck at its true final return point.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic
    )
    _repo_map._stamp_partial_status(result, deadline_exceeded=deadline_exceeded_at_return)
    return result


def build_context_pack_json(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
    max_tokens: int | None = None,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_context_pack(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )


def build_context_pack_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    auto_deweight: bool = True,
    _test_source_limit: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _test_scan_counts: _repo_map._TestScanCounts | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = dict(repo_map)
    _repo_map._copy_partial_signal(payload, repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(symbol) for symbol in repo_map.get("symbols", [])]
    payload["imports"] = [dict(entry) for entry in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    payload.pop("_profiling", None)
    # dogfood finding 1 (council must-fix #2, stamp the partial boolean DIRECTLY): every current
    # caller of this function (agent/context/edit-plan's own render/pack builders) passes NO
    # deadline_hit at all, so a sibling loop breaking early INSIDE _build_context_pack_from_map
    # (symbol-scoring, pagerank) was silently unstamped -- `_copy_partial_signal` can only
    # PROPAGATE an existing dict's `partial` key forward, it cannot originate one from a bare
    # _DeadlineBreakFlag. Always own a flag here (reuse the caller's if one WAS supplied, so a
    # caller folding this into its own wider N-way union -- mirroring the callers/impact/
    # blast-radius fold-in pattern -- still observes `.hit`).
    own_deadline_hit = deadline_hit if deadline_hit is not None else _repo_map._DeadlineBreakFlag()
    payload = _repo_map._build_context_pack_from_map(
        payload,
        query,
        auto_deweight=auto_deweight,
        _test_source_limit=_test_source_limit,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=own_deadline_hit,
        _test_scan_counts=_test_scan_counts,
        _profiling_collector=_profiling_collector,
    )
    if own_deadline_hit.hit:
        payload["partial"] = True
        # setdefault, not overwrite: a repo_map already partial from the SCAN stage (build_repo_
        # map's own --deadline cutoff, copied onto `payload` via `dict(repo_map)` above) carries a
        # richer deadline_limit (files_scanned/files_total) -- never clobber that with the generic
        # shape below just because a post-map sibling loop also happened to cross the same budget.
        _repo_map._stamp_partial_status(payload, deadline_exceeded=True, emit_reason=False)
    return _repo_map._attach_profiling(payload, _profiling_collector)
