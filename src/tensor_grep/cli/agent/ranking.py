from __future__ import annotations

import re
from typing import Any

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_graph as _repo_map_graph


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        normalized = raw_token.lower()
        for candidate in [normalized, *_repo_map.split_terms(raw_token)]:
            if candidate and candidate not in terms:
                terms.append(candidate)
            for synonym in _repo_map._QUERY_TERM_SYNONYMS.get(candidate, ()):
                if synonym not in terms:
                    terms.append(synonym)
    return terms


def _symbol_query_terms(query: str) -> list[str]:
    expanded_terms: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        normalized = raw_token.lower()
        if normalized and normalized not in expanded_terms:
            expanded_terms.append(normalized)
        for synonym in _repo_map._QUERY_TERM_SYNONYMS.get(normalized, ()):
            if synonym not in expanded_terms:
                expanded_terms.append(synonym)
        if "_" in raw_token:
            continue
        for term in _repo_map.split_terms(raw_token):
            if term not in expanded_terms:
                expanded_terms.append(term)
            for synonym in _repo_map._QUERY_TERM_SYNONYMS.get(term, ()):
                if synonym not in expanded_terms:
                    expanded_terms.append(synonym)
    return expanded_terms


def _query_language_hints(query: str) -> list[str]:
    hints: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        language = _repo_map._QUERY_LANGUAGE_ALIASES.get(raw_token.lower())
        if language is not None and language not in hints:
            hints.append(language)
    return hints


def _target_language_for_path(path: str | _repo_map.Path | None) -> str | None:
    if path is None:
        return None
    suffix = _repo_map.Path(str(path)).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in _repo_map._TS_SUFFIXES:
        return "typescript"
    if suffix in _repo_map._JS_TS_SUFFIXES:
        return "javascript"
    if suffix in _repo_map._RUST_SUFFIXES:
        return "rust"
    if suffix == ".go":
        # MOST-FORGOTTEN seam (PATH A Stage 1 design note): without this, the capsule's
        # query-language-vs-target-language 0.55 confidence cap (agent_capsule.py) never even
        # sees "go" as a candidate target language, so it can silently misfire on Go targets --
        # e.g. treating a Go primary file as having "no target language" instead of correctly
        # reporting primary_target_language == "go".
        return "go"
    if suffix in _repo_map._JAVA_SUFFIXES:
        # Same MOST-FORGOTTEN seam, Stage 2: without this, `tg agent`'s capsule never reports
        # primary_target_language == "java" for a Java target.
        return "java"
    if suffix == ".php":
        # MOST-FORGOTTEN seam (see the ".go" branch above) -- same fix, same reason, for PHP's
        # Stage 1 registration.
        return "php"
    if suffix == ".cs":
        # Same MOST-FORGOTTEN seam, now for C# (PATH A Stage 1, second expansion).
        return "csharp"
    if suffix == ".c":
        # Same MOST-FORGOTTEN seam, now for C (PATH A Stage 3, top-10 language campaign). Note
        # ".h" is deliberately absent here -- lang_cpp.py (below) is the registered owner of
        # every C/C++ header suffix (see lang_c.py's module docstring's header-ambiguity note);
        # this branch must match ONLY the suffixes lang_c.py's LanguageSpec actually registers.
        return "c"
    if suffix in _repo_map._CPP_SUFFIXES:
        # Same MOST-FORGOTTEN seam, now for C++ (PATH A Stage 3, Phase 2 -- closes the top-10
        # language campaign to 10/10). Must match lang_cpp.py's LanguageSpec.suffixes AND
        # _provider_language_for_path's pre-existing "cpp" assignment exactly, or
        # test_target_and_provider_language_agree_with_registry fails.
        return "cpp"
    return None


def _path_matches_query_language_hints(path: str | _repo_map.Path, hints: list[str]) -> bool:
    language = _repo_map._target_language_for_path(path)
    return bool(language is not None and language in hints)


def _query_file_name_hint_score(path: str | _repo_map.Path, query: str) -> int:
    query_lower = query.lower()
    path_obj = _repo_map.Path(str(path))
    name = path_obj.name.lower()
    stem = path_obj.stem.lower()
    if name and name in query_lower:
        return 24
    if stem and re.search(rf"\b{re.escape(stem)}\b", query_lower):
        return 12
    return 0


def _score_text_terms(text: str, terms: list[str]) -> int:
    haystack = text.lower()
    haystack_terms = set(_repo_map.split_terms(text))
    score = 0
    for term in terms:
        normalized = term.lower()
        if len(normalized) <= 3:
            if normalized in haystack_terms:
                score += 1
        elif normalized in haystack or normalized in haystack_terms:
            score += 1
    return score


def _is_cli_command_module_path(file_path: str) -> bool:
    """No-I/O, path-only check: does ``file_path`` live under a ``cli/`` package? Necessary but
    NOT sufficient for "thin CLI dispatcher" (task #250) -- see
    ``_thin_cli_dispatcher_call_targets``, which additionally requires the specific symbol to
    carry a Typer/Click ``.command(...)`` registration decorator AND a provable call-through
    before a ``cli/`` symbol is ever treated as a pass-through wrapper rather than genuine logic
    (e.g. ``tg search``'s own flag-parsing in ``cli/main.py`` IS the real implementation for "add
    a --flag to tg search" and must never be demoted by this check)."""
    if not file_path.lower().endswith(".py"):
        return False
    try:
        parts = {part.lower() for part in _repo_map.Path(file_path).parts}
    except (OSError, ValueError):
        return False
    return "cli" in parts


def _thin_cli_dispatcher_call_targets(
    file_path: str, symbol_name: str, *, expected_line: int | None = None
) -> set[str] | None:
    """Task #250: does ``symbol_name`` (defined in ``file_path``) look like a GENUINELY THIN
    Typer/Click command dispatcher -- decorated with a ``.command(...)`` registration AND small
    enough on both axes below to be structurally a passthrough, not a real implementation -- and
    if so, what names does its body call?

    Returns ``None`` when the file can't be parsed, the symbol isn't a function/method, it is NOT
    decorated as a command, OR it exceeds ``_THIN_DISPATCHER_MAX_BODY_STATEMENTS`` top-level body
    statements (docstring excluded) or ``_THIN_DISPATCHER_MAX_CALL_TARGETS`` distinct callee
    names -- i.e. not provably a THIN dispatcher, so the caller must leave the primary target
    alone. This is what protects a genuine ``cli/main.py`` target, e.g. ``search_command`` (see
    the constants' docstring above) or "add a --flag to tg search", from ever being demoted: a
    big command is rejected here regardless of whether one of its many callees happens to match an
    alternative candidate's name. Returns the -- possibly empty -- set of names the (small) body
    calls otherwise; the caller intersects this against alternative-candidate symbol names to find
    the SPECIFIC implementation a dispatcher hands off to (e.g. ``ledger_claim``'s body calling
    ``ledger_store.submit_claim(...)``).

    Reuses ``_read_source_text_cached``/``_cached_ast_parse`` (both content-addressed -- a cache
    HIT, not a re-read/re-parse, for a file already scanned earlier in this same process) and
    bounds the walk to the ONE matched function's own subtree: a structural re-check of a symbol
    already selected as the primary-target candidate, not a new repo-wide scan.
    """
    if not _repo_map._is_cli_command_module_path(file_path):
        return None
    try:
        source = _repo_map._read_source_text_cached(file_path)
        tree = _repo_map._cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError, RecursionError):
        return None

    matches = [
        node
        for node in _repo_map.ast.walk(tree)
        if isinstance(node, (_repo_map.ast.FunctionDef, _repo_map.ast.AsyncFunctionDef))
        and node.name == symbol_name
    ]
    if not matches:
        return None
    if len(matches) > 1 and expected_line is not None:
        matches.sort(key=lambda node: abs(node.lineno - expected_line))
    match = matches[0]

    decorator_names: set[str] = set()
    for decorator in match.decorator_list:
        target = decorator.func if isinstance(decorator, _repo_map.ast.Call) else decorator
        if isinstance(target, _repo_map.ast.Attribute):
            decorator_names.add(target.attr)
        elif isinstance(target, _repo_map.ast.Name):
            decorator_names.add(target.id)
    if "command" not in decorator_names:
        return None

    # Structural thinness gate (NIT-1): count top-level body statements, excluding a leading
    # docstring (boilerplate that scales with how well-documented a command is, not with its
    # implementation size) so a thoroughly-documented thin dispatcher isn't unfairly penalized.
    body = match.body
    if body and _repo_map.ast.get_docstring(match, clean=False) is not None:
        body = body[1:]
    if len(body) > _repo_map._THIN_DISPATCHER_MAX_BODY_STATEMENTS:
        return None

    called_names: set[str] = set()
    for call_node in _repo_map.ast.walk(match):
        if not isinstance(call_node, _repo_map.ast.Call):
            continue
        func = call_node.func
        if isinstance(func, _repo_map.ast.Name):
            called_names.add(func.id)
        elif isinstance(func, _repo_map.ast.Attribute):
            called_names.add(func.attr)
    if len(called_names) > _repo_map._THIN_DISPATCHER_MAX_CALL_TARGETS:
        return None
    return called_names


def _symbol_lookup_key(text: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9]+", text)).lower()


def _symbol_name_matches_query_exactly(symbol_name: str, query: str) -> bool:
    normalized_name = symbol_name.strip()
    normalized_query = query.strip()
    if not normalized_name:
        return False
    if normalized_name == normalized_query:
        return True
    return any(
        normalized_name == token and _repo_map._is_distinctive_identifier(token)
        for token in re.findall(r"[A-Za-z0-9_]+", query)
    )


def _symbol_name_matches_query_bridge(symbol_name: str, query: str) -> bool:
    symbol_key = _repo_map._symbol_lookup_key(symbol_name)
    if not symbol_key:
        return False
    if symbol_key == _repo_map._symbol_lookup_key(query):
        return not _repo_map._symbol_name_matches_query_exactly(symbol_name, query)
    return any(
        symbol_key == _repo_map._symbol_lookup_key(token)
        for token in _repo_map._query_terms(query)
        if symbol_name != token
    )


def _symbol_name_terms_cover_query(symbol_name: str, terms: list[str]) -> bool:
    symbol_terms = set(_repo_map.split_terms(symbol_name))
    meaningful_query_terms = {term for term in terms if len(term) > 2}
    return bool(len(symbol_terms) >= 2 and symbol_terms <= meaningful_query_terms)


def _score_file_path(path: str, terms: list[str]) -> int:
    path_obj = _repo_map.Path(path)
    path_parts = [
        part
        for part in path_obj.parts
        if part and part not in {path_obj.anchor, path_obj.drive, _repo_map.os.sep, "/"}
    ]
    score_root_markers = {
        "src",
        "tests",
        "test",
        "rust_core",
        "crates",
        "packages",
        "apps",
        "docs",
        "scripts",
        "benchmarks",
    }
    start_index = next(
        (index for index, part in enumerate(path_parts) if part.lower() in score_root_markers),
        max(0, len(path_parts) - 4),
    )
    repo_like_tail = " ".join(path_parts[start_index:])
    return _repo_map._score_text_terms(path_obj.name, terms) + _repo_map._score_text_terms(
        repo_like_tail, terms
    )


def _non_test_definition_names(symbols: list[dict[str, Any]]) -> frozenset[str]:
    """Task #254 heuristic 2 support: names with at least one definition OUTSIDE a test file.

    Query-independent (a structural fact about the scanned repo, not the search terms), so a
    caller computes this ONCE per scoring pass -- cheaply, no I/O, no re-parsing -- and reuses it
    for every `_score_symbol` call in that pass rather than re-deriving it per symbol.
    """
    return frozenset(
        str(symbol["name"])
        for symbol in symbols
        if symbol.get("name")
        and symbol.get("file")
        and not _repo_map._is_test_file(_repo_map.Path(str(symbol["file"])))
    )


def _symbol_name_exact_boundary_bonus(symbol_name: str, terms: list[str]) -> int:
    """Task #254 heuristic 3: +1 when a query term matches ``symbol_name`` as a clean,
    word-boundary-respecting token (a member of ``split_terms(symbol_name)``) rather than only
    through ``_score_text_terms``'s looser RAW-SUBSTRING fallback (a long term merely embedded
    inside a longer, differently-tokenized identifier -- e.g. term "underscore" inside a name
    like "my_underscored_value"). Standard IR signal: an exact token match is stronger evidence of
    relevance than mere containment. Restricted to ``len(term) > 3`` to mirror
    ``_score_text_terms``'s own threshold -- a term of length <= 3 is ALREADY boundary-only there
    (it only ever checks ``haystack_terms``), so there is no substring laxity to correct for short
    terms. Deliberately additive and capped at +1 total (not per matching term) to keep the delta
    small relative to the existing scoring scale -- this refines ORDER among candidates that
    already match; it never changes WHICH symbols match (the base credit from
    ``_score_text_terms`` already covers both the clean-token and substring cases).
    """
    name_terms = set(_repo_map.split_terms(symbol_name))
    return 1 if any(len(term) > 3 and term.lower() in name_terms for term in terms) else 0


def _score_symbol(
    symbol: dict[str, Any],
    terms: list[str],
    *,
    non_test_definition_names: frozenset[str] | None = None,
) -> int:
    symbol_name = str(symbol["name"])
    score = (
        _repo_map._score_text_terms(symbol_name, terms) * 3
        + _repo_map._score_text_terms(str(symbol["kind"]), terms)
        + _repo_map._score_file_path(str(symbol["file"]), terms)
    )
    if _repo_map._symbol_name_terms_cover_query(symbol_name, terms):
        score += 2
    score += _repo_map._symbol_name_exact_boundary_bonus(symbol_name, terms)
    if (
        non_test_definition_names is not None
        and symbol_name in non_test_definition_names
        and _repo_map._is_test_file(_repo_map.Path(str(symbol["file"])))
    ):
        # Task #254 heuristic 2: a test-file hit sinks below a same-named non-test
        # implementation instead of competing with it on equal footing -- floored at 0 so a
        # borderline match is never pushed to a misleading negative score.
        score = max(0, score - _repo_map._TEST_SHADOW_PENALTY)
    return score


def _score_import_entry(entry: dict[str, Any], terms: list[str]) -> int:
    imports_joined = " ".join(str(item) for item in entry["imports"])
    return (
        _repo_map._score_file_path(str(entry["file"]), terms)
        + _repo_map._score_text_terms(imports_joined, terms) * 2
    )


def _score_file_source_terms(path: str, terms: list[str]) -> int:
    try:
        source = _repo_map.lang_registry.read_source_text(_repo_map.Path(path))
    except OSError:
        return 0
    return _repo_map.score_term_overlap(terms, source)


def _append_reason(reason_map: dict[str, list[str]], path: str, reason: str) -> None:
    current = reason_map.setdefault(path, [])
    if reason not in current:
        current.append(reason)


def _provenance_from_reasons(reasons: list[str]) -> list[str]:
    provenance: list[str] = []
    for reason in reasons:
        if reason in {"definition", "symbol", "import", "caller"}:
            label = "parser-backed"
        elif reason in {"import-graph", "graph-depth", "graph-centrality", "test-graph"}:
            label = "graph-derived"
        elif reason == "framework-pattern":
            label = "framework-pattern"
        elif reason in {"filename", "path"}:
            label = "filename-convention"
        else:
            label = "heuristic"
        if label not in provenance:
            provenance.append(label)
    return provenance or ["heuristic"]


def _span_rationale(symbol_name: str, reasons: list[str], depth: int) -> str:
    reason_text = ", ".join(reasons) if reasons else "ranked relevance"
    if "caller" in reasons:
        return f"Selected {symbol_name} because it directly calls the target symbol and sits at depth {depth}."
    if "definition" in reasons:
        return f"Selected {symbol_name} because it contains the defining behavior for the ranked edit target."
    return f"Selected {symbol_name} because it is connected by {reason_text} at depth {depth}."


def _ranking_quality(
    file_matches: list[dict[str, Any]],
    test_matches: list[dict[str, Any]],
) -> str:
    candidates = [
        int(current.get("score", 0))
        for current in [*file_matches[:2], *test_matches[:1]]
        if current
    ]
    if not candidates:
        return "weak"
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else 0
    if top >= 10 and (top - runner_up) >= 3:
        return "strong"
    if top >= 5:
        return "moderate"
    return "weak"


def _reference_kind_counts(references: list[Any]) -> dict[str, int]:
    """Additive T1 aggregate: counts every ``references`` row by its ``ref_kind`` label.

    Always sums to ``len(references)`` -- ref_kind is additive-only (capability P0-T1), so this must
    never drift from a straight tally of what is already in the list. F21 fix: a non-dict row
    (defensive-only today -- every real producer emits dicts) used to hit a bare ``continue`` and
    silently vanish from the tally, breaking the sum-equals-``len`` invariant the docstring
    promises; it is now counted too (under its label-less default, "value"), same as a dict row
    missing the ``ref_kind`` key.
    """
    counts: dict[str, int] = {"call": 0, "import": 0, "type": 0, "field": 0, "value": 0}
    for item in references:
        if not isinstance(item, dict):
            counts["value"] += 1
            continue
        ref_kind = str(item.get("ref_kind", "value"))
        counts[ref_kind] = counts.get(ref_kind, 0) + 1
    return counts


def _coverage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = dict(payload.get("coverage", {}))
    language_scope = str(coverage.get("language_scope", ""))
    evidence_counts = {
        "parser_backed": 0,
        "graph_derived": 0,
        "heuristic": 0,
    }

    def add_label(label: str) -> None:
        normalized = label.strip().lower()
        if normalized in {"python-ast", "tree-sitter", "parser-backed"}:
            evidence_counts["parser_backed"] += 1
        elif normalized == "graph-derived":
            evidence_counts["graph_derived"] += 1
        elif normalized in {
            "regex-heuristic",
            "heuristic",
            "filename-convention",
            "framework-pattern",
        }:
            evidence_counts["heuristic"] += 1

    def add_from_value(value: Any) -> None:
        if isinstance(value, str):
            add_label(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add_label(item)
        elif isinstance(value, dict):
            provenance = value.get("provenance")
            if provenance is not None:
                add_from_value(provenance)

    for group_name in (
        "file_matches",
        "test_matches",
        "imports",
        "definitions",
        "references",
        "callers",
    ):
        for item in payload.get(group_name, []):
            if isinstance(item, dict):
                add_from_value(item.get("provenance"))
                if group_name == "test_matches":
                    add_from_value(item.get("association"))
    for item in payload.get("caller_tree", []):
        if isinstance(item, dict):
            add_from_value(item.get("provenance"))
    total_evidence = sum(evidence_counts.values())
    if total_evidence > 0:
        evidence_ratios = {
            key: round(value / total_evidence, 6) for key, value in evidence_counts.items()
        }
    else:
        evidence_ratios = {
            "parser_backed": 0.0,
            "graph_derived": 0.0,
            "heuristic": 0.0,
        }
    return {
        "language_scope": language_scope,
        "parser_backed_fields": [
            "defs",
            "source",
            "refs",
            "callers",
        ],
        "heuristic_fields": [
            "test-matching",
            "graph-ranking",
        ],
        "graph_completeness": str(payload.get("graph_completeness", "moderate")),
        "evidence_counts": evidence_counts,
        "evidence_ratios": evidence_ratios,
        "reference_kind_counts": _repo_map._reference_kind_counts(payload.get("references", [])),
    }


def _graph_trust_summary(
    caller_tree: list[dict[str, Any]],
    *,
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parser_backed = 0
    heuristic = 0
    provenance: list[str] = []
    max_confidence_rank = 0
    confidence_order = {"weak": 1, "moderate": 2, "strong": 3}
    for level in caller_tree:
        edge_summary = level.get("edge_summary", {})
        if not isinstance(edge_summary, dict):
            continue
        counts = edge_summary.get("evidence_counts", {})
        if isinstance(counts, dict):
            parser_backed += int(counts.get("parser_backed", 0))
            heuristic += int(counts.get("heuristic", 0))
        for label in edge_summary.get("provenance", []):
            if isinstance(label, str) and label not in provenance:
                provenance.append(label)
        confidence = str(edge_summary.get("confidence", "weak"))
        max_confidence_rank = max(max_confidence_rank, confidence_order.get(confidence, 1))
    rank_to_confidence = {value: key for key, value in confidence_order.items()}
    # Additive T1 capability closer: by_ref_kind lets a consumer see whether the blast-radius callers
    # are parser-backed CALL sites (strong evidence) vs type/field/value-only mentions
    # (moderate/weak) -- reuses the same ref_kind labels stamped on `calls` (payload["callers"]).
    by_ref_kind: dict[str, int] = {}
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        ref_kind = str(call.get("ref_kind", "call"))
        by_ref_kind[ref_kind] = by_ref_kind.get(ref_kind, 0) + 1
    return {
        "edge_kind": "reverse-import",
        "confidence": rank_to_confidence.get(max_confidence_rank, "weak"),
        "provenance": provenance or ["graph-derived"],
        "depth_count": len(caller_tree),
        "evidence_counts": {
            "parser_backed": parser_backed,
            "heuristic": heuristic,
            "by_ref_kind": by_ref_kind,
        },
    }


def _downgrade_graph_trust_summary_for_coverage_gaps(
    summary: dict[str, Any],
    resolution_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Additive downgrade: a non-empty ``resolution_gaps`` means part of the caller/reference
    universe was scanned with no parser-backed extractor, so the blast-radius trust summary
    should not read as more confident than that. Downgrades ``confidence`` by exactly one rung
    and stamps ``resolution_gaps_present`` -- never touches anything when gaps are empty, so
    the 4 currently-supported languages see byte-identical output."""
    if not resolution_gaps:
        return summary
    confidence_order = {"weak": 1, "moderate": 2, "strong": 3}
    rank_to_confidence = {value: key for key, value in confidence_order.items()}
    current_rank = confidence_order.get(str(summary.get("confidence", "weak")), 1)
    downgraded = dict(summary)
    downgraded["confidence"] = rank_to_confidence[max(1, current_rank - 1)]
    downgraded["resolution_gaps_present"] = True
    return downgraded


def _is_parser_backed_provenance(label: str) -> bool:
    return label.strip().lower() in {"python-ast", "tree-sitter", "parser-backed"}


def _is_heuristic_provenance(label: str) -> bool:
    return label.strip().lower() in {"regex-heuristic", "heuristic", "filename-convention"}


def _dependency_trust(
    repo_map: dict[str, Any],
    dependent_files: list[str],
) -> dict[str, Any]:
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get(
                "provenance", _repo_map._symbol_navigation_provenance_for_path(str(current["file"]))
            )
        )
        for current in repo_map.get("imports", [])
        if current.get("file")
    }
    parser_backed_count = 0
    heuristic_count = 0
    for current in dict.fromkeys(str(path) for path in dependent_files if path):
        provenance = import_provenance_by_file.get(current, "")
        if _repo_map._is_parser_backed_provenance(provenance):
            parser_backed_count += 1
        elif _repo_map._is_heuristic_provenance(provenance):
            heuristic_count += 1
    if parser_backed_count > 0 and heuristic_count == 0:
        import_resolution_quality = "strong"
    elif parser_backed_count > 0:
        import_resolution_quality = "moderate"
    else:
        import_resolution_quality = "weak"
    return {
        "import_resolution_quality": import_resolution_quality,
        "parser_backed_count": parser_backed_count,
        "heuristic_count": heuristic_count,
    }


def _plan_trust_summary(
    ranking_quality: str,
    coverage_summary: dict[str, Any],
    dependency_trust: dict[str, Any],
) -> str:
    evidence_counts = dict(coverage_summary.get("evidence_counts", {}))
    return (
        f"Ranking {ranking_quality}; coverage {coverage_summary.get('graph_completeness', 'moderate')} "
        f"(parser_backed={int(evidence_counts.get('parser_backed', 0))}, "
        f"graph_derived={int(evidence_counts.get('graph_derived', 0))}, "
        f"heuristic={int(evidence_counts.get('heuristic', 0))}); dependency trust "
        f"{dependency_trust.get('import_resolution_quality', 'weak')} "
        f"(parser_backed={int(dependency_trust.get('parser_backed_count', 0))}, "
        f"heuristic={int(dependency_trust.get('heuristic_count', 0))})."
    )


def _match_record(
    path: str,
    score: int,
    reasons: list[str],
    graph_score: float | None = None,
) -> dict[str, Any]:
    payload = {
        "path": path,
        "score": score,
        "reasons": list(reasons),
        "provenance": _repo_map._provenance_from_reasons(reasons),
    }
    if graph_score is not None:
        payload["graph_score"] = round(graph_score, 6)
    return payload


def _file_summaries(symbols: list[dict[str, Any]], ranked_files: list[str]) -> list[dict[str, Any]]:
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = symbols_by_file.setdefault(current_path, [])
        current_symbols.append({
            "name": str(symbol["name"]),
            "kind": str(symbol["kind"]),
            "line": int(symbol["line"]),
        })
    for current_symbols in symbols_by_file.values():
        current_symbols.sort(
            key=lambda item: (int(item["line"]), str(item["kind"]), str(item["name"]))
        )

    summaries: list[dict[str, Any]] = []
    for current in ranked_files:
        file_symbols = symbols_by_file.get(str(current), [])
        if not file_symbols:
            continue
        summaries.append({"path": str(current), "symbols": file_symbols})
    return summaries


def _source_tokens(source_files: list[str]) -> set[str]:
    return _repo_map_graph.source_tokens(source_files)
