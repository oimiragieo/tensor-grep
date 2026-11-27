from __future__ import annotations

import json
import re
from typing import Any

from tensor_grep.cli import repo_map as _repo_map


def _preferred_edit_anchor_symbol(
    primary_symbol: dict[str, Any] | None,
    ranked_symbols: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if primary_symbol is not None:
        primary_file = primary_symbol.get("file")
        if primary_file and not _repo_map._is_test_file(_repo_map.Path(str(primary_file))):
            return primary_symbol
    for symbol in ranked_symbols:
        file_path = symbol.get("file")
        if file_path and not _repo_map._is_test_file(_repo_map.Path(str(file_path))):
            return symbol
    return primary_symbol


def _symbol_is_unrequested_marker_helper(
    symbol: dict[str, Any] | None,
    query: str,
) -> bool:
    if symbol is None:
        return False
    symbol_name = str(symbol.get("name", "") or "")
    if not symbol_name:
        return False
    query_terms = set(_repo_map._query_terms(query))
    symbol_terms = set(_repo_map.split_terms(symbol_name))
    return "marker" in symbol_terms and "marker" not in query_terms


def _symbol_name_mentions_marker(symbol: dict[str, Any] | None) -> bool:
    if symbol is None:
        return False
    symbol_name = str(symbol.get("name", "") or "")
    return "marker" in set(_repo_map.split_terms(symbol_name))


def _non_marker_implementation_candidate(
    primary_symbol: dict[str, Any],
    ranked_symbols: list[dict[str, Any]],
    *,
    selected_files: set[str],
    query_language_hints: list[str],
) -> dict[str, Any] | None:
    primary_file = str(primary_symbol.get("file", "") or "")
    primary_score = int(primary_symbol.get("score", 0) or 0)
    primary_span = _repo_map._symbol_span_length(primary_symbol)
    primary_language_matches_hint = bool(
        query_language_hints
        and _repo_map._path_matches_query_language_hints(primary_file, query_language_hints)
    )
    for candidate in ranked_symbols:
        candidate_file = str(candidate.get("file", "") or "")
        if not candidate_file or candidate_file not in selected_files:
            continue
        if candidate_file == primary_file:
            continue
        if _repo_map._is_test_file(_repo_map.Path(candidate_file)):
            continue
        if _repo_map._symbol_name_mentions_marker(candidate):
            continue
        if primary_language_matches_hint and not _repo_map._path_matches_query_language_hints(
            candidate_file,
            query_language_hints,
        ):
            continue
        candidate_score = int(candidate.get("score", 0) or 0)
        candidate_span = _repo_map._symbol_span_length(candidate)
        if candidate_score > primary_score or (
            candidate_score == primary_score and candidate_span >= primary_span
        ):
            return candidate
    return None


def _promote_substantive_symbol_for_edit_seed(
    primary_symbol: dict[str, Any] | None,
    ranked_symbols: list[dict[str, Any]],
    *,
    query: str,
    selected_files: set[str],
    query_language_hints: list[str],
    primary_file_reasons: set[str],
) -> dict[str, Any] | None:
    if primary_symbol is None:
        return None
    primary_file = str(primary_symbol.get("file", "") or "")
    if not primary_file:
        return primary_symbol
    if _repo_map._symbol_is_unrequested_marker_helper(primary_symbol, query):
        implementation_candidate = _repo_map._non_marker_implementation_candidate(
            primary_symbol,
            ranked_symbols,
            selected_files=selected_files,
            query_language_hints=query_language_hints,
        )
        if implementation_candidate is not None:
            return implementation_candidate
    # An exactly-named primary symbol (H7) must not be demoted by a higher-scored
    # but merely graph-central candidate.
    if bool(primary_symbol.get("exact_query_match") or primary_symbol.get("filename_phrase_match")):
        return primary_symbol
    if "validation-direct-definition" in primary_file_reasons:
        return primary_symbol
    primary_score = int(primary_symbol.get("score", 0) or 0)
    primary_span = _repo_map._symbol_span_length(primary_symbol)
    primary_language_matches_hint = bool(
        query_language_hints
        and _repo_map._path_matches_query_language_hints(primary_file, query_language_hints)
    )
    for candidate in ranked_symbols:
        candidate_file = str(candidate.get("file", "") or "")
        if not candidate_file or candidate_file not in selected_files:
            continue
        if _repo_map._is_test_file(_repo_map.Path(candidate_file)):
            continue
        if primary_language_matches_hint and not _repo_map._path_matches_query_language_hints(
            candidate_file, query_language_hints
        ):
            continue
        if candidate_file == primary_file:
            return primary_symbol
        candidate_score = int(candidate.get("score", 0) or 0)
        candidate_span = _repo_map._symbol_span_length(candidate)
        if candidate_score > primary_score or (
            candidate_score == primary_score and candidate_span >= primary_span + 2
        ):
            return candidate
        return primary_symbol
    return primary_symbol


def _should_build_edit_plan_blast_radius(
    symbol: dict[str, Any] | None,
    file_match: dict[str, Any] | None,
) -> bool:
    if symbol is None:
        return False
    if bool(symbol.get("exact_query_match") or symbol.get("bridge_query_match")):
        return True

    reasons = {str(reason) for reason in (file_match or {}).get("reasons", [])}
    symbol_score = int(symbol.get("score", 0) or 0)
    if "validation-direct-definition" in reasons:
        return symbol_score >= 6
    return symbol_score >= 8 and bool(reasons & {"definition", "source", "import"})


def _with_lsp_primary_symbol_evidence(
    repo_map: dict[str, Any],
    primary_symbol: dict[str, Any] | None,
    *,
    semantic_provider: str,
) -> dict[str, Any] | None:
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native" or primary_symbol is None:
        return primary_symbol
    symbol_name = str(primary_symbol.get("name", "") or "")
    if not symbol_name:
        return primary_symbol
    try:
        defs_payload = _repo_map.build_symbol_defs_from_map(
            repo_map,
            symbol_name,
            semantic_provider=normalized_provider,
        )
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        _repo_map.LSPTransportError,
        ValueError,
    ):
        return primary_symbol
    proof_definitions = [
        dict(current)
        for current in defs_payload.get("definitions", [])
        if isinstance(current, dict) and _repo_map._is_lsp_proof_row(current)
    ]
    if not proof_definitions:
        return primary_symbol
    primary_file = str(primary_symbol.get("file", "") or "")
    selected_definition = next(
        (
            current
            for current in proof_definitions
            if primary_file and str(current.get("file", "")) == primary_file
        ),
        proof_definitions[0],
    )
    enriched = dict(primary_symbol)
    for key in (
        "file",
        "kind",
        "line",
        "start_line",
        "end_line",
        "provenance",
        "lsp_provider_response",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in selected_definition:
            enriched[key] = selected_definition[key]
    if "start_line" not in enriched and "line" in enriched:
        enriched["start_line"] = enriched["line"]
    if "end_line" not in enriched:
        enriched["end_line"] = enriched.get("start_line", enriched.get("line", 1))
    enriched["lsp_proof"] = True
    enriched["semantic_provider"] = normalized_provider
    try:
        enriched["score"] = max(
            int(enriched.get("score", 0) or 0),
            int(selected_definition.get("score", 0) or 0),
        )
    except (TypeError, ValueError):
        pass
    return enriched


def _scoped_repo_map_for_edit_plan_blast_radius(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    symbol: str,
    *,
    max_files: int,
) -> dict[str, Any]:
    limit = max(1, int(max_files)) * _repo_map._EDIT_PLAN_BLAST_RADIUS_FILE_MULTIPLIER
    all_files = [str(current) for current in repo_map.get("files", [])]
    all_file_set = set(all_files)
    selected_files: list[str] = []
    selected_file_set: set[str] = set()

    def _add_file(path_value: object) -> None:
        if len(selected_files) >= limit:
            return
        path = str(path_value or "")
        if not path or path in selected_file_set or path not in all_file_set:
            return
        selected_files.append(path)
        selected_file_set.add(path)

    for current in repo_map.get("symbols", []):
        if not isinstance(current, dict) or str(current.get("name", "")) != symbol:
            continue
        _add_file(current.get("file"))
    for current in payload.get("files", []):
        _add_file(current)
    for current in payload.get("tests", []):
        _add_file(current)

    if len(selected_files) < limit:
        definition_aliases: set[str] = set()
        for current in selected_files:
            definition_aliases.update(_repo_map._module_aliases_for_path(current))
        for entry in repo_map.get("imports", []):
            if not isinstance(entry, dict):
                continue
            importer = str(entry.get("file", "") or "")
            if importer in selected_file_set:
                continue
            imported_aliases: set[str] = set()
            for import_name in entry.get("imports", []):
                imported_aliases.update(_repo_map._import_alias_candidates(str(import_name)))
            if definition_aliases & imported_aliases:
                _add_file(importer)
            if len(selected_files) >= limit:
                break

    selected_file_set = set(selected_files)
    scoped = dict(repo_map)
    scoped["files"] = selected_files
    scoped["symbols"] = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if isinstance(current, dict) and str(current.get("file", "")) in selected_file_set
    ]
    scoped["imports"] = [
        dict(current)
        for current in repo_map.get("imports", [])
        if isinstance(current, dict) and str(current.get("file", "")) in selected_file_set
    ]
    scoped["tests"] = [
        str(current) for current in repo_map.get("tests", []) if str(current) in selected_file_set
    ]
    scoped["edit_plan_blast_radius_scope"] = {
        "max_files": limit,
        "source": "selected-context",
        "original_file_count": len(all_files),
        "scoped_file_count": len(selected_files),
    }
    return scoped


def _rollback_risk_from_blast_radius(
    *,
    dependent_matches: list[dict[str, Any]],
    caller_symbol_count: int,
    associated_test_count: int,
    max_depth: int,
) -> float:
    """Heuristic rollback-risk score for an edit, from its blast radius.

    P10 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 15):
    ``associated_test_count`` is a STATIC count of test files the repo map found importing or
    referencing the edited symbol -- it is test ASSOCIATION, not measured runtime coverage.
    None of those tests have been observed to execute or pass; this function must never be read
    as "N% of this change is covered by passing tests." A genuinely execution-verified receipt
    (a test actually run, at a pinned revision, with its result captured) is a separate,
    stronger claim -- see ``evidence_receipt.py``/``evidence_signing.py`` for that primitive.
    """
    if not dependent_matches and caller_symbol_count <= 0:
        return 0.0

    normalized_max_depth = max(1, int(max_depth))
    observed_depth = max((int(current["depth"]) for current in dependent_matches), default=0)
    dependent_count = len(dependent_matches)
    depth_factor = min(1.0, observed_depth / normalized_max_depth)
    caller_factor = min(1.0, caller_symbol_count / 5.0)
    dependent_factor = min(1.0, dependent_count / 6.0)
    test_association_factor = min(1.0, associated_test_count / max(1, dependent_count + 1))
    risk = (
        0.1
        + (0.3 * depth_factor)
        + (0.25 * caller_factor)
        + (0.15 * dependent_factor)
        - (0.25 * test_association_factor)
    )
    return round(min(1.0, max(0.0, risk)), 3)


def _is_distinctive_identifier(token: str) -> bool:
    """Return ``True`` when ``token`` looks like a deliberate code identifier.

    Distinctive identifiers carry a structural signal that a bare English word
    does not: an underscore, a digit, or internal case mixing (``camelCase`` /
    ``PascalCase``). This keeps the exact-symbol fallback from latching onto a
    common word (``device``, ``file``) that merely happens to also be a symbol.
    """

    if len(token) < 3:
        return False
    if "_" in token or any(char.isdigit() for char in token):
        return True
    lowered = token.lower()
    return token not in {lowered, token.upper()}


def _exact_query_match_primary_symbol(
    ranked_symbols: list[dict[str, Any]],
    *,
    repo_map: dict[str, Any] | None = None,
    query: str = "",
) -> dict[str, Any] | None:
    """Return the best symbol whose name exactly matches a query token.

    When the query names a symbol that resolves exactly (``exact_query_match``
    was set during scoring), that definition should win primary-target selection
    over a higher-ranked graph-centrality candidate that merely happens to live
    in the top-ranked file. ``ranked_symbols`` is already score-sorted, so the
    first exact match with a resolvable file is the highest-scored exact match.

    Some pipelines (notably context-render) pre-filter ``ranked_symbols`` down to
    symbols defined in the top-ranked files, which can drop the exactly-named
    target before it reaches here. In that case fall back to scanning the full
    ``repo_map`` symbol inventory for a definition matching a query token and
    synthesize a candidate carrying ``exact_query_match`` so it is treated as the
    resolved primary target.
    """

    ranked = next(
        (
            current
            for current in ranked_symbols
            if bool(current.get("exact_query_match")) and current.get("file")
        ),
        None,
    )
    if ranked is not None:
        return ranked
    if repo_map is None or not query:
        return None

    query_tokens = set(re.findall(r"[A-Za-z0-9_]+", query))
    if not query_tokens:
        return None
    # Only fall back for distinctive identifiers the query *names* on purpose
    # (``tg_rewrite_plan``, ``camelCase``, ``Name2``) -- never a bare lowercase
    # English word such as ``device`` that merely happens to also be a symbol,
    # which would hijack descriptive queries away from graph-centrality ranking.
    distinctive_tokens = {
        token for token in query_tokens if _repo_map._is_distinctive_identifier(token)
    }
    if not distinctive_tokens:
        return None
    for definition in repo_map.get("symbols", []):
        if not isinstance(definition, dict):
            continue
        name = str(definition.get("name", "") or "")
        if name not in distinctive_tokens or not definition.get("file"):
            continue
        candidate = dict(definition)
        candidate["exact_query_match"] = True
        return candidate
    return None


def _build_edit_plan_seed(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    ranked_symbols: list[dict[str, Any]],
    query: str,
    max_files: int,
    max_depth: int = _repo_map._DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    suggested_edits_max: int | None = None,
) -> dict[str, Any]:
    primary_file = next(iter(payload.get("files", [])), None)
    primary_symbol = next(
        (
            current
            for current in ranked_symbols
            if primary_file is not None and str(current.get("file")) == str(primary_file)
        ),
        next(iter(ranked_symbols), None),
    )
    # H7: an exactly-named, resolvable symbol must outrank a graph-centrality
    # candidate that merely lives in the top-ranked file. Promote it here so the
    # capsule's primary_symbol stays coupled to the highest-scored candidate.
    exact_primary = _repo_map._exact_query_match_primary_symbol(
        ranked_symbols,
        repo_map=repo_map,
        query=query,
    )
    if exact_primary is not None and exact_primary is not primary_symbol:
        primary_symbol = exact_primary
        primary_file = str(exact_primary["file"])
    if primary_symbol is not None and primary_file is not None:
        primary_symbol_file = str(primary_symbol.get("file", "") or "")
        primary_file_has_ranked_symbol = any(
            str(current.get("file", "") or "") == str(primary_file) for current in ranked_symbols
        )
        if primary_symbol_file and primary_symbol_file != str(primary_file):
            if not primary_file_has_ranked_symbol:
                primary_file = primary_symbol_file
    if primary_symbol is not None and primary_file is None:
        preferred_files = _repo_map._preferred_definition_files(
            repo_map, str(primary_symbol.get("name", ""))
        )
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    selected_files = {
        str(current) for current in list(payload.get("files", []))[: max(1, int(max_files))]
    }
    promoted_symbol = _repo_map._promote_substantive_symbol_for_edit_seed(
        primary_symbol,
        ranked_symbols,
        query=query,
        selected_files=selected_files,
        query_language_hints=_repo_map._query_language_hints(query),
        primary_file_reasons={str(reason) for reason in primary_file_match.get("reasons", [])},
    )
    if promoted_symbol is not None and promoted_symbol is not primary_symbol:
        primary_symbol = promoted_symbol
        if primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
            primary_file_match = next(
                (
                    match
                    for match in payload.get("file_matches", [])
                    if str(match.get("path")) == str(primary_file)
                ),
                payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
            )
    lsp_enriched_symbol = _repo_map._with_lsp_primary_symbol_evidence(
        repo_map,
        primary_symbol,
        semantic_provider=semantic_provider,
    )
    if lsp_enriched_symbol is not None:
        primary_symbol = lsp_enriched_symbol
        if primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
            primary_file_match = next(
                (
                    match
                    for match in payload.get("file_matches", [])
                    if str(match.get("path")) == str(primary_file)
                ),
                payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
            )
    validation_tests = list(payload.get("tests", []))[: max(1, min(max_files, 3))]
    primary_symbol_name = (
        str(primary_symbol.get("name"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("name")
        else None
    )
    validation_root = (
        _repo_map._validation_repo_root(_repo_map._resolved_input_path(str(primary_file)).parent)
        if primary_file is not None
        else None
    )
    validation_file_paths = _repo_map._repo_map_validation_file_paths(
        repo_map,
        validation_root=validation_root,
    )
    if len(validation_tests) < max(1, min(max_files, 3)):
        for current in _repo_map._discover_validation_tests_for_primary_file(
            payload.get("path", "."),
            str(primary_file) if primary_file is not None else None,
            primary_symbol_name=primary_symbol_name,
            query=query,
            limit=max(1, min(max_files, 3)) - len(validation_tests),
            precomputed_file_paths=validation_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        ):
            if current not in validation_tests:
                validation_tests.append(current)
    primary_test = next(iter(validation_tests), None)
    primary_test_match: dict[str, Any] = next(
        (
            match
            for match in payload.get("test_matches", [])
            if str(match.get("path")) == str(primary_test)
        ),
        {"score": 1, "reasons": ["validation-discovery"]} if primary_test else {},
    )

    dependent_files: list[str] = []
    related_spans: list[dict[str, Any]] = []
    caller_symbol_count = 0
    rollback_risk = 0.0
    edit_anchor_symbol = _repo_map._preferred_edit_anchor_symbol(primary_symbol, ranked_symbols)
    edit_anchor_file = (
        str(edit_anchor_symbol.get("file"))
        if edit_anchor_symbol is not None and edit_anchor_symbol.get("file")
        else None
    )
    radius_payload = blast_radius_payload
    if (
        edit_anchor_symbol is not None
        and radius_payload is None
        and _repo_map._should_build_edit_plan_blast_radius(edit_anchor_symbol, primary_file_match)
    ):
        edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
        if edit_symbol_name:
            radius_repo_map = _repo_map._scoped_repo_map_for_edit_plan_blast_radius(
                repo_map,
                payload,
                edit_symbol_name,
                max_files=max_files,
            )
            radius_payload = _repo_map.build_symbol_blast_radius_from_map(
                radius_repo_map,
                edit_symbol_name,
                max_depth=max_depth,
            )
            scope = radius_repo_map.get("edit_plan_blast_radius_scope")
            if isinstance(scope, dict):
                radius_payload["edit_plan_blast_radius_scope"] = dict(scope)
    if edit_anchor_symbol is not None and radius_payload is not None:
        dependent_matches = _repo_map._ordered_dependent_file_matches(
            radius_payload,
            primary_file=edit_anchor_file,
            max_depth=max_depth,
        )
        dependent_files = [str(current["path"]) for current in dependent_matches]
        related_spans, caller_symbol_count = _repo_map._related_spans_from_blast_radius(
            repo_map,
            radius_payload,
            primary_symbol=edit_anchor_symbol,
            dependent_matches=dependent_matches,
        )
        rollback_risk = _repo_map._rollback_risk_from_blast_radius(
            dependent_matches=dependent_matches,
            caller_symbol_count=caller_symbol_count,
            associated_test_count=len(radius_payload.get("tests", payload.get("tests", []))),
            max_depth=max_depth,
        )
    dependency_trust = _repo_map._dependency_trust(repo_map, dependent_files)
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _repo_map._ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _repo_map._coverage_summary(payload)))
    suggested_edit_primary_symbol = edit_anchor_symbol or primary_symbol
    suggested_edit_definitions = (
        list(radius_payload.get("definitions", []))
        if radius_payload is not None
        else ([suggested_edit_primary_symbol] if suggested_edit_primary_symbol is not None else [])
    )
    # #642 gate nit-1 fast-follow: this IS the SECOND validation-plan chain the gate named (the
    # FIRST -- _discover_validation_tests_for_primary_file above -- already threads deadline_
    # monotonic/deadline_hit). Reuse this function's own already-threaded `deadline_hit` flag (not
    # a fresh one) so an overrun HERE folds into the SAME `edit_plan_seed_deadline_hit` union
    # `_attach_edit_plan_metadata` already checks -- no new fold-in site needed.
    validation_plan, validation_alignment = _repo_map._validation_plan_and_alignment_for_tests(
        validation_tests,
        repo_root=payload.get("path", "."),
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        primary_file=str(primary_file) if primary_file is not None else None,
        query=query,
        precomputed_file_paths=validation_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    validation_commands = [str(step["command"]) for step in validation_plan]
    # Additive, unverified suggestion (test-neighbor filename probe) â€” NEVER merged into the
    # strict, evidence-gated `validation_commands`/`validation_plan` above. See
    # `_suggested_validation_command_for_primary_file`.
    suggested_validation_command = (
        _repo_map._suggested_validation_command_for_primary_file(
            primary_file,
            validation_root if validation_root is not None else payload.get("path", "."),
        )
        if primary_file is not None
        else None
    )
    suggested_validation_commands = (
        [suggested_validation_command] if suggested_validation_command is not None else []
    )
    confidence = {
        "file": _repo_map._confidence_from_score(int(primary_file_match.get("score", 0))),
        "symbol": _repo_map._confidence_from_score(int(primary_symbol.get("score", 0)))
        if primary_symbol is not None
        else 0.0,
        "test": _repo_map._confidence_from_score(int(primary_test_match.get("score", 0))),
    }
    if int(validation_alignment.get("filtered_count", 0) or 0) > 0:
        confidence = {key: round(min(float(value), 0.65), 3) for key, value in confidence.items()}
    confidence["overall"] = _repo_map._derive_seed_overall(confidence)

    return {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _repo_map._primary_span_for_symbol(primary_symbol),
        "primary_test": primary_test,
        "validation_tests": validation_tests,
        "validation_commands": validation_commands,
        "validation_plan": validation_plan,
        "validation_alignment": validation_alignment,
        "suggested_validation_commands": suggested_validation_commands,
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": confidence,
        "related_spans": related_spans,
        "suggested_edits": _repo_map._suggested_edits_from_related_spans(
            related_spans,
            primary_symbol=suggested_edit_primary_symbol,
            definitions=suggested_edit_definitions,
            callers=list(radius_payload.get("callers", [])) if radius_payload is not None else [],
            repo_root=_repo_map._resolved_input_path(str(repo_map["path"])),
            # Opt-in only (audit B9/A18 fix scope, broadened by #212): `suggested_edits_max` defaults
            # to `None` (unbounded, byte-identical to every UNNAMED caller's pre-fix behavior) unless
            # the caller of `_build_edit_plan_seed` explicitly requests a bound. `tg edit-plan`'s own
            # builder (`build_context_edit_plan_from_map`) opted in first (B9/A18); `tg context-render`
            # (which also has its own separate, pre-existing downstream cap for the compact/llm
            # profiles only -- see `_compact_edit_plan_seed` -- but none for the default "full"
            # profile) and `tg blast-radius-plan`/`tg blast-radius-render` (which had no cap at all,
            # in any profile) now ALSO opt in (#212, a follow-up to B9/#661), each passing its own
            # `--max-files` value through `_attach_edit_plan_metadata`. Any future caller that omits
            # `suggested_edits_max` still gets the unbounded pre-fix shape.
            max_edits=suggested_edits_max,
        ),
        "dependency_trust": dependency_trust,
        "plan_trust_summary": _repo_map._plan_trust_summary(
            ranking_quality,
            coverage_summary,
            dependency_trust,
        ),
        "dependent_files": dependent_files,
        "edit_ordering": _repo_map._deterministic_edit_ordering(
            edit_anchor_file or (str(primary_file) if primary_file is not None else None),
            dependent_files,
            [str(current) for current in payload.get("tests", [])],
        ),
        "rollback_risk": rollback_risk,
        "blast_radius_scope": dict(radius_payload.get("edit_plan_blast_radius_scope", {}))
        if isinstance(radius_payload, dict)
        else {},
    }


def _sorted_ranked_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(symbols, key=_repo_map._symbol_rank_key)


def _attach_edit_plan_headline_aliases(payload: dict[str, Any]) -> None:
    navigation_pack = payload.get("navigation_pack")
    edit_plan_seed = payload.get("edit_plan_seed")
    if not isinstance(navigation_pack, dict) or not isinstance(edit_plan_seed, dict):
        return
    primary_target = navigation_pack.get("primary_target")
    if isinstance(primary_target, dict) and primary_target:
        payload["primary_target"] = dict(primary_target)
    edit_order = edit_plan_seed.get("edit_ordering") or navigation_pack.get("edit_ordering")
    if isinstance(edit_order, list):
        payload["edit_order"] = [str(item) for item in edit_order]
    payload["plan"] = {
        "query": str(payload.get("query", "")),
        "primary_file": edit_plan_seed.get("primary_file"),
        "primary_symbol": dict(edit_plan_seed.get("primary_symbol", {}))
        if isinstance(edit_plan_seed.get("primary_symbol"), dict)
        else edit_plan_seed.get("primary_symbol"),
        "primary_span": dict(edit_plan_seed.get("primary_span", {}))
        if isinstance(edit_plan_seed.get("primary_span"), dict)
        else edit_plan_seed.get("primary_span"),
        "edit_order": list(payload.get("edit_order", [])),
        "validation_commands": list(edit_plan_seed.get("validation_commands", [])),
        "suggested_validation_commands": [
            dict(current)
            for current in edit_plan_seed.get("suggested_validation_commands", []) or []
            if isinstance(current, dict)
        ],
        "rollback_risk": edit_plan_seed.get("rollback_risk"),
        "ranking_quality": str(payload.get("ranking_quality", "")),
    }


def _attach_edit_plan_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
    max_depth: int = _repo_map._DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
    suggested_edits_max: int | None = None,
) -> dict[str, Any]:
    with _repo_map._profiling_phase(_profiling_collector, "edit_plan_assembly"):

        def _ordered_candidate_files(
            files: list[str],
            primary_file: str | None,
            *,
            limit: int,
        ) -> list[str]:
            if primary_file is None:
                return files[:limit]
            ordered = [current for current in files if current != primary_file]
            return [primary_file, *ordered][:limit]

        ranked_symbols = _repo_map._sorted_ranked_symbols(list(payload.get("symbols", [])))
        primary_payload_file = next(iter(payload.get("files", [])), None)
        payload_file_symbol = next(
            (
                current
                for current in ranked_symbols
                if primary_payload_file is not None
                and str(current.get("file")) == str(primary_payload_file)
            ),
            None,
        )
        primary_payload_file_match: dict[str, Any] = next(
            (
                match
                for match in payload.get("file_matches", [])
                if primary_payload_file is not None
                and str(match.get("path")) == str(primary_payload_file)
            ),
            {},
        )
        edit_anchor_symbol = _repo_map._preferred_edit_anchor_symbol(
            payload_file_symbol, ranked_symbols
        )
        resolved_blast_radius_payload = blast_radius_payload
        if (
            edit_anchor_symbol is not None
            and resolved_blast_radius_payload is None
            and _repo_map._should_build_edit_plan_blast_radius(
                edit_anchor_symbol,
                primary_payload_file_match,
            )
        ):
            edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
            if edit_symbol_name:
                with _repo_map._profiling_phase(_profiling_collector, "edit_plan_blast_radius"):
                    radius_repo_map = _repo_map._scoped_repo_map_for_edit_plan_blast_radius(
                        repo_map,
                        payload,
                        edit_symbol_name,
                        max_files=max_files,
                    )
                    resolved_blast_radius_payload = _repo_map.build_symbol_blast_radius_from_map(
                        radius_repo_map,
                        edit_symbol_name,
                        max_depth=max_depth,
                        _profiling_collector=_profiling_collector,
                    )
                    scope = radius_repo_map.get("edit_plan_blast_radius_scope")
                    if isinstance(scope, dict):
                        resolved_blast_radius_payload["edit_plan_blast_radius_scope"] = dict(scope)
        with _repo_map._profiling_phase(_profiling_collector, "edit_plan_seed"):
            # #639 review nit 1 (dogfood #1 RESIDUAL): the validation-file discovery this seed
            # runs (_discover_validation_tests_for_primary_file -> _precomputed_validation_files_
            # for_root) does a per-file Path.resolve() pass over the repo map's file list -- a
            # pre-existing UNBOUNDED cost (see that function's own docstring) that let a `tg agent
            # --deadline` request overrun in this tail even after the checkpointed scan + pack
            # stage finished in budget. Own a flag here (mirrors build_context_pack_from_map's own
            # `own_deadline_hit` pattern) and fold an early break into `payload["partial"]` below,
            # same "any one sibling stage" fold-in shape every other deadline-scoped seam uses.
            edit_plan_seed_deadline_hit = _repo_map._DeadlineBreakFlag()
            payload["edit_plan_seed"] = _repo_map._build_edit_plan_seed(
                repo_map,
                payload,
                ranked_symbols=ranked_symbols,
                query=query,
                max_files=max_files,
                max_depth=max_depth,
                blast_radius_payload=resolved_blast_radius_payload,
                semantic_provider=semantic_provider,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=edit_plan_seed_deadline_hit,
                suggested_edits_max=suggested_edits_max,
            )
            if edit_plan_seed_deadline_hit.hit:
                payload["partial"] = True
                # setdefault, not overwrite: never clobber a richer deadline_limit already present
                # from the SCAN stage or build_context_pack_from_map's own self-stamp.
                _repo_map._stamp_partial_status(payload, deadline_exceeded=True, emit_reason=False)
        payload["graph_trust_summary"] = (
            dict(resolved_blast_radius_payload.get("graph_trust_summary", {}))
            if resolved_blast_radius_payload is not None
            else _repo_map._graph_trust_summary([])
        )
        primary_file = str(payload["edit_plan_seed"].get("primary_file") or "") or None
        payload["candidate_edit_targets"] = {
            "files": _ordered_candidate_files(
                list(payload.get("files", [])),
                primary_file,
                limit=max_files,
            ),
            # Widen the candidate-edit symbol POOL beyond the render cap so a query-relevant
            # implementation symbol cannot be crowded entirely out of `candidate_edit_targets`
            # by same-tier symbols from a large file (Task #4 / agent-capsule capability). The
            # token-bearing `payload["symbols"]` stays at `max_symbols`; only the alternative
            # pool widens, and the capsule's marker-helper swap can then promote the impl.
            "symbols": ranked_symbols[: max(max_symbols, 8)],
            "tests": list(payload.get("tests", []))[:max_files],
            "ranking_quality": str(
                payload.get(
                    "ranking_quality",
                    _repo_map._ranking_quality(
                        list(payload.get("file_matches", [])),
                        list(payload.get("test_matches", [])),
                    ),
                )
            ),
            "coverage_summary": dict(
                payload.get("coverage_summary", _repo_map._coverage_summary(payload))
            ),
            "spans": _repo_map._candidate_edit_spans(
                primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
                primary_file_match={},
                related_spans=[],
                max_spans=max(max_files, max_symbols),
            ),
        }
        primary_file_match = next(
            (
                match
                for match in payload.get("file_matches", [])
                if str(match.get("path")) == str(primary_file)
            ),
            payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
        )
        payload["candidate_edit_targets"]["spans"] = _repo_map._candidate_edit_spans(
            primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
            primary_file_match=primary_file_match,
            related_spans=list(payload["edit_plan_seed"].get("related_spans", [])),
            max_spans=max(max_files, max_symbols),
        )
        with _repo_map._profiling_phase(_profiling_collector, "edit_plan_navigation_pack"):
            payload["navigation_pack"] = _repo_map._navigation_pack(
                repo_map,
                payload,
                max_reads=max(max_files, max_symbols),
            )
        _repo_map._attach_edit_plan_headline_aliases(payload)
    return payload


def _attach_lightweight_navigation_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> dict[str, Any]:
    def _ordered_candidate_files(
        files: list[str],
        primary_file: str | None,
        *,
        limit: int,
    ) -> list[str]:
        if primary_file is None:
            return files[:limit]
        ordered = [current for current in files if current != primary_file]
        return [primary_file, *ordered][:limit]

    ranked_symbols = _repo_map._sorted_ranked_symbols(list(payload.get("symbols", [])))
    primary_symbol = next(iter(ranked_symbols), None)
    primary_file = next(iter(payload.get("files", [])), None)
    if primary_symbol is not None:
        preferred_files = _repo_map._preferred_definition_files(
            repo_map, str(primary_symbol.get("name", ""))
        )
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)

    payload["edit_plan_seed"] = {}
    payload["edit_plan_seed_skipped"] = True
    payload["graph_trust_summary"] = _repo_map._graph_trust_summary([])
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _repo_map._ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _repo_map._coverage_summary(payload)))
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    validation_plan, validation_alignment = _repo_map._validation_plan_and_alignment_for_tests(
        [],
        repo_root=payload.get("path", repo_map.get("path", ".")),
        primary_test=None,
        primary_symbol=primary_symbol,
        primary_file=primary_file,
        query=query,
        precomputed_file_paths=_repo_map._repo_map_validation_file_paths(
            repo_map,
            validation_root=(
                _repo_map._validation_repo_root(
                    _repo_map._resolved_input_path(str(primary_file)).parent
                )
                if primary_file
                else None
            ),
        ),
        # #642 gate nit-1 fast-follow : this is the render's `include_edit_plan_seed=
        # False` lightweight-navigation sibling of _build_edit_plan_seed's own validation-plan call
        # above -- no LIVE caller currently sets include_edit_plan_seed=False, so this path is
        # unreachable today (the return-time backstop below would already catch it either way),
        # but thread deadline through it too so it stays actually bounded rather than merely
        # backstopped if a future caller ever does take this branch.
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    lightweight_suggested_validation_command = (
        _repo_map._suggested_validation_command_for_primary_file(
            primary_file, payload.get("path", ".")
        )
        if primary_file
        else None
    )
    seed_confidence: dict[str, Any] = {
        "file": _repo_map._confidence_from_score(int(primary_file_match.get("score", 0))),
        "symbol": _repo_map._confidence_from_score(int(primary_symbol.get("score", 0)))
        if primary_symbol is not None
        else 0.0,
        "test": 0.0,
    }
    seed_confidence["overall"] = _repo_map._derive_seed_overall(seed_confidence)
    lightweight_seed = {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _repo_map._primary_span_for_symbol(primary_symbol),
        "primary_test": None,
        "validation_tests": [],
        "validation_commands": [str(step["command"]) for step in validation_plan],
        "validation_plan": validation_plan,
        "validation_alignment": validation_alignment,
        "suggested_validation_commands": (
            [lightweight_suggested_validation_command]
            if lightweight_suggested_validation_command is not None
            else []
        ),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": seed_confidence,
        "related_spans": [],
        "suggested_edits": [],
        "dependency_trust": _repo_map._dependency_trust(repo_map, []),
        "plan_trust_summary": _repo_map._plan_trust_summary(
            ranking_quality,
            coverage_summary,
            _repo_map._dependency_trust(repo_map, []),
        ),
        "dependent_files": [],
        "edit_ordering": _repo_map._deterministic_edit_ordering(
            str(primary_file) if primary_file is not None else None,
            [],
            [],
        ),
        "rollback_risk": 0.0,
    }
    lightweight_primary_symbol = dict(primary_symbol) if isinstance(primary_symbol, dict) else None
    payload["candidate_edit_targets"] = {
        "files": _ordered_candidate_files(
            list(payload.get("files", [])),
            str(primary_file) if primary_file is not None else None,
            limit=max_files,
        ),
        # Widen the candidate-edit symbol pool beyond the render cap (Task #4 / agent-capsule
        # capability) â€” same rationale as build_context_render: don't let a large file's same-tier
        # symbols crowd a query-relevant implementation out of the candidate pool.
        "symbols": ranked_symbols[: max(max_symbols, 8)],
        "tests": list(payload.get("tests", []))[:max_files],
        "ranking_quality": ranking_quality,
        "coverage_summary": coverage_summary,
        "spans": _repo_map._candidate_edit_spans(
            primary_symbol=lightweight_primary_symbol,
            primary_file_match={},
            related_spans=[],
            max_spans=max(max_files, max_symbols),
        ),
    }
    payload["candidate_edit_targets"]["spans"] = _repo_map._candidate_edit_spans(
        primary_symbol=lightweight_primary_symbol,
        primary_file_match=primary_file_match,
        related_spans=[],
        max_spans=max(max_files, max_symbols),
    )
    navigation_payload = dict(payload)
    navigation_payload["edit_plan_seed"] = lightweight_seed
    payload["navigation_pack"] = _repo_map._navigation_pack(
        repo_map,
        navigation_payload,
        max_reads=max(max_files, max_symbols),
    )
    return payload


def build_context_edit_plan(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    """``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline, used AS-IS instead of being recomputed from
    ``deadline_seconds`` when supplied. The CLI cold path (``main.edit_plan``) anchors it at
    command entry, before path resolution and the daemon gate, so front-door time is budgeted the
    same way scan time already is. Existing ``deadline_seconds``-only callers are unaffected: the
    fallback computation below is byte-identical to the prior behavior."""
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    # CLI consistency fix : `--deadline` used to be undefined on `tg edit-plan`
    # (Click "No such option" exit-2). Converted ONCE (capability P0-6 step-3 pattern) and shared across
    # the repo-map build AND edit-plan's own symbol-scoring pass in `_from_map` below.
    if deadline_monotonic is None:
        deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    return _repo_map.build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_symbols=max_symbols,
        max_sources=max_sources,
        max_tokens=max_tokens,
        semantic_provider=semantic_provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )


def build_context_edit_plan_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_monotonic: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    payload = _repo_map.build_context_pack_from_map(
        repo_map,
        query,
        _test_source_limit=max_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    normalized_max_sources = (
        max(1, max_sources) if max_sources is not None else normalized_max_symbols
    )
    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    payload["routing_reason"] = "context-edit-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = [
        {
            **dict(summary),
            "symbols": list(dict(summary).get("symbols", []))[:normalized_max_symbols],
        }
        for summary in list(payload.get("file_summaries", []))[:normalized_max_files]
        if isinstance(summary, dict)
    ]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _repo_map._sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    selected_file_set = {str(current) for current in payload["files"]}
    selected_test_set = {str(current) for current in payload["tests"]}
    payload["imports"] = [
        {
            **dict(entry),
            "imports": list(dict(entry).get("imports", []))[:normalized_max_symbols],
        }
        for entry in payload.get("imports", [])
        if isinstance(entry, dict) and str(entry.get("file", "")) in selected_file_set
    ][:normalized_max_files]
    payload["related_paths"] = [
        str(path)
        for path in payload.get("related_paths", [])
        if str(path) in selected_file_set | selected_test_set
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload["max_sources"] = normalized_max_sources
    payload["max_tokens"] = normalized_max_tokens
    payload["semantic_provider"] = _repo_map._normalize_semantic_provider(semantic_provider)
    payload = _repo_map._attach_edit_plan_metadata(
        repo_map,
        payload,
        query=query,
        max_files=normalized_max_files,
        max_symbols=min(normalized_max_symbols, normalized_max_sources),
        semantic_provider=semantic_provider,
        # #642 gate nit-1 fast-follow: this call dropped deadline_monotonic entirely (distinct from
        # the SECOND-validation-plan-chain gap the #642 gate itself flagged) -- `tg edit-plan` never
        # threaded a deadline into the edit-plan-seed's validation-test discovery or validation-plan
        # chain at all, independent of the return-time backstop below. build_context_render_from_
        # map's own call to this same helper already passes deadline_monotonic; mirror it here.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
        # audit B9/A18: `tg edit-plan --json --max-files N` wired `max_edits=max_files` into
        # `_suggested_edits_from_related_spans` but the callee never read it, so `suggested_edits`
        # grew unbounded regardless of `--max-files`. This is the ONE opt-in call site for the new
        # `suggested_edits_max` bound -- edit-plan's own top-level builder, never shared with
        # context-render or blast-radius-plan/render (see `_build_edit_plan_seed`'s call comment).
        suggested_edits_max=normalized_max_files,
    )
    payload["validation_commands"] = _repo_map._top_level_validation_commands(payload)
    payload["suggested_validation_commands"] = _repo_map._top_level_suggested_validation_commands(
        payload
    )
    # Parity fix (v1.71.1 dogfood): `tg agent --json` already surfaces a top-level structured
    # `validation_plan`; edit-plan only had the flat `validation_commands` above even though the
    # structured steps already exist at `edit_plan_seed.validation_plan`. Purely additive -- does
    # not change `validation_commands`/`suggested_validation_commands` above.
    payload["validation_plan"] = _repo_map._top_level_validation_plan(payload)
    # Parity fix : `tg agent --json` already surfaces a top-level `confidence`
    # (float-bearing object) and `ask_user_before_editing` gate; edit-plan had neither (`confidence`
    # read as `null`, `ask_user_before_editing` was absent). Purely additive -- see
    # `_edit_plan_confidence_and_ask`'s docstring for exactly what is/isn't reused from agent.
    payload["confidence"], payload["ask_user_before_editing"] = (
        _repo_map._edit_plan_confidence_and_ask(
            payload,
            query=query,
        )
    )
    # #642 gate nit-1 fast-follow: mirrors build_context_render_from_map's own return-time
    # catch-all (added by this same PR) verbatim -- this function is `tg edit-plan`'s OWN builder
    # (build_context_edit_plan -> build_context_edit_plan_from_map), a sibling of build_context_
    # render_from_map, not a caller of it, so it needs its own independent recheck.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic
    )
    _repo_map._stamp_partial_status(payload, deadline_exceeded=deadline_exceeded_at_return)
    return _repo_map._attach_profiling(payload, collector)


def build_context_edit_plan_json(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_context_edit_plan(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_symbols=max_symbols,
            max_sources=max_sources,
            max_tokens=max_tokens,
            semantic_provider=semantic_provider,
            profile=profile,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )
