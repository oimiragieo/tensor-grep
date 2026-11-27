from __future__ import annotations

import json
from typing import Any

from tensor_grep.cli import focused_rendering as _focused_rendering
from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_graph as _repo_map_graph


def _blast_radius_no_match_is_possibly_truncated(payload: dict[str, Any]) -> bool:
    """True iff a blast-radius-shaped payload is a ``no_match`` produced from a
    possibly-truncated repo map -- the one case where a map-based lookup that skipped the
    literal-seed rescue below cannot be trusted (the symbol may simply sit outside the scan
    window, not genuinely be absent from the repo).

    ONE shared definition for three call sites that must agree verbatim on exactly when a
    no_match is trustworthy, instead of three independently-drifting inline copies:
    (1) this function's own caller below, which retries via ``_literal_symbol_seed_files``;
    (2) the Tier-2 daemon agent-capsule's call-site-evidence collector
    (``agent_capsule._collect_capsule_call_site_evidence_from_map``), which has no rescue
    available on the daemon's cached map and must instead signal its caller to fall back to the
    cold path (task #108, the TRAP A class); and (3) ``main._daemon_blast_radius_no_match_is_
    unreliable`` (the Tier-1 ``blast-radius`` command's own daemon-fallback gate, audit #107).

    Deliberately narrow: only fires on ``no_match`` AND an unfinished scan together. A
    no_match on a COMPLETE map is a real miss -- treating that as untrustworthy too would
    defeat retries/daemon-fallback for every genuine no-match, not just the truncated ones.

    Task 332 widened WHICH truncations count, not how narrow the gate is. The body read
    ``scan_limit.possibly_truncated`` directly, so it was blind to the ``--deadline`` arm and
    treated a deadline-truncated no_match as TRUSTWORTHY at all three call sites above --
    silently skipping the rescue in exactly the case that needs it most (a deadline cutoff can
    leave ``files_scanned: 0``). The name is kept because three modules import it by that name;
    the predicate is now ``_scan_did_not_finish``, which covers both causes.

    Direction of the change: this only ever ADDS rescue attempts. Nothing that previously
    retried stops retrying.
    """
    if not payload.get("no_match"):
        return False
    return _repo_map._scan_did_not_finish(payload)


