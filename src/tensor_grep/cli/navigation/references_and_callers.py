from __future__ import annotations

import json
import re
from typing import Any, cast

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_graph as _repo_map_graph


def _dedupe_symbol_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for current in references:
        line = int(current.get("line", 0) or 0)
        end_line = int(current.get("end_line", line) or line)
        key = (
            str(current.get("file", "")),
            line,
            end_line,
            str(current.get("name", "")),
            str(current.get("text", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(current))
    deduped.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", ""))))
    return deduped


def _classify_string_reference(line_text: str, match_start: int) -> str:
    """Classify a quoted-string occurrence of a symbol.

    Returns one of ``"decorator-arg"`` (inside a ``@deco(...)`` call on the same
    line), ``"fstring"`` (the literal is an f-string), ``"getattr-arg"``
    (the name argument of a ``getattr(obj, "Symbol")`` call -- #160 dogfood-tail
    "getattr breadth"), or ``"string-literal"`` (any other quoted occurrence, e.g.
    ``routing_backend="Foo"`` or an ``__all__`` entry). These are *not* AST
    references; they exist so that rename-aware agents can find
    ``@patch("module.Symbol")`` targets, ``getattr(mod, "Symbol")`` dynamic
    dispatch, and string assignments that the precise AST pass intentionally
    excludes.

    ``getattr-arg`` detection is a same-line, unbalanced-parens heuristic (the
    same precision level as the pre-existing ``decorator-arg`` check above, not
    a full AST match) -- it requires the text between ``getattr(`` and the
    matched quote to contain no further ``(``/``)`` at all, so
    ``getattr(get_handler(), "Symbol")`` falls through to the generic
    ``"string-literal"`` bucket rather than a guessed match. Fails closed:
    missing a getattr-arg classification only under-labels an occurrence that
    is still reported (as "string-literal"), never drops the row itself.
    """

    prefix = line_text[:match_start]
    stripped_prefix = prefix.lstrip()
    if stripped_prefix.startswith("@") and "(" in prefix:
        return "decorator-arg"
    quote_lead = prefix.rstrip()
    if quote_lead.endswith(("f'", 'f"', "rf'", 'rf"', "fr'", 'fr"', "f'''", 'f"""')):
        return "fstring"
    if _repo_map._GETATTR_ARG_PREFIX_RE.search(prefix):
        return "getattr-arg"
    return "string-literal"


def _string_literal_references(path: _repo_map.Path, symbol: str) -> list[dict[str, Any]]:
    """Find occurrences of ``symbol`` as a quoted string literal.

    The precise AST reference pass deliberately excludes string occurrences such
    as ``@patch("pkg.mod.Symbol")`` decorator arguments,
    ``routing_backend="Symbol"`` assignments, and ``__all__`` entries. Those are
    surfaced here so they are not silently dropped from rename planning.

    #52 fix (loop D): read via ``_read_source_text_cached`` instead of a raw uncached
    ``path.read_text`` -- bundles the SAME ``_SYMBOL_LITERAL_SEED_MAX_BYTES`` size guard its
    siblings (``_file_may_contain_literal_symbol`` et al.) already use: normal files are read
    once per (path, mtime, size) and shared across repeated calls in a session; oversize files
    still get read directly, uncached, exactly like before (no behavior change for them).
    """

    if not symbol:
        return []
    try:
        source = _repo_map._read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return []

    # Match the symbol when it sits inside a single- or double-quoted string,
    # either standalone (``"Symbol"``) or as a dotted-path tail
    # (``"pkg.mod.Symbol"``). ``\b`` keeps it from matching ``SymbolExtra``.
    pattern = re.compile(
        rf"""(?P<quote>['"])(?:[A-Za-z0-9_.]*\.)?{re.escape(symbol)}\b(?:['"])""",
    )
    occurrences: list[dict[str, Any]] = []
    for match in pattern.finditer(source):
        line_no = source.count("\n", 0, match.start()) + 1
        line_start = source.rfind("\n", 0, match.start()) + 1
        line_end = source.find("\n", match.start())
        if line_end < 0:
            line_end = len(source)
        line_text = source[line_start:line_end]
        occurrence_kind = _repo_map._classify_string_reference(
            line_text, match.start() - line_start
        )
        occurrences.append({
            "name": symbol,
            "kind": "string-reference",
            "occurrence": occurrence_kind,
            "file": str(path),
            "line": line_no,
            "text": line_text.strip(),
        })
    return occurrences


def build_symbol_refs(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
) -> dict[str, Any]:
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic
    )
    result = _repo_map.build_symbol_refs_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
    )
    _repo_map._copy_partial_signal(result, repo_map)
    return result


def build_symbol_refs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    max_tests: int | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg refs
    # --deadline` overran by 47.4s). `payload` here IS the defs return value, mutated in place
    # for the rest of this function, so threading the deadline is the whole fix at this site --
    # defs_payload's own partial/deadline_limit (if set) survive untouched through every mutation
    # below since nothing resets those keys before the final return.
    payload = _repo_map.build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    if payload.get("no_match"):
        payload["routing_reason"] = "symbol-refs"
        payload["references"] = []
        payload["string_refs"] = []
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _repo_map._coverage_summary(payload)
        payload.setdefault("resolution_gaps", [])
        return payload
    # #205: bound context-pack's own symbol-scoring + pagerank loop with the SAME warm-daemon
    # deadline. Previously called BARE here while the sibling handlers callers/impact threaded it
    # (repo_map.py:16431 / 15011); on a very large session repo this in-memory stage could still
    # overrun the 60s budget. Fold its early-break into the refs partial signal below.
    context_pack_deadline_hit = _repo_map._DeadlineBreakFlag()
    context_pack_test_scan_counts = _repo_map._TestScanCounts()
    context_payload = _repo_map.build_context_pack_from_map(
        repo_map,
        symbol,
        # opt10 #3: refs reads context_payload["test_matches"]/["file_matches"] directly into
        # _ranking_quality (test_matches[:1] only) and never re-derives payload["tests"] from
        # context_payload -- that field comes from defs_payload's OWN _relevant_tests_for_symbol
        # call above, computed independently. Bounding the source list is results-identical here;
        # see test_context_tests_source_limit_and_deadline.py.
        _test_source_limit=_repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
        _test_scan_counts=context_pack_test_scan_counts,
    )
    repo_root = _repo_map._repo_map_root_dir(repo_map)
    refs_universe_files, refs_universe_tests = _repo_map._repo_map_file_and_test_universe(repo_map)
    bounded_files, refs_ceiling_hit = _repo_map._cap_caller_scan_files(
        [*refs_universe_files, *refs_universe_tests],
        symbol=symbol,
        test_files=refs_universe_tests,
        deadline_monotonic=deadline_monotonic,
    )
    bounded_file_set = {str(current) for current in bounded_files}
    # F25 fix (Go alias-resolution confidence): the symbol's own known definition directories,
    # so a package-qualified Go call only earns high-confidence "go-import-resolution" when it
    # resolves to a package that actually OWNS this symbol -- not merely to a package alias that
    # happens to resolve to SOME directory (which used to give two unrelated same-named exports
    # identical fabricated confidence). Computed once, outside the per-file loop.
    go_definition_dirs = frozenset(
        str(_repo_map._resolved_input_path(str(current_definition["file"])).parent)
        for current_definition in payload.get("definitions", [])
    )
    references: list[dict[str, Any]] = []
    refs_scan_deadline_hit = False
    refs_files_scanned = 0
    for current in bounded_files:
        # capability P0-6 step 6: bound the reference-scan traversal so a CENTRAL symbol honors --deadline
        # instead of hanging past it (1.35.0 dogfood: `refs QueryEngine --deadline 15` -> 45s timeout,
        # no partial). Same per-file-scan hot loop as callers.
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            refs_scan_deadline_hit = True
            break
        refs_files_scanned += 1
        current_provenance = _repo_map._symbol_navigation_provenance_for_path(str(current))
        current_spec = _repo_map.lang_registry.spec_for_path(current)
        # Registry-driven extractor dispatch (replaces the prior per-language_id if/elif
        # chain). As of Task 10E (C++, the final wave) no registered language has
        # references_and_calls is None any more -- only an UNREGISTERED suffix hits the
        # EXPLICIT `_regex_references_and_calls` fallback inside `_references_and_calls_for_path`.
        current_refs, current_calls = _repo_map._references_and_calls_for_path(
            current, symbol, repo_root, definition_dirs=go_definition_dirs
        )
        if current_spec is not None and current_spec.references_and_calls is not None:
            # Secondary degrade for parser-backed languages that advertise regex-heuristic
            # missing-provenance (JS/TS/Rust today): try provider-alias, then regex. Gated on
            # registry fields -- NOT language_id -- so Go (provenance_when_missing=
            # "grammar-missing", provider_alias_calls=None) stays fail-closed: a
            # grammar-missing file yields ([], []) here and is surfaced honestly via
            # `resolution_gaps` further down, never a silently-degraded text match.
            # Python (provenance_when_missing="python-ast") likewise keeps its prior
            # no-regex-after-extractor behavior; its provider_alias_calls are applied later
            # via the python_files / external-provider pass, not here.
            if (
                not current_refs
                and not current_calls
                and current_spec.provider_alias_calls is not None
                and current_spec.provenance_when_missing == "regex-heuristic"
            ):
                current_calls = current_spec.provider_alias_calls(current, symbol, repo_root)
            if (
                not current_refs
                and not current_calls
                and current_spec.provenance_when_missing == "regex-heuristic"
            ):
                current_refs, current_calls = _repo_map._regex_references_and_calls(current, symbol)
            # Merge call rows into the reference list for tree-sitter languages
            # (JS/TS/Rust/Go). Python keeps refs-only (the prior call site discarded calls
            # with `current_refs, _ = ...`); its provenance_when_parsed is "python-ast".
            if current_spec.provenance_when_parsed == "tree-sitter":
                call_refs = [
                    {
                        "name": str(call["name"]),
                        "kind": "reference",
                        "ref_kind": str(call.get("ref_kind", "call")),
                        "file": str(call["file"]),
                        "line": int(call["line"]),
                        "text": str(call["text"]),
                        "provenance": current_provenance,
                        **(
                            {
                                "resolution_provenance": list(
                                    call.get("resolution_provenance", [])
                                ),
                                "resolution_confidence": float(
                                    call.get("resolution_confidence", 0.95)
                                ),
                            }
                            if "resolution_provenance" in call
                            else {}
                        ),
                    }
                    for call in current_calls
                ]
                current_refs.extend(call_refs)
            else:
                # Python (and any future non-tree-sitter parser-backed language): refs only.
                # Matches the prior `current_refs, _ = _python_references_and_calls(...)`.
                current_calls = []
        else:
            # Foundational / unregistered: seam already used `_regex_references_and_calls`.
            # Prior call site discarded calls with `current_refs, _ = ...`.
            current_calls = []
        references.extend(
            {
                **dict(current_ref),
                "provenance": str(current_ref.get("provenance", current_provenance)),
            }
            for current_ref in current_refs
        )

    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    external_refs: list[dict[str, Any]] = []
    fallback_used = False
    # Native ref count captured BEFORE any lsp/hybrid merge -> the divergence signal for
    # provider_agreement (a partial LSP result must surface as diverged, not a clean lsp-only proof).
    native_reference_count = len(references)
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _repo_map._external_references(
                repo_root, symbol, [dict(current) for current in payload["definitions"]]
            )
            if str(_repo_map._resolved_input_path(str(current.get("file", "")))) in bounded_file_set
        ]
        # Merge (union) native + external refs for BOTH lsp and hybrid. A partial / under-indexed
        # LSP result must NEVER discard the correct native answer: the old `references = proof_refs
        # or references` REPLACED the native rows with the LSP rows, then recomputed native_count
        # from the replaced list -> always 0 -> falsely reported lsp-only / lsp_proof:True (dogfood
        # v1.20.0: `tg refs --provider lsp` returned 2 of 14, marked authoritative -- a silent
        # wrong-output / fail-closed-contract violation). Union keeps the native truth; the
        # provider_agreement + lsp_proof below are then stamped honestly (native>lsp -> diverged).
        proof_refs = [
            dict(current) for current in external_refs if _repo_map._is_lsp_proof_row(current)
        ]
        merged_refs: dict[tuple[str, int, int], dict[str, Any]] = {}
        for current_ref in [*external_refs, *references]:
            key = (
                str(current_ref["file"]),
                int(current_ref["line"]),
                int(current_ref.get("end_line", current_ref["line"])),
            )
            if key in merged_refs:
                merged_refs[key] = _repo_map._merge_navigation_duplicate(
                    merged_refs[key], dict(current_ref)
                )
            else:
                merged_refs[key] = dict(current_ref)
        references = list(merged_refs.values())
        references.sort(
            key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
        )
        if normalized_provider == "lsp" and len(proof_refs) < native_reference_count:
            # LSP proved fewer refs than the native scan -> its index is incomplete for this symbol;
            # flag a fallback so the partial is not mis-reported as a clean lsp-only proof.
            fallback_used = True

    references = _repo_map._dedupe_symbol_references(references)
    for reference in references:
        if _repo_map._is_lsp_proof_row(reference):
            reference["lsp_proof"] = True
        # Additive T1 safety net: every code path above (native AST match, JS/TS-or-Rust call
        # flatten, regex/alias fallback, external/LSP merge) must leave a ref_kind on the row;
        # "value" is the least-specific label for paths (LSP/regex) that carry no AST context.
        reference.setdefault("ref_kind", "value")

    # Collect string-literal occurrences (decorator args, ``routing_backend=``
    # assignments, ``__all__`` entries, f-strings). These are reported alongside
    # the precise AST ``references`` so rename-aware agents do not miss them.
    string_refs: list[dict[str, Any]] = []
    for current in bounded_files:
        # #52 fix (loop D): this second pass over bounded_files ran AFTER the deadline-checked
        # main scan above with no bound of its own -- fold into the SAME refs_scan_deadline_hit
        # local the main loop already declares and the payload assembly below already reads.
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            refs_scan_deadline_hit = True
            break
        string_refs.extend(_repo_map._string_literal_references(current, symbol))
    string_refs.sort(
        key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
    )

    referenced_files = sorted(dict.fromkeys(str(current["file"]) for current in references))
    # refs' own DEDICATED --max-tests (design #96 item 2), independent of defs' -- the nested
    # build_symbol_defs_from_map call above passes no max_tests, so `payload["tests"]` here is
    # still the full relevance-filtered (uncapped) list; cap it now, BEFORE related_paths derives
    # below, or the omitted tests leak back in via the second field.
    _repo_map._apply_symbol_field_output_limit(payload, field_name="tests", max_count=max_tests)
    related_paths: list[str] = []
    for current in [*payload["files"], *referenced_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-refs"
    payload["references"] = references
    payload["string_refs"] = string_refs
    payload["files"] = referenced_files
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "moderate"
    payload["ranking_quality"] = _repo_map._ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _repo_map._coverage_summary(payload)
    coverage_gap_deadline_hit = _repo_map._DeadlineBreakFlag()
    payload["resolution_gaps"] = _repo_map._language_coverage_gaps_for_universe(
        bounded_files,
        repo_root,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=coverage_gap_deadline_hit,
    )
    payload["semantic_provider"] = normalized_provider
    lsp_proof_count = _repo_map._lsp_proof_row_count(references)
    if normalized_provider != "native" and lsp_proof_count == 0 and references:
        fallback_used = True
    payload["provider_agreement"] = _repo_map._merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=native_reference_count,
        lsp_count=lsp_proof_count,
        merged_count=len(references),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _repo_map._provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_repo_map._provider_languages_for_symbol(
            repo_map, symbol, payload["definitions"]
        ),
        fallback_used=fallback_used,
    )
    _repo_map._attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    if refs_scan_deadline_hit or context_pack_deadline_hit.hit or coverage_gap_deadline_hit.hit:
        payload["partial"] = True
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "reference_files_scanned": refs_files_scanned,
            "reference_files_total": len(bounded_files),
        }
        # opt10 #3: attribute WHICH stage ran out of budget. `deadline_exceeded` is a SHARED
        # boolean any of this module's time.monotonic readers can set, so it cannot name the
        # stage. Additive, and only when _context_tests actually ran -- mirrors the sibling
        # scanned/total pairs already in this payload. See docs/CONTRACTS.md.
        if context_pack_test_scan_counts.total:
            payload["deadline_limit"]["test_candidates_scanned"] = (
                context_pack_test_scan_counts.scanned
            )
            payload["deadline_limit"]["test_candidates_total"] = context_pack_test_scan_counts.total
    if refs_ceiling_hit:
        # backlog #1 chokepoint: the caller-scan ceiling dropped files the map otherwise covers
        # -> the reference set is not exhaustive, so mark it honestly incomplete (exit-2 contract).
        _repo_map._mark_result_incomplete(
            payload,
            remediation=_repo_map._CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": _repo_map.CALLER_SCAN_FILE_CEILING,
                "files_total": len(refs_universe_files) + len(refs_universe_tests),
            },
        )
    _repo_map.apply_answer_gaps(payload, "refs")
    return payload


