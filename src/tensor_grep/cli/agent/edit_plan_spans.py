from __future__ import annotations

from typing import Any

from tensor_grep.cli import repo_map as _repo_map


def _symbol_sort_key(symbol: dict[str, Any]) -> tuple[int, int, str, str]:
    start_line = int(symbol.get("start_line", symbol.get("line", 0)))
    end_line = int(symbol.get("end_line", start_line))
    return (
        start_line,
        end_line,
        str(symbol.get("kind", "")),
        str(symbol.get("name", "")),
    )


def _symbols_for_file(repo_map: dict[str, Any], file_path: str) -> list[dict[str, Any]]:
    symbols = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("file")) == file_path
    ]
    symbols.sort(key=_repo_map._symbol_sort_key)
    return symbols


def _enclosing_symbol_for_line(
    repo_map: dict[str, Any],
    file_path: str,
    line_number: int,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for symbol in _repo_map._symbols_for_file(repo_map, file_path):
        start_line = symbol.get("start_line", symbol.get("line"))
        end_line = symbol.get("end_line", start_line)
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        if start_line <= line_number <= end_line:
            candidates.append(symbol)
    if not candidates:
        return None
    candidates.sort(
        key=lambda symbol: (
            int(symbol.get("end_line", symbol.get("line", 0)))
            - int(symbol.get("start_line", symbol.get("line", 0))),
            *_repo_map._symbol_sort_key(symbol),
        )
    )
    return candidates[0]


def _related_span_record(
    symbol: dict[str, Any],
    *,
    depth: int,
    score: int,
    reasons: list[str],
) -> dict[str, Any] | None:
    span = _repo_map._primary_span_for_symbol(symbol)
    file_path = symbol.get("file")
    symbol_name = symbol.get("name")
    if span is None or not file_path or not symbol_name:
        return None
    return {
        "file": str(file_path),
        "symbol": str(symbol_name),
        "start_line": int(span["start_line"]),
        "end_line": int(span["end_line"]),
        "depth": int(depth),
        "score": int(score),
        "reasons": list(reasons),
        "provenance": _repo_map._provenance_from_reasons(reasons),
        "rationale": _repo_map._span_rationale(str(symbol_name), list(reasons), int(depth)),
    }


def _ordered_dependent_file_matches(
    radius_payload: dict[str, Any],
    *,
    primary_file: str | None,
    max_depth: int,
) -> list[dict[str, Any]]:
    definition_files = {
        str(current.get("file"))
        for current in radius_payload.get("definitions", [])
        if current.get("file")
    }
    if primary_file:
        definition_files.add(str(primary_file))

    matches: list[dict[str, Any]] = []
    for current in radius_payload.get("file_matches", []):
        current_path = str(current.get("path", ""))
        if not current_path or current_path in definition_files:
            continue
        if _repo_map._is_test_file(_repo_map.Path(current_path)):
            continue
        reasons = list(current.get("reasons", []))
        if "heuristic-import" in reasons and not {"caller", "graph-depth"}.intersection(reasons):
            continue
        depth = int(current.get("depth", max_depth + 1))
        if depth > max_depth:
            continue
        matches.append({
            "path": current_path,
            "depth": depth,
            "score": int(current.get("score", 0)),
            "reasons": reasons,
            "graph_score": float(current.get("graph_score", 0.0)),
        })

    matches = _repo_map._narrow_python_depth_two_dependency_matches(
        matches, primary_file=primary_file
    )

    matches.sort(
        key=lambda current: (
            int(current["depth"]),
            0 if "caller" in current["reasons"] else 1,
            -int(current["score"]),
            -float(current["graph_score"]),
            str(current["path"]),
        )
    )
    return matches


def _narrow_python_depth_two_dependency_matches(
    matches: list[dict[str, Any]],
    *,
    primary_file: str | None,
) -> list[dict[str, Any]]:
    if not primary_file:
        return matches
    primary_name = _repo_map.Path(primary_file).name
    if primary_name not in {"utils.py", "exceptions.py", "termui.py", "_compat.py", "core.py"}:
        return matches
    if primary_name in {"utils.py", "termui.py", "core.py"}:
        has_depth_one = any(int(current.get("depth", 0)) <= 1 for current in matches)
        if not has_depth_one:
            return matches
        return [current for current in matches if int(current.get("depth", 0)) <= 1]
    caller_backed = any("caller" in list(current.get("reasons", [])) for current in matches)
    if not caller_backed:
        return matches
    filtered: list[dict[str, Any]] = []
    for current in matches:
        reasons = list(current.get("reasons", []))
        depth = int(current.get("depth", 0))
        if "caller" not in reasons and depth > 1:
            continue
        filtered.append(current)
    return filtered


def _related_spans_from_blast_radius(
    repo_map: dict[str, Any],
    radius_payload: dict[str, Any],
    *,
    primary_symbol: dict[str, Any] | None,
    dependent_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if primary_symbol is None:
        return [], 0

    primary_key = (
        str(primary_symbol.get("file", "")),
        str(primary_symbol.get("name", "")),
    )
    seen: set[tuple[str, str]] = {primary_key}
    related_spans: list[dict[str, Any]] = []
    caller_keys: set[tuple[str, str]] = set()
    match_by_path = {str(match["path"]): match for match in dependent_matches}

    def _add_symbol(
        symbol: dict[str, Any] | None,
        *,
        is_caller: bool = False,
        depth: int = 0,
        score: int = 0,
        reasons: list[str] | None = None,
    ) -> None:
        if symbol is None:
            return
        key = (str(symbol.get("file", "")), str(symbol.get("name", "")))
        if not key[0] or not key[1] or key in seen:
            return
        record = _repo_map._related_span_record(
            symbol,
            depth=depth,
            score=score,
            reasons=list(reasons or []),
        )
        if record is None:
            return
        seen.add(key)
        related_spans.append(record)
        if is_caller:
            caller_keys.add(key)

    direct_callers = sorted(
        (
            dict(current)
            for current in radius_payload.get("callers", [])
            if current.get("file")
            and isinstance(current.get("line"), int)
            and not _repo_map._is_test_file(_repo_map.Path(str(current.get("file"))))
        ),
        key=lambda current: (
            str(current.get("file", "")),
            int(current.get("line", 0)),
            str(current.get("text", "")),
        ),
    )
    for caller in direct_callers:
        caller_path = str(caller["file"])
        caller_match = match_by_path.get(
            caller_path,
            {
                "depth": 1,
                "score": 0,
                "reasons": ["caller"],
            },
        )
        _add_symbol(
            _repo_map._enclosing_symbol_for_line(
                repo_map,
                caller_path,
                int(caller["line"]),
            ),
            is_caller=True,
            depth=int(caller_match.get("depth", 1)),
            score=int(caller_match.get("score", 0)),
            reasons=list(caller_match.get("reasons", ["caller"])),
        )

    for match in dependent_matches:
        for symbol in _repo_map._symbols_for_file(repo_map, str(match["path"])):
            _add_symbol(
                symbol,
                depth=int(match.get("depth", 0)),
                score=int(match.get("score", 0)),
                reasons=list(match.get("reasons", [])),
            )
            break

    return related_spans, len(caller_keys)


def _candidate_edit_spans(
    *,
    primary_symbol: dict[str, Any] | None,
    primary_file_match: dict[str, Any],
    related_spans: list[dict[str, Any]],
    max_spans: int,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if primary_symbol is not None:
        span = _repo_map._primary_span_for_symbol(primary_symbol)
        if span is not None and primary_symbol.get("file") and primary_symbol.get("name"):
            spans.append({
                "file": str(primary_symbol["file"]),
                "symbol": str(primary_symbol["name"]),
                "start_line": int(span["start_line"]),
                "end_line": int(span["end_line"]),
                "depth": 0,
                "score": int(primary_file_match.get("score", 0))
                + int(primary_symbol.get("score", 0)),
                "reasons": list(primary_file_match.get("reasons", [])) or ["primary"],
                "provenance": _repo_map._provenance_from_reasons(
                    list(primary_file_match.get("reasons", [])) or ["primary"]
                ),
                "rationale": _repo_map._span_rationale(
                    str(primary_symbol["name"]),
                    list(primary_file_match.get("reasons", [])) or ["primary"],
                    0,
                ),
            })
    spans.extend(dict(current) for current in related_spans)
    spans.sort(
        key=lambda current: (
            int(current.get("depth", 0)),
            -int(current.get("score", 0)),
            str(current.get("file", "")),
            int(current.get("start_line", 0)),
            str(current.get("symbol", "")),
        )
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for current in spans:
        key = (
            str(current.get("file", "")),
            str(current.get("symbol", "")),
            int(current.get("start_line", 0)),
            int(current.get("end_line", 0)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
        if len(deduped) >= max(1, max_spans):
            break
    return deduped


def _mention_ref(file_path: str, start_line: int, end_line: int) -> str:
    normalized_end = max(start_line, end_line)
    if normalized_end <= start_line:
        return f"{file_path}#L{start_line}"
    return f"{file_path}#L{start_line}-L{normalized_end}"


def _navigation_pack(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    max_reads: int,
) -> dict[str, Any]:
    seed = dict(payload.get("edit_plan_seed", {}))
    primary_file = str(seed.get("primary_file", "") or "")
    raw_primary_symbol = seed.get("primary_symbol")
    primary_symbol = dict(raw_primary_symbol) if isinstance(raw_primary_symbol, dict) else {}
    primary_symbol_name = str(primary_symbol.get("name", "") or "")
    raw_primary_span = seed.get("primary_span")
    primary_span = dict(raw_primary_span) if isinstance(raw_primary_span, dict) else {}
    primary_start = int(primary_span.get("start_line", 0) or 0)
    primary_end = int(primary_span.get("end_line", primary_start) or primary_start)
    primary_target = {
        "file": primary_file,
        "symbol": primary_symbol_name,
        "start_line": primary_start,
        "end_line": primary_end,
        "mention_ref": _repo_map._mention_ref(primary_file, primary_start, primary_end)
        if primary_file and primary_start > 0
        else "",
        "reasons": list(seed.get("reasons", [])),
        "confidence": dict(seed.get("confidence", {})),
    }
    for key in (
        "semantic_provider",
        "provenance",
        "lsp_provider_response",
        "lsp_proof",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in primary_symbol:
            primary_target[key] = primary_symbol[key]

    follow_up_reads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()

    def add_read(entry: dict[str, Any], *, role: str) -> None:
        file_path = str(entry.get("file", "") or "")
        symbol_name = str(entry.get("symbol", "") or "")
        start_line = int(entry.get("start_line", 0) or 0)
        end_line = int(entry.get("end_line", start_line) or start_line)
        if not file_path or start_line <= 0 or end_line < start_line:
            return
        key = (file_path, symbol_name, start_line, end_line)
        if key in seen:
            return
        seen.add(key)
        follow_up_reads.append({
            "file": file_path,
            "symbol": symbol_name,
            "start_line": start_line,
            "end_line": end_line,
            "mention_ref": _repo_map._mention_ref(file_path, start_line, end_line),
            "role": role,
            "rationale": str(entry.get("rationale", "") or ""),
            "reasons": list(entry.get("reasons", [])),
            "provenance": list(entry.get("provenance", [])),
        })

    if primary_file and primary_symbol_name and primary_start > 0:
        add_read(
            {
                "file": primary_file,
                "symbol": primary_symbol_name,
                "start_line": primary_start,
                "end_line": primary_end,
                "rationale": "Primary edit target selected from ranked symbols and file matches.",
                "reasons": list(seed.get("reasons", [])),
            },
            role="primary",
        )

    for span in list(payload.get("candidate_edit_targets", {}).get("spans", [])):
        span_file = str(span.get("file", "") or "")
        span_symbol = str(span.get("symbol", "") or "")
        role = (
            "primary"
            if span_file == primary_file
            and span_symbol == primary_symbol_name
            and int(span.get("start_line", 0) or 0) == primary_start
            else "related"
        )
        add_read(dict(span), role=role)
        if len(follow_up_reads) >= max(1, max_reads):
            break

    if len(follow_up_reads) < max(1, max_reads):
        validation_tests = [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ]
        for test_path in validation_tests:
            symbols = _repo_map._symbols_for_file(repo_map, test_path)
            preferred = next(
                (current for current in symbols if str(current.get("name", "")).startswith("test")),
                symbols[0] if symbols else None,
            )
            if preferred is None:
                continue
            span = _repo_map._primary_span_for_symbol(preferred)
            if span is None:
                continue
            add_read(
                {
                    "file": test_path,
                    "symbol": str(preferred.get("name", "") or ""),
                    "start_line": int(span["start_line"]),
                    "end_line": int(span["end_line"]),
                    "rationale": "Validation target likely to confirm the primary edit outcome.",
                    "reasons": ["validation-test"],
                    "provenance": ["filename-heuristic"],
                },
                role="test",
            )
            if len(follow_up_reads) >= max(1, max_reads):
                break

    grouped_reads: dict[str, list[dict[str, Any]]] = {"related": [], "test": []}
    for current in follow_up_reads:
        role = str(current.get("role", "") or "")
        if role == "related":
            grouped_reads["related"].append(current)
        elif role == "test":
            grouped_reads["test"].append(current)

    parallel_read_groups: list[dict[str, Any]] = []
    prefetched_reads: list[dict[str, Any]] = []
    if primary_file:
        try:
            primary_parent = _repo_map._resolved_input_path(primary_file).parent
        except (OSError, ValueError):
            primary_parent = None
        if primary_parent is not None:
            for role in ("related", "test"):
                remaining_reads: list[dict[str, Any]] = []
                for current in grouped_reads[role]:
                    current_file = str(current.get("file", "") or "")
                    try:
                        current_parent = _repo_map._resolved_input_path(current_file).parent
                    except (OSError, ValueError):
                        current_parent = None
                    if current_parent is not None and current_parent == primary_parent:
                        prefetched_reads.append(current)
                    else:
                        remaining_reads.append(current)
                grouped_reads[role] = remaining_reads
    if primary_target["mention_ref"]:
        primary_mentions = [str(primary_target["mention_ref"])]
        primary_files = [primary_file] if primary_file else []
        primary_roles = ["primary"]
        for current in prefetched_reads:
            mention = str(current.get("mention_ref", "") or "")
            file_path = str(current.get("file", "") or "")
            role = str(current.get("role", "") or "")
            if mention:
                primary_mentions.append(mention)
            if file_path:
                primary_files.append(file_path)
            if role:
                primary_roles.append(role)
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "primary",
            "can_parallelize": False,
            "mentions": primary_mentions,
            "files": primary_files,
            "roles": primary_roles,
        })
    if grouped_reads["related"]:
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "related",
            "can_parallelize": True,
            "mentions": [
                str(current.get("mention_ref", "") or "") for current in grouped_reads["related"]
            ],
            "files": [str(current.get("file", "") or "") for current in grouped_reads["related"]],
            "roles": ["related"],
        })
    if grouped_reads["test"]:
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "test",
            "can_parallelize": True,
            "mentions": [
                str(current.get("mention_ref", "") or "") for current in grouped_reads["test"]
            ],
            "files": [str(current.get("file", "") or "") for current in grouped_reads["test"]],
            "roles": ["test"],
        })

    return {
        "primary_target": primary_target,
        "follow_up_reads": follow_up_reads,
        "parallel_read_groups": parallel_read_groups,
        "related_tests": [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ],
        "validation_commands": [
            str(current) for current in seed.get("validation_commands", []) if str(current)
        ],
        "suggested_validation_commands": [
            dict(current)
            for current in seed.get("suggested_validation_commands", []) or []
            if isinstance(current, dict)
        ],
        "validation_alignment": dict(seed.get("validation_alignment", {}))
        if isinstance(seed.get("validation_alignment"), dict)
        else {},
        "edit_ordering": [
            str(current) for current in seed.get("edit_ordering", []) if str(current)
        ],
        "rollback_risk": float(seed.get("rollback_risk", 0.0) or 0.0),
    }


def _deterministic_edit_ordering(
    primary_file: str | None,
    dependent_files: list[str],
    tests: list[str],
) -> list[str]:
    ordering: list[str] = []
    for current in [primary_file, *dependent_files, *tests]:
        if not current or current in ordering:
            continue
        ordering.append(str(current))
    return ordering


def _suggested_edit_confidence(score: int, provenance: str | None = None) -> float:
    if score <= 0:
        return 0.0
    normalized_provenance = str(provenance or "").strip().lower()
    adjusted_score = score
    if normalized_provenance in {"heuristic", "regex-heuristic", "filename-convention"}:
        adjusted_score = max(1, score - 3)
    elif normalized_provenance == "graph-derived":
        adjusted_score = max(1, score - 2)
    return _repo_map._confidence_from_score(adjusted_score)


def _import_update_rationale(symbol: str, module_name: str) -> str:
    return (
        f"this file imports {symbol} from {module_name}; "
        f"if {symbol} changes, this import must be updated"
    )


def _caller_update_rationale(symbol: str, line_number: int) -> str:
    return (
        f"calls {symbol}() on line {line_number}; "
        f"if {symbol}'s signature changes, this call site must be updated"
    )


def _suggested_edit_base_entry(current: dict[str, Any]) -> dict[str, Any]:
    file_path = str(current.get("file", ""))
    reasons = list(current.get("reasons", []))
    edit_kind = "caller-update" if "caller" in reasons else "dependency-update"
    provenance = (
        _repo_map._symbol_navigation_provenance_for_path(file_path) if file_path else "heuristic"
    )
    return {
        "file": file_path,
        "symbol": str(current.get("symbol", "")),
        "start_line": int(current.get("start_line", 0)),
        "end_line": int(current.get("end_line", 0)),
        "edit_kind": edit_kind,
        "rationale": str(
            current.get(
                "rationale",
                _repo_map._span_rationale(
                    str(current.get("symbol", "")),
                    reasons,
                    int(current.get("depth", 0)),
                ),
            )
        ),
        "provenance": provenance,
        "confidence": _repo_map._suggested_edit_confidence(
            int(current.get("score", 0)), provenance
        ),
    }


def _caller_update_targets_for_span(
    current: dict[str, Any],
    callers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_file = str(current.get("file", ""))
    start_line = int(current.get("start_line", 0))
    end_line = int(current.get("end_line", start_line))
    if not current_file or start_line <= 0 or end_line < start_line:
        return []

    targets_by_line: dict[int, dict[str, Any]] = {}
    for caller in callers:
        if str(caller.get("file", "")) != current_file:
            continue
        line_number = int(caller.get("line", 0))
        if line_number < start_line or line_number > end_line or line_number <= 0:
            continue
        provenance = str(
            caller.get("provenance", _repo_map._symbol_navigation_provenance_for_path(current_file))
        )
        candidate = {
            "start_line": line_number,
            "end_line": line_number,
            "provenance": provenance,
        }
        existing = targets_by_line.get(line_number)
        if existing is None or float(_repo_map._suggested_edit_confidence(10, provenance)) > float(
            _repo_map._suggested_edit_confidence(10, str(existing.get("provenance", "")))
        ):
            targets_by_line[line_number] = candidate

    return [targets_by_line[line] for line in sorted(targets_by_line)]


def _ambiguous_suggested_edit_alternatives(
    primary_symbol: dict[str, Any] | None,
    definitions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if primary_symbol is None:
        return []
    primary_name = str(primary_symbol.get("name", ""))
    primary_file = str(primary_symbol.get("file", ""))
    alternatives: dict[tuple[str, str], dict[str, str]] = {}
    for current in definitions:
        current_file = str(current.get("file", ""))
        current_name = str(current.get("name", ""))
        if not current_file or not current_name:
            continue
        if current_name != primary_name:
            continue
        if current_file == primary_file:
            continue
        key = (current_file, current_name)
        alternatives[key] = {"file": current_file, "symbol": current_name}
    return [alternatives[key] for key in sorted(alternatives)]


def _suggested_edit_priority(entry: dict[str, Any]) -> tuple[int, float, int, int, str, str]:
    edit_kind = str(entry.get("edit_kind", "dependency-update"))
    kind_priority = {
        "caller-update": 0,
        "import-update": 1,
        "dependency-update": 2,
    }.get(edit_kind, 3)
    start_line = int(entry.get("start_line", 0))
    end_line = int(entry.get("end_line", start_line))
    return (
        kind_priority,
        -float(entry.get("confidence", 0.0)),
        max(0, end_line - start_line),
        0 if bool(entry.get("ambiguous")) else 1,
        str(entry.get("symbol", "")),
        str(entry.get("rationale", "")),
    )


def _deduplicate_suggested_edits(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_keys: list[tuple[str, int]] = []
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in suggestions:
        file_path = str(entry.get("file", ""))
        start_line = int(entry.get("start_line", 0))
        if not file_path or start_line <= 0:
            continue
        key = (file_path, start_line)
        existing = deduped.get(key)
        if existing is None:
            ordered_keys.append(key)
            deduped[key] = dict(entry)
            continue
        if _repo_map._suggested_edit_priority(entry) < _repo_map._suggested_edit_priority(existing):
            deduped[key] = dict(entry)
    return [deduped[key] for key in ordered_keys]


def _capped_suggested_edits(
    entries: list[dict[str, Any]],
    max_edits: int | None,
) -> list[dict[str, Any]]:
    """Bound ``entries`` to ``max_edits`` items; ``None`` means unbounded. This is the SOLE
    enforcement point for ``_suggested_edits_from_related_spans``'s ``max_edits`` contract (audit
    B9/A18): the parameter used to be accepted but silently ignored, so ``tg edit-plan --json
    --max-files N`` grew ``suggested_edits`` unbounded despite the flag's documented meaning.

    ``max_edits`` is threaded in from ``_build_edit_plan_seed``'s ``suggested_edits_max`` parameter,
    which defaults to ``None`` and is OPT-IN per caller (see that parameter's docstring).
    ``tg edit-plan``'s own builder (``build_context_edit_plan_from_map``, audit B9/A18) was the
    first to pass a real value. Audit #212 (a follow-up to B9/#661) found the identical flag-lie in
    three sibling callers and closed it the same way: ``tg context-render`` (own separate,
    pre-existing downstream cap via ``_compact_edit_plan_seed``, but only for the compact/llm render
    profiles -- the default "full" profile had no cap at all) and ``tg blast-radius-plan``/``tg
    blast-radius-render`` (no cap at all, in any profile) now ALSO opt in, passing their own
    ``--max-files`` value through ``build_context_render_from_map``/``build_symbol_blast_radius_
    plan_from_map``/``build_symbol_blast_radius_render_from_map`` respectively. See
    ``tests/unit/test_edit_plan_max_files_bounds_suggested_edits.py`` and
    ``tests/unit/test_context_render_and_blast_radius_max_files_bounds_suggested_edits.py`` for the
    pins proving so. Any FUTURE caller of ``_build_edit_plan_seed``/``_attach_edit_plan_metadata``
    that does not explicitly pass ``suggested_edits_max`` still gets the unbounded, pre-fix
    behavior -- the low-level default stays ``None`` on purpose so a not-yet-written caller is never
    silently capped."""
    if max_edits is None:
        return entries
    return entries[:max_edits]


def _suggested_edits_from_related_spans(
    related_spans: list[dict[str, Any]],
    *,
    primary_symbol: dict[str, Any] | None = None,
    definitions: list[dict[str, Any]] | None = None,
    callers: list[dict[str, Any]] | None = None,
    repo_root: _repo_map.Path | str | None = None,
    max_edits: int | None = None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    processed_spans: list[dict[str, Any]] = []
    resolved_callers = [dict(current) for current in callers or []]
    resolved_definitions = [dict(current) for current in definitions or []]
    for current in related_spans:
        reasons = list(current.get("reasons", []))
        if "caller" not in reasons:
            suggestions.append(_repo_map._suggested_edit_base_entry(current))
            processed_spans.append(current)
            continue

        current_file = str(current.get("file", ""))
        score = int(current.get("score", 0))
        call_targets = _repo_map._caller_update_targets_for_span(current, resolved_callers)
        ambiguous_alternatives = _repo_map._ambiguous_suggested_edit_alternatives(
            primary_symbol,
            resolved_definitions,
        )
        if call_targets and primary_symbol is not None:
            primary_name = str(primary_symbol.get("name", ""))
            for target in call_targets:
                entry: dict[str, Any] = {
                    "file": current_file,
                    "symbol": str(current.get("symbol", "")),
                    "start_line": int(target["start_line"]),
                    "end_line": int(target["end_line"]),
                    "edit_kind": "caller-update",
                    "rationale": _repo_map._caller_update_rationale(
                        primary_name,
                        int(target["start_line"]),
                    ),
                    "provenance": str(target.get("provenance", "heuristic")),
                    "confidence": _repo_map._suggested_edit_confidence(
                        score,
                        str(target.get("provenance", "heuristic")),
                    ),
                }
                if ambiguous_alternatives:
                    entry["ambiguous"] = True
                    entry["alternatives"] = ambiguous_alternatives
                suggestions.append(entry)
        else:
            fallback_entry = _repo_map._suggested_edit_base_entry(current)
            if ambiguous_alternatives:
                fallback_entry["ambiguous"] = True
                fallback_entry["alternatives"] = ambiguous_alternatives
            suggestions.append(fallback_entry)
        processed_spans.append(current)

    if primary_symbol is None:
        return _repo_map._capped_suggested_edits(
            _repo_map._deduplicate_suggested_edits(suggestions), max_edits
        )

    primary_name = str(primary_symbol.get("name", ""))
    definition_path = str(primary_symbol.get("file", ""))
    if not primary_name or not definition_path:
        return _repo_map._capped_suggested_edits(
            _repo_map._deduplicate_suggested_edits(suggestions), max_edits
        )

    import_updates_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for current in processed_spans:
        current_file = str(current.get("file", ""))
        if not current_file:
            continue
        import_target = _repo_map._import_update_target(
            _repo_map.Path(current_file),
            primary_name,
            definition_path,
            repo_root,
        )
        if import_target is None:
            continue
        start_line = int(import_target.get("start_line", 0))
        end_line = int(import_target.get("end_line", start_line))
        provenance = str(import_target.get("provenance", "heuristic"))
        module_name = str(import_target.get("module", _repo_map.Path(definition_path).stem))
        import_entry: dict[str, Any] = {
            "file": current_file,
            "symbol": primary_name,
            "start_line": start_line,
            "end_line": end_line,
            "edit_kind": "import-update",
            "rationale": _repo_map._import_update_rationale(primary_name, module_name),
            "provenance": provenance,
            "confidence": _repo_map._suggested_edit_confidence(
                int(current.get("score", 0)), provenance
            ),
        }
        key = (current_file, start_line, end_line)
        existing = import_updates_by_key.get(key)
        if existing is None or float(import_entry["confidence"]) > float(existing["confidence"]):
            import_updates_by_key[key] = import_entry

    suggestions.extend(import_updates_by_key.values())
    return _repo_map._capped_suggested_edits(
        _repo_map._deduplicate_suggested_edits(suggestions), max_edits
    )