def build_symbol_blast_radius(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_callers: int | None = None,
    max_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # capability P0-6 step 3: ONE absolute deadline shared across BOTH the initial scan and the
    # literal-seed retry below -- a per-call re-derivation would double the wall-clock for exactly
    # the already-truncated huge repos that need a deadline most.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    payload = _repo_map.build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    if _repo_map._blast_radius_no_match_is_possibly_truncated(payload):
        seed_files = _repo_map._literal_symbol_seed_files(
            _repo_map._resolved_input_path(path),
            symbol,
            existing_files=_repo_map._repo_map_file_universe(repo_map),
        )
        if seed_files:
            repo_map = _repo_map.build_repo_map(
                path,
                max_repo_files=max_repo_files,
                extra_files=seed_files,
                deadline_monotonic=deadline_monotonic,
                _profiling_collector=_profiling_collector,
            )
            payload = _repo_map.build_symbol_blast_radius_from_map(
                repo_map,
                symbol,
                max_depth=max_depth,
                semantic_provider=semantic_provider,
                deadline_monotonic=deadline_monotonic,
                _profiling_collector=_profiling_collector,
            )
            payload_scan_limit = payload.get("scan_limit")
            if isinstance(payload_scan_limit, dict):
                payload_scan_limit["literal_seed_files"] = [str(current) for current in seed_files]
    result = _repo_map._apply_blast_radius_output_limits(
        payload,
        max_callers=max_callers,
        max_files=max_files,
    )
    _repo_map._copy_partial_signal(
        result, repo_map
    )  # deadline signal from the (possibly retried) scan
    return result


def build_symbol_blast_radius_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline. This is blast-radius' OWN
    # DIRECT defs call (distinct from the deadline-aware callers_payload/impact_payload sub-calls
    # below, which already threaded deadline_monotonic through their own now-fixed sibling defs
    # calls) -- fixing only the sub-calls would leave THIS call blocking before either of them
    # ever runs. Folded into this function's own partial stamp below (defs_payload.get("partial")).
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
        payload["routing_reason"] = "symbol-blast-radius"
        payload["max_depth"] = max(0, int(max_depth))
        payload["callers"] = []
        payload["import_graph_consumers"] = []
        payload["import_graph_consumer_files"] = []
        payload["import_graph_consumer_count"] = 0
        payload["affected_files"] = []
        payload["blast_radius_score"] = 0.0
        payload["file_matches"] = []
        payload["file_summaries"] = []
        payload["test_matches"] = []
        payload["caller_tree"] = []
        payload["rendered_caller_tree"] = str(
            defs_payload.get("message", "No exact definition found.")
        )
        payload["graph_trust_summary"] = {
            "graph_completeness": "empty",
            "edge_confidence": "none",
            "evidence_counts": {"parser_backed": 0, "heuristic": 0},
        }
        payload.setdefault("resolution_gaps", [])
        _repo_map.inherit_coverage_gap(payload, defs_payload)
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _repo_map._coverage_summary(payload)
        payload["provider_agreement"] = dict(default_agreement)
        payload["provider_status"] = dict(default_status)
        _repo_map._copy_lsp_evidence_status(payload, defs_payload)
        if "scan_limit" in repo_map:
            payload["scan_limit"] = dict(repo_map["scan_limit"])
        return _repo_map._attach_profiling(payload, _profiling_collector)
    callers_payload = _repo_map.build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop C): forward the shared deadline into the impact call too -- previously
    # unbounded (build_symbol_impact_from_map had no deadline param at all), so blast-radius
    # inherited an unbounded internal preferred-definition/related-tests scan via this call even
    # though every other seam in this function is deadline-aware.
    impact_payload = _repo_map.build_symbol_impact_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # task #61: this blast-radius-local call feeds the same sibling loop as the callers seam above
    # -- share the SAME deadline_monotonic and fold its early-break signal into this payload's own
    # partial marking below (build_symbol_callers_from_map already folds its OWN internal
    # preferred-definition-files/import-graph-consumers calls into callers_payload["partial"]).
    preferred_definition_deadline_hit_blast = _repo_map._DeadlineBreakFlag()
    preferred_definition_files = _repo_map._preferred_definition_files(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=preferred_definition_deadline_hit_blast,
    )
    preferred_definition_file_set = set(preferred_definition_files)
    raw_defs = [
        d for d in defs_payload["definitions"] if str(d["file"]) in preferred_definition_file_set
    ] or defs_payload["definitions"]
    definitions = [
        {
            **dict(current),
            "provenance": str(
                current.get(
                    "provenance",
                    _repo_map._symbol_navigation_provenance_for_path(str(current["file"])),
                )
            ),
        }
        for current in raw_defs
    ]

    normalized_depth = max(0, int(max_depth))
    all_files = [str(current) for current in repo_map.get("files", [])]
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get(
                "provenance", _repo_map._symbol_navigation_provenance_for_path(str(current["file"]))
            )
        )
        for current in repo_map.get("imports", [])
    }
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(
                str(import_name) for import_name in current.get("imports", []) if import_name
            )
        )
        for current in repo_map.get("imports", [])
    }
    # #222 (real-workspace-scale residual of #220/#669/#671): this is blast-radius' OWN direct
    # reverse-import-graph derivation (distinct from callers_payload/impact_payload's already-
    # deadline-aware sub-scans read above) -- it fed the same un-gated `_reverse_import_distances`/
    # `_reverse_importers` this PR bounds in `_build_context_pack_from_map`, reached from `tg
    # agent`'s cold path via `_collect_capsule_call_site_evidence`'s blast-radius call whenever a
    # symbol was explicitly requested at high seed confidence. Dedicated flag (not reusing
    # `preferred_definition_deadline_hit_blast`, which names a DIFFERENT stage) folded into the
    # SAME partial/deadline_limit union below.
    reverse_import_graph_deadline_hit_blast = _repo_map._DeadlineBreakFlag()
    blast_uncertain: dict[str, dict[str, Any]] = {}
    blast_candidate_statuses: dict[str, dict[str, Any]] = {}
    reverse_importers = _repo_map._reverse_importers(
        all_files,
        imports_by_file,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_import_graph_deadline_hit_blast,
        _profiling_collector=_profiling_collector,
    )
    definition_files = [str(current["file"]) for current in definitions]
    if _repo_map.os.environ.get("TG_BLAST_SYMBOL_PRUNE") == "1":
        reverse_importers = _repo_map.prune_symbol_unreachable_importers(
            symbol,
            definition_files,
            reverse_importers,
            all_files,
            imports_by_file=imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=reverse_import_graph_deadline_hit_blast,
        )
    reachable_files = (
        set(definition_files)
        | set(reverse_importers)
        | {imp for imps in reverse_importers.values() for imp in imps}
    )
    dependency_distances = {
        path: dist
        for path, dist in _repo_map._reverse_import_distances(
            definition_files,
            all_files,
            imports_by_file,
            repo_root=_repo_map._repo_map_root_dir(repo_map),
            uncertain_candidates=blast_uncertain,
            candidate_statuses=blast_candidate_statuses,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=reverse_import_graph_deadline_hit_blast,
            _profiling_collector=_profiling_collector,
        ).items()
        if _repo_map.os.environ.get("TG_BLAST_SYMBOL_PRUNE") != "1" or path in reachable_files
    }
    if _repo_map.os.environ.get("TG_BLAST_SYMBOL_PRUNE") == "1":
        blast_uncertain = {k: v for k, v in blast_uncertain.items() if k in reachable_files}
    for target, importers in reverse_importers.items():
        reverse_importers[target] = {
            importer
            for importer in importers
            if blast_candidate_statuses.get(importer, {}).get("status") != "nonedge"
        }
    reverse_graph_scores = _repo_map._personalized_reverse_import_pagerank(
        definition_files,
        all_files,
        reverse_importers,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_import_graph_deadline_hit_blast,
        _profiling_collector=_profiling_collector,
    )

    direct_callers = [dict(current) for current in callers_payload.get("callers", [])]
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in direct_callers))
    import_graph_consumers = [
        dict(current)
        for current in _repo_map._list_of_dicts(callers_payload.get("import_graph_consumers"))
    ]
    import_graph_consumer_files = sorted(
        dict.fromkeys(str(current["file"]) for current in import_graph_consumers)
    )
    import_graph_consumer_file_set = set(import_graph_consumer_files)

    file_matches_by_path: dict[str, dict[str, Any]] = {}
    file_depths: dict[str, int] = {}

    def _merge_file_match(
        current_path: str,
        *,
        depth: int,
        score: int,
        reasons: list[str],
        graph_score: float | None = None,
    ) -> None:
        entry = file_matches_by_path.setdefault(
            current_path,
            {
                "path": current_path,
                "depth": depth,
                "score": 0,
                "reasons": [],
            },
        )
        entry["depth"] = min(int(entry.get("depth", depth)), depth)
        entry["score"] = max(int(entry.get("score", 0)), score)
        for reason in reasons:
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        if graph_score is not None and graph_score > float(entry.get("graph_score", 0.0)):
            entry["graph_score"] = round(graph_score, 6)
        file_depths[current_path] = min(file_depths.get(current_path, depth), depth)

    for current in definition_files:
        current_match: dict[str, Any] = next(
            (
                item
                for item in impact_payload.get("file_matches", [])
                if str(item.get("path")) == current
            ),
            {},
        )
        _merge_file_match(
            current,
            depth=0,
            score=max(1, int(current_match.get("score", 0))),
            reasons=["definition", *list(current_match.get("reasons", []))],
            graph_score=(
                float(current_match["graph_score"])
                if "graph_score" in current_match
                else reverse_graph_scores.get(current)
            ),
        )

    for current in caller_files:
        _merge_file_match(
            current,
            depth=1 if current not in definition_files else 0,
            score=max(
                1,
                int(
                    next(
                        (
                            item.get("score", 0)
                            for item in impact_payload.get("file_matches", [])
                            if str(item.get("path")) == current
                        ),
                        0,
                    )
                ),
            ),
            reasons=["caller"],
            graph_score=reverse_graph_scores.get(current),
        )

    for current, depth in dependency_distances.items():
        if depth > normalized_depth:
            continue
        reasons = ["graph-depth"]
        if current in caller_files and "caller" not in reasons:
            reasons.append("caller")
        if current in import_graph_consumer_file_set:
            reasons.append("import-consumer")
        if current in definition_files and "definition" not in reasons:
            reasons.append("definition")
        graph_score = reverse_graph_scores.get(current, 0.0)
        score = max(1, round(graph_score * 10))
        _merge_file_match(
            current,
            depth=depth,
            score=score,
            reasons=reasons,
            graph_score=graph_score if graph_score > 0.0 else None,
        )

    for current, evidence in blast_uncertain.items():
        if blast_candidate_statuses.get(current, {}).get("status") in {"origin", "confirmed"}:
            continue
        depth = int(evidence.get("depth", normalized_depth + 1))
        if depth <= normalized_depth:
            _merge_file_match(
                current,
                depth=depth,
                score=1,
                reasons=["heuristic-import"],
            )

    ranked_files = sorted(
        file_matches_by_path.values(),
        key=lambda item: (
            int(item.get("depth", normalized_depth + 1)),
            -int(item.get("score", 0)),
            -float(item.get("graph_score", 0.0)),
            str(item.get("path", "")),
        ),
    )
    radius_files = [str(item["path"]) for item in ranked_files]

    test_match_lookup = {
        str(item["path"]): dict(item) for item in impact_payload.get("test_matches", [])
    }
    related_tests: list[str] = []
    for current in impact_payload.get("tests", []):
        test_entry = test_match_lookup.get(str(current), {})
        reasons = list(test_entry.get("reasons", []))
        if "blast-radius" not in reasons:
            reasons.append("blast-radius")
        graph_score = float(test_entry.get("graph_score", 0.0))
        score = int(test_entry.get("score", 0))
        coverage_hits = 0
        current_imports = imports_by_file.get(str(current), [])
        for source_file in radius_files:
            aliases = _repo_map._module_aliases_for_path(source_file)
            if any(
                alias and alias in import_name.lower()
                for alias in aliases
                for import_name in current_imports
            ):
                coverage_hits += 1
        if coverage_hits <= 0 and not any(
            reason
            in {"import-graph", "graph-centrality", "framework-pattern", "test-graph", "filename"}
            for reason in reasons
        ):
            continue
        score += coverage_hits
        related_tests.append(str(current))
        updated_entry = dict(test_entry)
        updated_entry["path"] = str(current)
        updated_entry["score"] = score
        updated_entry["reasons"] = reasons
        if graph_score > 0.0:
            updated_entry["graph_score"] = graph_score
        test_match_lookup[str(current)] = updated_entry

    caller_tree: list[dict[str, Any]] = []
    rendered_lines = [f"Blast radius for {symbol}:"]
    realised_depths = sorted({
        int(item.get("depth", normalized_depth + 1))
        for item in ranked_files
        if not (
            "heuristic-import" in item.get("reasons", [])
            and not {"caller", "graph-depth"}.intersection(item.get("reasons", []))
        )
        if 0 <= int(item.get("depth", normalized_depth + 1)) <= normalized_depth
    })
    for depth in realised_depths:
        depth_files = [
            str(item["path"])
            for item in ranked_files
            if int(item.get("depth", normalized_depth + 1)) == depth
            and not (
                "heuristic-import" in item.get("reasons", [])
                and not {"caller", "graph-depth"}.intersection(item.get("reasons", []))
            )
        ]
        if not depth_files:
            continue
        parser_backed_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"python-ast", "tree-sitter"}
        )
        heuristic_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"regex-heuristic", "heuristic"}
        )
        edge_provenance = ["graph-derived"]
        if parser_backed_edges > 0:
            edge_provenance.append("parser-backed")
        if heuristic_edges > 0:
            edge_provenance.append("heuristic")
        if parser_backed_edges > 0 and heuristic_edges == 0:
            edge_confidence = "strong"
        elif parser_backed_edges > 0:
            edge_confidence = "moderate"
        else:
            edge_confidence = "weak"
        caller_tree.append({
            "depth": depth,
            "files": depth_files,
            "provenance": edge_provenance,
            "graph_completeness": "moderate",
            "edge_summary": {
                "edge_kind": "reverse-import",
                "confidence": edge_confidence,
                "provenance": edge_provenance,
                "evidence_counts": {
                    "parser_backed": parser_backed_edges,
                    "heuristic": heuristic_edges,
                },
            },
        })
        rendered_lines.append(f"Depth {depth}:")
        rendered_lines.extend(f"- {current}" for current in depth_files)

    related_paths: list[str] = []
    for current in [*radius_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _repo_map._envelope(_repo_map.Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-blast-radius"
    payload["symbol"] = symbol
    payload["max_depth"] = normalized_depth
    payload["definitions"] = definitions
    payload["callers"] = direct_callers
    payload["import_graph_consumers"] = import_graph_consumers
    payload["import_graph_consumer_files"] = import_graph_consumer_files
    payload["import_graph_consumer_count"] = len(import_graph_consumers)
    payload["files"] = radius_files
    payload["affected_files"] = list(radius_files)
    evidence_score = sum(min(10, max(0, int(item.get("score", 0)))) for item in ranked_files)
    evidence_score += len(direct_callers) + len(related_tests)
    evidence_denominator = max(
        1, (10 * len(ranked_files)) + len(direct_callers) + len(related_tests)
    )
    payload["blast_radius_score"] = round(min(1.0, evidence_score / evidence_denominator), 3)
    payload["file_matches"] = ranked_files
    payload["file_summaries"] = _repo_map._file_summaries(repo_map.get("symbols", []), radius_files)
    payload["tests"] = related_tests
    payload["test_matches"] = [test_match_lookup[str(current)] for current in related_tests]
    payload["caller_tree"] = caller_tree
    payload["rendered_caller_tree"] = "\n".join(rendered_lines)
    payload["resolution_gaps"] = list(callers_payload.get("resolution_gaps", []))
    _repo_map_graph.add_uncertainty(
        _repo_map, payload, list(blast_uncertain), _repo_map._repo_map_root_dir(repo_map)
    )
    payload["graph_trust_summary"] = _repo_map._downgrade_graph_trust_summary_for_coverage_gaps(
        _repo_map._graph_trust_summary(caller_tree, calls=direct_callers),
        payload["resolution_gaps"],
    )
    payload["imports"] = impact_payload["imports"]
    payload["symbols"] = impact_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _repo_map._ranking_quality(
        payload["file_matches"], payload["test_matches"]
    )
    payload["coverage_summary"] = _repo_map._coverage_summary(payload)
    if blast_uncertain:
        payload["coverage_summary"]["graph_completeness"] = "partial"
    payload["semantic_provider"] = _repo_map._normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(
        callers_payload.get("provider_agreement", default_agreement)
    )
    payload["provider_status"] = dict(callers_payload.get("provider_status", default_status))
    _repo_map._copy_lsp_evidence_status(payload, callers_payload)
    if "scan_limit" in repo_map:
        payload["scan_limit"] = dict(repo_map["scan_limit"])
    # capability P0-6 step 6: the direct-caller scan may have been cut by --deadline for a CENTRAL symbol
    # -> carry its partial + deadline_limit onto the blast radius so a caller does not trust a
    # truncated caller_tree / blast_radius_score as complete.
    # task #61: OR in this function's OWN preferred-definition-files call (a second, redundant call
    # separate from the one build_symbol_callers_from_map already made internally) -- it can still
    # push wall-clock past the shared deadline even when callers_payload came back complete.
    # #52 fix (loop C): ALSO OR in impact_payload's own partial signal -- this function reads
    # impact_payload["file_matches"]/["tests"]/["imports"]/["symbols"] directly (below), so a
    # deadline-truncated impact scan makes THIS result incomplete too, even when callers_payload
    # and this function's own direct preferred-definition-files call both finished inside budget.
    # audit C1/C2: ALSO OR in defs_payload's own partial signal -- defs_payload is
    # this function's OWN direct build_symbol_defs_from_map call above (distinct from the nested
    # defs calls inside callers_payload/impact_payload, which already fold into THEIR OWN partial
    # fields read above); a deadline blown during THIS call's stage-1 scan must not go unreported
    # just because it isn't otherwise read by name past this point.
    # #222: ALSO OR in this function's OWN reverse-import-graph derivation (`reverse_importers`/
    # `dependency_distances`/`reverse_graph_scores` above) -- previously un-gated and unreported.
    if (
        callers_payload.get("partial")
        or preferred_definition_deadline_hit_blast.hit
        or impact_payload.get("partial")
        or defs_payload.get("partial")
        or reverse_import_graph_deadline_hit_blast.hit
    ):
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        for source_partial in (callers_payload, impact_payload, defs_payload):
            _repo_map._copy_partial_signal(payload, source_partial)
        _repo_map._stamp_partial_status(
            payload,
            emit_reason=False,
            deadline_exceeded=(
                preferred_definition_deadline_hit_blast.hit
                or reverse_import_graph_deadline_hit_blast.hit
            ),
        )
    if callers_payload.get("result_incomplete") and not _repo_map.inherit_coverage_gap(
        payload, callers_payload
    ):
        # backlog #1 chokepoint: the direct-caller scan's internal ceiling (CALLER_SCAN_FILE_CEILING)
        # dropped files the map covers -> the blast radius built on top of it is not exhaustive
        # either (session_blast_radius calls this function directly on a full, unbounded session
        # repo_map -- this is the leak fix, since a per-command option default can't reach that path).
        # `caller_scan_truncated` is a DISTINCT scan-incompleteness signal so the blast-radius CLI gate
        # can exit 2 on it WITHOUT catching a mere --max-callers/--max-files OUTPUT cap (which also sets
        # result_incomplete but is a complete analysis capped only for display -> stays exit 0).
        payload["caller_scan_truncated"] = True
        callers_caller_scan_limit = callers_payload.get("caller_scan_limit")
        _repo_map._mark_result_incomplete(
            payload,
            remediation=str(
                callers_payload.get("scan_remediation", _repo_map._CALLER_SCAN_CEILING_REMEDIATION)
            ),
            caller_scan_limit=(
                dict(callers_caller_scan_limit)
                if isinstance(callers_caller_scan_limit, dict)
                else None
            ),
        )
    _repo_map._copy_partial_signal(payload, repo_map)
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_callers: int | None = None,
    max_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_blast_radius(
            symbol,
            path,
            max_depth=max_depth,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
            max_callers=max_callers,
            max_files=max_files,
        ),
        indent=2,
    )


def build_symbol_blast_radius_plan(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg blast-radius-plan` (Click
    # "No such option" exit-2) even though its true sibling build_symbol_blast_radius already had
    # it -- same thin, additive thread-through (mirrors #581's build_symbol_defs / this file's own
    # build_symbol_blast_radius above), threaded into BOTH the initial repo_map build and the
    # _from_map call below so the caller-scan pass inside build_symbol_blast_radius_from_map also
    # honors the same shared absolute budget, not just the file walk/parse.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = _repo_map.build_symbol_blast_radius_plan_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    _repo_map._copy_partial_signal(result, repo_map)
    return result


def build_symbol_blast_radius_plan_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = _repo_map.build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    payload["routing_reason"] = "symbol-blast-radius-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = list(payload.get("file_summaries", []))[:normalized_max_files]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _repo_map._sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload = _repo_map._attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=normalized_max_files,
        max_symbols=normalized_max_symbols,
        max_depth=max_depth,
        blast_radius_payload=payload,
        # Task #203: this scan can exceed the caller's budget even when the repo-map and graph
        # stages honor it, so pass the same deadline into edit-plan test discovery.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
        # #212 (broader B9/#661 flag-lie): `tg blast-radius-plan` has NO downstream compaction step
        # at all (unlike context-render), so before this, edit_plan_seed.suggested_edits was
        # unconditionally unbounded regardless of --max-files -- dogfooded on tensor-grep's own repo
        # (SearchConfig, 80 files): --max-files 1 returned files=[1] but suggested_edits spanning 8
        # distinct files. Opt into the same mechanism build_context_edit_plan_from_map already uses.
        suggested_edits_max=normalized_max_files,
    )
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_plan_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_blast_radius_plan(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def build_symbol_blast_radius_render(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    return _repo_map.build_symbol_blast_radius_render_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )


def build_symbol_blast_radius_render_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    radius_payload = _repo_map.build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    normalized_profile = _repo_map._normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)

    top_files = {str(current) for current in radius_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    ranked_symbols = _repo_map._sorted_ranked_symbols(list(radius_payload.get("symbols", [])))
    # Perf guard (TG-4): a high-fan-in symbol yields thousands of candidate symbols in the top
    # files, and each candidate triggers an expensive build_symbol_source_from_map lookup. With
    # only the max_sources accumulation as a bound, a symbol whose candidates rarely yield a
    # matching source scanned them ALL â€” ~3.5 min on a large repo vs ~3s for the JSON graph.
    # Cap the expensive per-candidate lookups. ranked_symbols is relevance-sorted, so the best
    # sources are examined first and we degrade gracefully to fewer rendered blocks.
    max_source_candidates = max(max_sources * 8, 24)
    examined_candidates = 0
    # The shared deadline bounds build, graph, and source lookup; check before filtering because a
    # candidate-count cap alone does not bound elapsed time.
    source_loop_deadline_hit = False
    for current_symbol in ranked_symbols:
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            source_loop_deadline_hit = True
            break
        current_file = str(current_symbol["file"])
        if current_file not in top_files:
            continue
        symbol_key = (current_file, str(current_symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        examined_candidates += 1
        if examined_candidates > max_source_candidates:
            break
        symbol_sources = _repo_map.build_symbol_source_from_map(
            repo_map,
            str(current_symbol["name"]),
            _profiling_collector=collector,
        ).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _repo_map._render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            break
        if len(sources) >= max_sources:
            break

    payload = dict(radius_payload)
    payload["routing_reason"] = "symbol-blast-radius-render"
    payload["query"] = f"blast radius: {symbol}"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    if normalized_profile == "focused":
        sources = _focused_rendering.focus_sources(sources, symbol)
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    (
        rendered_context,
        sections,
        truncated,
        token_estimate,
        omitted_sections,
    ) = _repo_map._render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    focused_omissions = _focused_rendering.omissions(sources)
    payload["truncated"] = truncated or bool(focused_omissions)
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = [*focused_omissions, *omitted_sections]
    payload = _repo_map._attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=max_files,
        max_symbols=max_sources,
        max_depth=max_depth,
        blast_radius_payload=radius_payload,
        # task #203: thread the deadline into the edit-plan-seed assembly too, mirroring the
        # identical fix on build_context_edit_plan_from_map (repo_map.py:12617-12622) and
        # build_symbol_blast_radius_plan_from_map (this file, just above) -- this call previously
        # dropped deadline_monotonic entirely.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
        # #212 (broader B9/#661 flag-lie): `tg blast-radius-render` has NO downstream compaction
        # step either, in ANY render_profile, so before this, edit_plan_seed.suggested_edits was
        # unconditionally unbounded regardless of --max-files -- dogfooded on tensor-grep's own repo
        # (SearchConfig, 80 files): --max-files 1 returned files=[1] but suggested_edits spanning 40
        # distinct files (identical 73-entry count as --max-files 50 -- zero bounding effect at all).
        # Opt into the same mechanism build_context_edit_plan_from_map already uses.
        suggested_edits_max=max_files,
    )
    if normalized_profile == "focused":
        payload = _repo_map._apply_context_consistency_invariants(payload)
    # task #203: fold this function's OWN source-lookup loop deadline signal into partial --
    # `dict(radius_payload)` above already copied forward any partial/deadline_limit that
    # build_symbol_blast_radius_from_map (or _attach_edit_plan_metadata's own edit_plan_seed fold-in
    # just above) already stamped, so `setdefault` here never clobbers a richer upstream signal;
    # this only adds the flag when THIS loop was the one that broke early.
    _repo_map._stamp_partial_status(payload)
    if source_loop_deadline_hit:
        payload["partial"] = True
        # REQUIRED by the render-family contract: the other three members all stamp it and
        # test_render_family_tail_overrun_..._still_reports_partial asserts it for every member.
        # This command was absent from that parametrize list, so it shipped without the stamp.
        # setdefault, never assignment -- do not clobber a richer upstream reason.
        _repo_map._stamp_partial_status(payload, deadline_exceeded=True)
        payload["deadline_limit"].setdefault("source_candidates_examined", examined_candidates)
        payload["deadline_limit"].setdefault("source_candidates_total", len(ranked_symbols))
    return _repo_map._attach_profiling(payload, collector)