def build_symbol_refs_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_refs(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def build_symbol_callers(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = _repo_map.build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
        _profiling_collector=_profiling_collector,
    )
    _repo_map._copy_partial_signal(result, repo_map)
    return result


def build_symbol_callers_from_map(
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
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg callers
    # --deadline` overran by 20.2s). `_copy_partial_signal(payload, defs_payload)` below already
    # folds defs_payload's partial signal into this function's own return, so threading the
    # deadline here is the whole fix at this site.
    defs_payload = _repo_map.build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    if defs_payload.get("no_match"):
        payload = dict(defs_payload)
        payload["routing_reason"] = "symbol-callers"
        payload["callers"] = []
        payload["import_graph_consumers"] = []
        payload["import_graph_consumer_files"] = []
        payload["import_graph_consumer_count"] = 0
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _repo_map._coverage_summary(payload)
        payload.setdefault("resolution_gaps", [])
        return _repo_map._attach_profiling(payload, _profiling_collector)
    repo_root = _repo_map._repo_map_root_dir(repo_map)
    callers_universe_files, callers_universe_tests = _repo_map._repo_map_file_and_test_universe(
        repo_map
    )
    bounded_files, callers_ceiling_hit = _repo_map._cap_caller_scan_files(
        [*callers_universe_files, *callers_universe_tests],
        symbol=symbol,
        test_files=callers_universe_tests,
        deadline_monotonic=deadline_monotonic,
    )
    bounded_file_set = {str(current) for current in bounded_files}
    # task #61: this sibling loop used to be unbounded even though it feeds the deadline-aware
    # caller scan below -- share the SAME deadline_monotonic and fold its early-break signal into
    # caller_scan_deadline_hit (set once the scan variable exists, below) for exit-2 honesty.
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
    # F25 fix: see the matching comment in build_symbol_refs_from_map -- only trust a Go
    # package-alias resolution as high-confidence when it resolves to a directory that actually
    # owns this symbol.
    go_definition_dirs = frozenset(
        str(_repo_map._resolved_input_path(current_file).parent)
        for current_file in definition_files
    )
    calls: list[dict[str, Any]] = []
    python_files: set[str] = set()

    def _should_scan_for_symbol_callers(current: _repo_map.Path) -> bool:
        if _repo_map._file_may_contain_literal_symbol(current, symbol):
            return True
        if _repo_map.lang_registry.spec_for_path(current) is None:
            return False
        if not _repo_map._file_may_import_symbol_definition(current, definition_files):
            return False
        return any(
            _repo_map._file_imports_symbol_from_definition(
                str(current),
                symbol,
                definition_file,
                repo_root,
            )
            for definition_file in definition_files
        )

    caller_scan_deadline_hit = False
    caller_files_scanned = 0
    with _repo_map._profiling_phase(_profiling_collector, "caller_scan"):
        for current in bounded_files:
            # capability P0-6 step 6: bound the CALLER-SCAN traversal, not just the repo-map parse. A
            # CENTRAL symbol's cost is scanning many files for references here (leaf symbols were
            # bounded by the parse-loop deadline; central ones hung past it because this loop was
            # unbounded -- dogfood 1.35.0). Break + return partial callers instead of overrunning.
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                caller_scan_deadline_hit = True
                break
            caller_files_scanned += 1
            if not _should_scan_for_symbol_callers(current):
                continue
            current_spec = _repo_map.lang_registry.spec_for_path(current)
            # Track python files for the later provider-alias pass (unchanged contract:
            # python alias calls are applied AFTER the per-file loop / external merge,
            # not inline here -- do not move that into the seam).
            if current_spec is not None and current_spec.language_id == "python":
                python_files.add(str(current))
            # Registry-driven extractor dispatch (replaces the prior per-language_id
            # if/elif chain). Foundational languages (references_and_calls is None) and
            # unregistered suffixes hit the EXPLICIT `_regex_references_and_calls`
            # fallback inside `_references_and_calls_for_path`.
            _, current_calls = _repo_map._references_and_calls_for_path(
                current, symbol, repo_root, definition_dirs=go_definition_dirs
            )
            if current_spec is not None and current_spec.references_and_calls is not None:
                # Secondary degrade for JS/TS/Rust only: provider-alias then regex.
                # Gated on provenance_when_missing == "regex-heuristic" so:
                # - Go stays fail-closed (Stage 1 trap): no provider-alias/regex
                #   fallback -- a grammar-missing file simply contributes no calls
                #   (surfaced via `resolution_gaps` for the refs/blast-radius payloads
                #   that compute it).
                # - Python keeps its prior loop behavior (alias applied later via
                #   python_files, not here), even though provider_alias_calls is set
                #   on its LanguageSpec.
                if (
                    not current_calls
                    and current_spec.provider_alias_calls is not None
                    and current_spec.provenance_when_missing == "regex-heuristic"
                ):
                    current_calls = current_spec.provider_alias_calls(current, symbol, repo_root)
                if not current_calls and current_spec.provenance_when_missing == "regex-heuristic":
                    _, current_calls = _repo_map._regex_references_and_calls(current, symbol)
            for current_call in current_calls:
                call_payload = dict(current_call)
                call_payload["provenance"] = _repo_map._symbol_navigation_provenance_for_path(
                    str(current_call["file"])
                )
                call_payload.setdefault("ref_kind", "call")
                calls.append(call_payload)

    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    external_calls: list[dict[str, Any]] = []
    fallback_used = False
    # Native call count captured BEFORE any lsp/hybrid merge -> the divergence signal for
    # provider_agreement (a partial LSP result must surface as diverged, not a clean lsp-only
    # proof). Mirrors build_symbol_refs_from_map's native_reference_count (H1 fix).
    native_call_count = len(calls)
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _repo_map._external_references(
                repo_root, symbol, [dict(current) for current in defs_payload["definitions"]]
            )
            if str(_repo_map._resolved_input_path(str(current.get("file", "")))) in bounded_file_set
        ]
        python_external_provenance = {
            str(current["file"]): str(
                current.get(
                    "provenance",
                    f"lsp-{_repo_map._language_for_path(_repo_map.Path(str(current['file'])))}",
                )
            )
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_provenance = {
            str(current["file"]): str(
                current.get(
                    "provenance",
                    f"lsp-{_repo_map._language_for_path(_repo_map.Path(str(current['file'])))}",
                )
            )
            for current in external_refs
            if _repo_map.Path(str(current.get("file", ""))).suffix.lower()
            in _repo_map._JS_TS_SUFFIXES
        }
        rust_external_provenance = {
            str(current["file"]): str(
                current.get(
                    "provenance",
                    f"lsp-{_repo_map._language_for_path(_repo_map.Path(str(current['file'])))}",
                )
            )
            for current in external_refs
            if _repo_map.Path(str(current.get("file", ""))).suffix.lower()
            in _repo_map._RUST_SUFFIXES
        }
        python_external_files = {
            str(current["file"])
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_files = {
            str(current["file"])
            for current in external_refs
            if _repo_map.Path(str(current.get("file", ""))).suffix.lower()
            in _repo_map._JS_TS_SUFFIXES
        }
        rust_external_files = {
            str(current["file"])
            for current in external_refs
            if _repo_map.Path(str(current.get("file", ""))).suffix.lower()
            in _repo_map._RUST_SUFFIXES
        }
        for external_ref in external_refs:
            text = str(external_ref.get("text", ""))
            if f"{symbol}(" in text or f"{symbol}!" in text or symbol in text:
                external_calls.append({
                    **dict(external_ref),
                    "kind": "call",
                })
        for python_file in sorted(python_external_files):
            alias_calls = _repo_map._python_provider_alias_calls(
                _repo_map.Path(python_file), symbol
            )
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": python_external_provenance.get(
                        python_file,
                        f"lsp-{_repo_map._language_for_path(_repo_map.Path(python_file))}",
                    ),
                })
        for js_ts_file in sorted(js_ts_external_files):
            alias_calls = _repo_map._js_ts_provider_alias_calls(
                _repo_map.Path(js_ts_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": js_ts_external_provenance.get(
                        js_ts_file,
                        f"lsp-{_repo_map._language_for_path(_repo_map.Path(js_ts_file))}",
                    ),
                })
        for rust_file in sorted(rust_external_files):
            alias_calls = _repo_map._rust_provider_alias_calls(
                _repo_map.Path(rust_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": rust_external_provenance.get(
                        rust_file,
                        f"lsp-{_repo_map._language_for_path(_repo_map.Path(rust_file))}",
                    ),
                })
        if not external_calls:
            fallback_used = True
            for python_file in sorted(python_files):
                alias_calls = _repo_map._python_provider_alias_calls(
                    _repo_map.Path(python_file), symbol
                )
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_repo_map._language_for_path(_repo_map.Path(python_file))}-fallback",
                    })
            js_ts_files = sorted(
                str(current)
                for current in bounded_files
                if _repo_map.Path(str(current)).suffix.lower() in _repo_map._JS_TS_SUFFIXES
            )
            for js_ts_file in js_ts_files:
                alias_calls = _repo_map._js_ts_provider_alias_calls(
                    _repo_map.Path(js_ts_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_repo_map._language_for_path(_repo_map.Path(js_ts_file))}-fallback",
                    })
            rust_files = sorted(
                str(current)
                for current in bounded_files
                if _repo_map.Path(str(current)).suffix.lower() in _repo_map._RUST_SUFFIXES
            )
            for rust_file in rust_files:
                alias_calls = _repo_map._rust_provider_alias_calls(
                    _repo_map.Path(rust_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_repo_map._language_for_path(_repo_map.Path(rust_file))}-fallback",
                    })
        # Merge (union) native + external calls for BOTH lsp and hybrid modes. A partial /
        # under-indexed LSP result must NEVER discard the correct native answer: the old
        # `calls = external_calls or calls` REPLACED the native call list with the (possibly
        # partial) LSP list in lsp mode, then native_count was recomputed from the REPLACED list
        # (=0) -> _merge_agreement_status always returned "lsp-only" + stamped lsp_proof: True,
        # silently dropping native-found call sites beyond a partial LSP index (H1: native finds
        # create_invoice() in a.py + b.py; a partial LSP index proves only a.py -> b.py is
        # silently dropped and the result stamped authoritative). Union keeps the native truth;
        # the provider_agreement + lsp_proof below are then stamped honestly (native>lsp ->
        # diverged). Mirrors build_symbol_refs_from_map's ref merge exactly.
        proof_calls = [
            current for current in external_calls if _repo_map._is_lsp_proof_row(current)
        ]
        merged_calls: dict[tuple[str, int, int], dict[str, Any]] = {}
        for current_call_entry in [*external_calls, *calls]:
            key = (
                str(current_call_entry["file"]),
                int(current_call_entry["line"]),
                int(current_call_entry.get("end_line", current_call_entry["line"])),
            )
            if key in merged_calls:
                merged_calls[key] = _repo_map._merge_navigation_duplicate(
                    merged_calls[key],
                    dict(current_call_entry),
                )
            else:
                merged_calls[key] = dict(current_call_entry)
        calls = list(merged_calls.values())
        calls.sort(
            key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
        )
        calls = _repo_map._dedupe_symbol_references(calls)
        if normalized_provider == "lsp" and len(proof_calls) < native_call_count:
            # LSP proved fewer calls than the native scan -> its index is incomplete for this
            # symbol; flag a fallback so the partial is not mis-reported as a clean lsp-only proof.
            fallback_used = True

    definition_locations = {
        (
            str(current["file"]),
            int(current.get("line", current.get("start_line", 0)) or 0),
        )
        for current in definitions
    }
    calls = [
        dict(current)
        for current in calls
        if (str(current["file"]), int(current.get("line", 0) or 0)) not in definition_locations
    ]
    for call in calls:
        if _repo_map._is_lsp_proof_row(call):
            call["lsp_proof"] = True
        # Additive T1 safety net (mirrors build_symbol_refs_from_map): every caller row is a
        # call by construction, so default a missing ref_kind (external/LSP/alias/regex paths
        # that predate this field) to "call" rather than leaving it absent.
        call.setdefault("ref_kind", "call")
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in calls))
    # task #61: this sibling loop used to be unbounded even though it re-walks the same
    # bounded_files set the deadline-checked caller-scan main loop above just finished -- share the
    # SAME deadline_monotonic and fold its early-break signal into caller_scan_deadline_hit below.
    import_graph_consumers_deadline_hit = _repo_map._DeadlineBreakFlag()
    import_graph_consumers = _repo_map._build_import_graph_consumers_from_map(
        repo_map,
        symbol,
        definition_files,
        bounded_files=bounded_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=import_graph_consumers_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    import_graph_consumer_files = sorted(
        dict.fromkeys(str(current["file"]) for current in import_graph_consumers)
    )
    # task #103 Fix 2: thread the shared deadline into context-pack's own symbol-scoring loop too
    # -- previously unbounded even though it feeds the same 4-way partial fold-in just below.
    context_pack_deadline_hit = _repo_map._DeadlineBreakFlag()
    context_pack_test_scan_counts = _repo_map._TestScanCounts()
    context_payload = _repo_map.build_context_pack_from_map(
        repo_map,
        symbol,
        # opt10 #3: callers never sets payload["test_matches"] from context_payload at all (only
        # payload["tests"], via _relevant_tests_for_symbol's fallback_tests= -- a last resort only
        # reached when its OWN graph-ranked + direct-import scans both find nothing) and
        # _ranking_quality reads context_payload["test_matches"][:1] directly. Bounding the source
        # list is results-identical here; see test_context_tests_source_limit_and_deadline.py.
        _test_source_limit=_repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
        _test_scan_counts=context_pack_test_scan_counts,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop B): this sibling loop used to be unbounded even though it feeds the
    # deadline-aware caller scan above -- share the SAME deadline_monotonic and fold its
    # early-break signal into the 4-way partial fold-in below (dominant cause of the 23x overrun
    # on a high-fan-out symbol: _preferred_definition_files falls back to the FULL unfiltered
    # definition_files when all import-scores are 0, flooding this loop unbounded).
    related_tests_deadline_hit = _repo_map._DeadlineBreakFlag()
    related_graph_uncertainty: dict[str, dict[str, Any]] = {}
    related_tests = _repo_map._relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        caller_files=caller_files,
        fallback_tests=list(context_payload.get("tests", [])),
        deadline_monotonic=deadline_monotonic,
        deadline_hit=related_tests_deadline_hit,
        _test_scan_counts=context_pack_test_scan_counts,
        _profiling_collector=_profiling_collector,
        _graph_uncertainty=related_graph_uncertainty,
    )
    # callers' own DEDICATED --max-tests (design #96 item 2): cap BEFORE related_paths derives
    # below, or the omitted tests leak back in via the second field. `payload` does not exist yet
    # at this point (built via _envelope below), so cap through a scratch dict and read the
    # (possibly capped) list + stamped output_limit back out.
    tests_limit_scratch: dict[str, Any] = {"tests": related_tests}
    _repo_map._apply_symbol_field_output_limit(
        tests_limit_scratch, field_name="tests", max_count=max_tests
    )
    related_tests = cast(list[str], tests_limit_scratch["tests"])
    tests_output_limit = tests_limit_scratch.get("output_limit")

    related_paths: list[str] = []
    for related_path in [
        *definition_files,
        *caller_files,
        *import_graph_consumer_files,
        *related_tests,
    ]:
        if related_path not in related_paths:
            related_paths.append(related_path)

    payload = _repo_map._envelope(_repo_map.Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-callers"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["callers"] = calls
    payload["import_graph_consumers"] = import_graph_consumers
    payload["import_graph_consumer_files"] = import_graph_consumer_files
    payload["import_graph_consumer_count"] = len(import_graph_consumers)
    payload["files"] = caller_files
    payload["tests"] = related_tests
    if tests_output_limit is not None:
        payload["output_limit"] = tests_output_limit
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "moderate"
    # Fold every sibling deadline signal into the main caller scan: import graph, preferred
    # definition, related tests, context pack, and coverage gaps all make the answer partial.
    coverage_gap_deadline_hit = _repo_map._DeadlineBreakFlag()
    if (
        caller_scan_deadline_hit
        or import_graph_consumers_deadline_hit.hit
        or preferred_definition_deadline_hit.hit
        or related_tests_deadline_hit.hit
        or context_pack_deadline_hit.hit
    ):
        # capability P0-6 step 6: the caller-scan was cut short by --deadline -> partial (the callers list
        # holds what was found before the budget). graph_completeness downgrades so an agent does not
        # trust a small/zero caller count on a deadline-truncated central-symbol scan.
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "caller_files_scanned": caller_files_scanned,
            "caller_files_total": len(bounded_files),
        }
        # opt10 #3: attribute WHICH stage ran out of budget. `deadline_exceeded` is a SHARED
        # boolean any of this module's time.monotonic readers can set, so it cannot name the
        # stage. Additive, and only when _context_tests actually ran -- mirrors the sibling
        # scanned/total pairs already in this payload. See docs/CONTRACTS.md.
        if context_pack_test_scan_counts.total:
            payload["deadline_limit"]["test_candidates_scanned"] = (
                context_pack_test_scan_counts.scanned
            )
            payload["deadline_limit"]["test_candidates_total"] = context_pack_test_scan_counts.total
    payload["ranking_quality"] = _repo_map._ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _repo_map._coverage_summary(payload)
    payload["resolution_gaps"] = _repo_map._language_coverage_gaps_for_universe(
        bounded_files,
        repo_root,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=coverage_gap_deadline_hit,
    )
    if coverage_gap_deadline_hit.hit:
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        payload.setdefault("deadline_limit", {})["deadline_exceeded"] = True
    callers_uncertain_paths = _repo_map_graph.uncertain_paths(
        context_payload.get("file_matches", []), related_graph_uncertainty
    )
    _repo_map_graph.add_uncertainty(_repo_map, payload, callers_uncertain_paths, repo_root)
    payload["semantic_provider"] = normalized_provider
    lsp_proof_count = _repo_map._lsp_proof_row_count(calls)
    if normalized_provider != "native" and lsp_proof_count == 0 and calls:
        fallback_used = True
    payload["provider_agreement"] = _repo_map._merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=native_call_count,
        lsp_count=lsp_proof_count,
        merged_count=len(calls),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _repo_map._provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_repo_map._provider_languages_for_symbol(
            repo_map, symbol, defs_payload["definitions"]
        ),
        fallback_used=fallback_used,
    )
    _repo_map._attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    _repo_map._copy_scan_limit(payload, defs_payload)
    _repo_map._copy_partial_signal(payload, defs_payload)
    if callers_ceiling_hit:
        # backlog #1 chokepoint: the caller-scan ceiling dropped files the map otherwise covers
        # -> the caller set is not exhaustive, so mark it honestly incomplete (exit-2 contract).
        _repo_map._mark_result_incomplete(
            payload,
            remediation=_repo_map._CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": _repo_map.CALLER_SCAN_FILE_CEILING,
                "files_total": len(callers_universe_files) + len(callers_universe_tests),
            },
        )
    _repo_map.apply_answer_gaps(payload, "callers")
    return _repo_map._attach_profiling(payload, _profiling_collector)


def build_symbol_callers_json(
    symbol: str,
    path: str | _repo_map.Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        _repo_map.build_symbol_callers(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )
