from __future__ import annotations

import json
from typing import Any

from tensor_grep.cli import focused_rendering as _focused_rendering
from tensor_grep.cli import repo_map as _repo_map


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(current) for current in value if isinstance(current, dict)]


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    strings: list[str] = []
    for current in value:
        if current is None:
            continue
        text = str(current)
        if text:
            strings.append(text)
    return strings


def _compact_symbol_record(symbol: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(symbol, dict):
        return None
    keys = (
        "name",
        "kind",
        "file",
        "line",
        "start_line",
        "end_line",
        "score",
        "provenance",
    )
    return {key: symbol[key] for key in keys if key in symbol}


def _compact_span_record(span: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "file",
        "symbol",
        "start_line",
        "end_line",
        "depth",
        "score",
        "provenance",
        "rationale",
    )
    return {key: span[key] for key in keys if key in span}


def _compact_suggested_edit(edit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "file",
        "symbol",
        "start_line",
        "end_line",
        "edit_kind",
        "confidence",
        "provenance",
        "rationale",
    )
    return {key: edit[key] for key in keys if key in edit}


def _compact_edit_plan_seed(seed: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(seed, dict):
        return {}
    compact = dict(seed)
    ordering_limit = max(1, max_files) + 1
    validation_limit = max(1, max_files) + 2
    compact["primary_symbol"] = _repo_map._compact_symbol_record(
        seed.get("primary_symbol") if isinstance(seed.get("primary_symbol"), dict) else None
    )
    compact["validation_tests"] = _repo_map._list_of_strings(seed.get("validation_tests"))[
        :max_files
    ]
    compact["validation_commands"] = _repo_map._list_of_strings(seed.get("validation_commands"))[
        :validation_limit
    ]
    compact["dependent_files"] = _repo_map._list_of_strings(seed.get("dependent_files"))[:max_files]
    compact["edit_ordering"] = _repo_map._list_of_strings(seed.get("edit_ordering"))[
        :ordering_limit
    ]
    compact["related_spans"] = [
        _repo_map._compact_span_record(current)
        for current in _repo_map._list_of_dicts(seed.get("related_spans"))
    ][:max_files]
    compact["suggested_edits"] = [
        _repo_map._compact_suggested_edit(current)
        for current in _repo_map._list_of_dicts(seed.get("suggested_edits"))
    ][:max_files]
    compact["validation_plan"] = _repo_map._list_of_dicts(seed.get("validation_plan"))[
        : max(1, max_files) + 2
    ]
    return compact


def _compact_navigation_group(group: dict[str, Any], *, max_files: int) -> dict[str, Any]:
    compact = dict(group)
    compact["mentions"] = _repo_map._list_of_strings(group.get("mentions"))[:max_files]
    compact["files"] = _repo_map._list_of_strings(group.get("files"))[:max_files]
    compact["roles"] = _repo_map._list_of_strings(group.get("roles"))[:max_files]
    return compact


def _compact_navigation_pack(pack: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(pack, dict):
        return {}
    compact = dict(pack)
    ordering_limit = max(1, max_files) + 1
    validation_limit = max(1, max_files) + 2
    compact["follow_up_reads"] = _repo_map._list_of_dicts(pack.get("follow_up_reads"))[:max_files]
    compact["parallel_read_groups"] = [
        _repo_map._compact_navigation_group(current, max_files=max_files)
        for current in _repo_map._list_of_dicts(pack.get("parallel_read_groups"))
    ][: max(1, max_files) + 1]
    compact["related_tests"] = _repo_map._list_of_strings(pack.get("related_tests"))[:max_files]
    compact["validation_commands"] = _repo_map._list_of_strings(pack.get("validation_commands"))[
        :validation_limit
    ]
    compact["edit_ordering"] = _repo_map._list_of_strings(pack.get("edit_ordering"))[
        :ordering_limit
    ]
    return compact


def _compact_candidate_edit_targets(targets: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(targets, dict):
        return {}
    compact = dict(targets)
    compact["files"] = _repo_map._list_of_strings(targets.get("files"))[:max_files]
    compact["symbols"] = [
        current
        for current in (
            _repo_map._compact_symbol_record(symbol)
            for symbol in _repo_map._list_of_dicts(targets.get("symbols"))
        )
        if current is not None
    ][:max_files]
    compact["tests"] = _repo_map._list_of_strings(targets.get("tests"))[:max_files]
    compact["spans"] = [
        _repo_map._compact_span_record(current)
        for current in _repo_map._list_of_dicts(targets.get("spans"))
    ][:max_files]
    return compact


def _compact_context_sources(sources: object, *, max_sources: int) -> list[dict[str, Any]]:
    compact_sources: list[dict[str, Any]] = []
    for source in _repo_map._list_of_dicts(sources)[:max_sources]:
        compact_sources.append({key: value for key, value in source.items() if key != "source"})
    return compact_sources


def _compact_context_sections(sections: object) -> list[dict[str, Any]]:
    compact_sections: list[dict[str, Any]] = []
    for section in _repo_map._list_of_dicts(sections):
        keys = ("kind", "start", "end", "token_estimate", "path", "symbol")
        compact_sections.append({key: section[key] for key in keys if key in section})
    return compact_sections


def _top_level_validation_commands(payload: dict[str, Any]) -> list[str]:
    navigation_pack = payload.get("navigation_pack")
    navigation_commands = (
        navigation_pack.get("validation_commands", []) if isinstance(navigation_pack, dict) else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_commands = (
        edit_plan_seed.get("validation_commands", []) if isinstance(edit_plan_seed, dict) else []
    )
    return _repo_map._list_of_strings(navigation_commands or seed_commands)


def _top_level_suggested_validation_commands(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Additive counterpart to `_top_level_validation_commands` â€” surfaces the unverified
    test-neighbor-heuristic suggestion. NEVER read by trust/confidence/tie logic."""
    navigation_pack = payload.get("navigation_pack")
    navigation_suggested = (
        navigation_pack.get("suggested_validation_commands", [])
        if isinstance(navigation_pack, dict)
        else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_suggested = (
        edit_plan_seed.get("suggested_validation_commands", [])
        if isinstance(edit_plan_seed, dict)
        else []
    )
    source = navigation_suggested or seed_suggested
    return [dict(current) for current in source if isinstance(current, dict)]


def _top_level_validation_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Additive parity counterpart to `_top_level_validation_commands` -- surfaces the existing
    STRUCTURED `validation_plan` (list of step dicts with `command`/`confidence`/`detection`/
    `runner`/`scope`/optional `target`) at the payload TOP LEVEL, matching how `tg agent --json`
    already exposes a top-level `validation_plan` (audit: v1.71.1 dogfood gap). Same dual-source
    precedence as `_top_level_validation_commands` (navigation_pack first, edit_plan_seed
    fallback); each step dict is copied so a caller mutating the returned list never mutates
    `edit_plan_seed`/`navigation_pack` in place. Never removes or mutates `validation_commands`."""
    navigation_pack = payload.get("navigation_pack")
    navigation_plan = (
        navigation_pack.get("validation_plan", []) if isinstance(navigation_pack, dict) else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_plan = (
        edit_plan_seed.get("validation_plan", []) if isinstance(edit_plan_seed, dict) else []
    )
    source = navigation_plan or seed_plan
    return [dict(current) for current in source if isinstance(current, dict)]


def _edit_plan_confidence_and_ask(
    payload: dict[str, Any],
    *,
    query: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parity fix : `tg edit-plan --json` had no top-level `confidence`/
    `ask_user_before_editing` even though `tg agent --json` already surfaces both (the same class
    of gap `_top_level_validation_plan` above closed for `validation_plan`). Delegates to
    `agent_capsule._capsule_confidence_and_ask_without_render`, which reproduces agent's confidence
    + ambiguity ladder (INCLUDING the tie/marker-helper ask-gate -- review safety MUST-FIX on
    c63f509) against this exact edit-plan payload, using agent's own helpers. A DEFERRED import,
    since `agent_capsule` imports this module at load time (`from tensor_grep.cli import repo_map`)
    and a module-level import here would cycle; same precedent as this module's existing local
    `orient_capsule` imports a few hundred lines up. See that helper's docstring for exactly which
    genuinely snippet-/call-site-/LSP-evidence-gated enrichments stay agent-only (and why)."""
    from tensor_grep.cli import agent_capsule

    return agent_capsule._capsule_confidence_and_ask_without_render(payload, query=query)


def _compact_context_render_payload(
    payload: dict[str, Any],
    *,
    render_profile: str,
    max_files: int,
    max_sources: int,
) -> dict[str, Any]:
    if render_profile not in _repo_map._COMPACT_CONTEXT_RENDER_PROFILES:
        return payload

    compact = dict(payload)
    omitted_keys: list[str] = []
    for key in _repo_map._COMPACT_CONTEXT_RENDER_OMITTED_KEYS:
        if key in compact:
            compact.pop(key)
            omitted_keys.append(key)
    if render_profile == "llm":
        for key in _repo_map._LLM_CONTEXT_RENDER_OMITTED_KEYS:
            if key in compact:
                compact.pop(key)
                omitted_keys.append(key)

    compact["tests"] = list(compact.get("tests", []))[:max_files]
    compact["sources"] = _repo_map._compact_context_sources(
        compact.get("sources"),
        max_sources=max_sources,
    )
    compact["sections"] = _repo_map._compact_context_sections(compact.get("sections"))
    compact["edit_plan_seed"] = _repo_map._compact_edit_plan_seed(
        compact.get("edit_plan_seed"),
        max_files=max_files,
    )
    compact["navigation_pack"] = _repo_map._compact_navigation_pack(
        compact.get("navigation_pack"),
        max_files=max_files,
    )
    if "candidate_edit_targets" in compact:
        compact["candidate_edit_targets"] = _repo_map._compact_candidate_edit_targets(
            compact.get("candidate_edit_targets"),
            max_files=max_files,
        )
    compact["validation_commands"] = _repo_map._top_level_validation_commands(compact)
    compact["suggested_validation_commands"] = _repo_map._top_level_suggested_validation_commands(
        compact
    )
    compact["context_payload_profile"] = f"{render_profile}-compact"
    compact["payload_compaction"] = {
        "omitted_keys": omitted_keys,
        "raw_source_omitted": True,
        "source_diagnostics_omitted": False,
        "agent_metadata_compacted": True,
        "max_files": max_files,
        "max_sources": max_sources,
    }
    return compact


def _downgrade_ranking_quality(value: str) -> str:
    if value == "strong":
        return "moderate"
    if value == "moderate":
        return "weak"
    return "weak"


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _span_bounds(span: object) -> tuple[int, int] | None:
    if not isinstance(span, dict):
        return None
    start = _repo_map._int_or_none(span.get("start_line") or span.get("line"))
    end = _repo_map._int_or_none(span.get("end_line") or start)
    if start is None:
        return None
    if end is None:
        end = start
    return start, max(start, end)


def _line_map_overlaps_span(line_map: object, span: object) -> bool:
    bounds = _repo_map._span_bounds(span)
    if bounds is None:
        return False
    span_start, span_end = bounds
    for row in _repo_map._list_of_dicts(line_map):
        row_start = _repo_map._int_or_none(
            row.get("original_start_line")
            or row.get("original_line")
            or row.get("line")
            or row.get("start_line")
        )
        if row_start is None:
            continue
        row_end = (
            _repo_map._int_or_none(row.get("original_end_line") or row.get("end_line")) or row_start
        )
        row_end = max(row_start, row_end)
        if row_start <= span_end and row_end >= span_start:
            return True
    return False


def _line_map_covers_span(line_map: object, span: object) -> bool:
    bounds = _repo_map._span_bounds(span)
    if bounds is None:
        return False
    span_start, span_end = bounds
    next_required = span_start
    rows: list[tuple[int, int]] = []
    for row in _repo_map._list_of_dicts(line_map):
        row_start = _repo_map._int_or_none(
            row.get("original_start_line")
            or row.get("original_line")
            or row.get("line")
            or row.get("start_line")
        )
        if row_start is None:
            continue
        row_end = (
            _repo_map._int_or_none(row.get("original_end_line") or row.get("end_line")) or row_start
        )
        rows.append((row_start, max(row_start, row_end)))
    for row_start, row_end in sorted(rows):
        if row_end < next_required:
            continue
        if row_start > next_required:
            return False
        if row_end >= span_end:
            return True
        next_required = row_end + 1
    return False


def _source_includes_primary_symbol(
    source: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(source.get("file", "") or "") != primary_file:
        return False
    source_symbol = str(source.get("name") or source.get("symbol") or "")
    if primary_symbol_name and source_symbol == primary_symbol_name:
        return True
    return _repo_map._line_map_overlaps_span(source.get("line_map"), primary_span)


def _source_truncates_primary_symbol(
    source: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(source.get("file", "") or "") != primary_file:
        return False
    source_budget = source.get("source_budget")
    focused_omissions = source.get("focus", {}).get("omitted_line_count", 0)
    if not focused_omissions and not (
        isinstance(source_budget, dict) and source_budget.get("truncated")
    ):
        return False
    source_symbol = str(source.get("name") or source.get("symbol") or "")
    overlaps_primary_span = _repo_map._line_map_overlaps_span(source.get("line_map"), primary_span)
    if primary_symbol_name and source_symbol != primary_symbol_name and not overlaps_primary_span:
        return False
    if _repo_map._span_bounds(primary_span) is None:
        return True
    return not _repo_map._line_map_covers_span(source.get("line_map"), primary_span)


def _section_includes_primary_symbol(
    section: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(section.get("kind", "")) != "source":
        return False
    if str(section.get("path", "") or "") != primary_file:
        return False
    section_symbol = str(section.get("symbol") or "")
    if primary_symbol_name and section_symbol == primary_symbol_name:
        return True
    bounds = _repo_map._span_bounds(primary_span)
    section_start = _repo_map._int_or_none(
        section.get("original_start_line") or section.get("start_line")
    )
    section_end = _repo_map._int_or_none(
        section.get("original_end_line") or section.get("end_line")
    )
    if bounds is None or section_start is None:
        return False
    if section_end is None:
        section_end = section_start
    span_start, span_end = bounds
    return section_start <= span_end and max(section_start, section_end) >= span_start


def _ensure_primary_source_in_sources(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    max_sources: int,
    render_profile: str,
    optimize_context: bool,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> list[dict[str, Any]]:
    edit_plan_seed = payload.get("edit_plan_seed")
    if not isinstance(edit_plan_seed, dict):
        return sources
    primary_file = str(edit_plan_seed.get("primary_file") or "")
    primary_symbol = edit_plan_seed.get("primary_symbol")
    primary_symbol_name = (
        str(primary_symbol.get("name") or "") if isinstance(primary_symbol, dict) else ""
    )
    if not primary_file or not primary_symbol_name:
        return sources
    primary_span = edit_plan_seed.get("primary_span") or primary_symbol
    hits = [
        i
        for i, source in enumerate(sources)
        if _repo_map._source_includes_primary_symbol(
            source,
            primary_file=primary_file,
            primary_symbol_name=primary_symbol_name,
            primary_span=primary_span,
        )
    ]
    if hits:  # present: move it to the front so a greedy source budget cannot starve it
        return [sources[hits[0]], *sources[: hits[0]], *sources[hits[0] + 1 :]]

    primary_source: dict[str, Any] | None = None
    primary_source_payload = _repo_map.build_symbol_source_from_map(
        repo_map,
        primary_symbol_name,
        _profiling_collector=_profiling_collector,
    )
    for source in _repo_map._list_of_dicts(primary_source_payload.get("sources")):
        if str(source.get("file", "") or "") != primary_file:
            continue
        primary_source = _repo_map._render_source_block(
            source,
            render_profile=render_profile,
            optimize_context=optimize_context,
            _profiling_collector=_profiling_collector,
        )
        break
    if primary_source is None:
        return sources

    filtered_sources = [
        source for source in sources if str(source.get("file", "") or "") != primary_file
    ]
    return [primary_source, *filtered_sources][:max_sources]


def _apply_context_consistency_invariants(payload: dict[str, Any]) -> dict[str, Any]:
    edit_plan_seed = payload.get("edit_plan_seed")
    navigation_pack = payload.get("navigation_pack")
    primary_file = (
        str(edit_plan_seed.get("primary_file") or "") if isinstance(edit_plan_seed, dict) else ""
    )
    raw_primary_symbol = (
        edit_plan_seed.get("primary_symbol") if isinstance(edit_plan_seed, dict) else None
    )
    primary_symbol = dict(raw_primary_symbol) if isinstance(raw_primary_symbol, dict) else {}
    primary_symbol_name = str(primary_symbol.get("name") or "")
    primary_span = edit_plan_seed.get("primary_span") if isinstance(edit_plan_seed, dict) else {}
    raw_primary_target = (
        navigation_pack.get("primary_target") if isinstance(navigation_pack, dict) else None
    )
    primary_target = dict(raw_primary_target) if isinstance(raw_primary_target, dict) else {}
    navigation_primary_file = str(primary_target.get("file", "") or "")
    files = {str(current) for current in payload.get("files", []) if str(current)}
    source_files = {
        str(source.get("file", ""))
        for source in _repo_map._list_of_dicts(payload.get("sources"))
        if str(source.get("file", ""))
    }
    follow_up_files = set()
    if isinstance(navigation_pack, dict):
        follow_up_files = {
            str(read.get("file", ""))
            for read in _repo_map._list_of_dicts(navigation_pack.get("follow_up_reads"))
            if str(read.get("file", ""))
        }
    rendered_files = {
        str(section.get("path", ""))
        for section in _repo_map._list_of_dicts(payload.get("sections"))
        if str(section.get("kind", "")) in {"summary", "source"} and str(section.get("path", ""))
    }
    primary_symbol_included = bool(
        not primary_file
        or not primary_symbol_name
        or any(
            _repo_map._source_includes_primary_symbol(
                source,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for source in _repo_map._list_of_dicts(payload.get("sources"))
        )
    )
    rendered_includes_primary_symbol = bool(
        not primary_file
        or not primary_symbol_name
        or any(
            _repo_map._section_includes_primary_symbol(
                section,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for section in _repo_map._list_of_dicts(payload.get("sections"))
        )
    )
    primary_symbol_truncated = bool(
        primary_file
        and primary_symbol_name
        and any(
            _repo_map._source_truncates_primary_symbol(
                source,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for source in _repo_map._list_of_dicts(payload.get("sources"))
        )
    )

    included = bool(primary_file and primary_file in (files | source_files | follow_up_files))
    render_matches_primary_target = bool(
        not primary_file or not navigation_primary_file or primary_file == navigation_primary_file
    )
    rendered_includes_primary = bool(not primary_file or primary_file in rendered_files)
    omitted_reason = None
    if primary_file and not included:
        omitted_reason = "primary file was outside the selected file/source/read budgets"
    elif primary_file and not rendered_includes_primary:
        omitted_reason = (
            "primary file metadata was selected but omitted from rendered_context budget"
        )
    elif primary_file and primary_symbol_truncated:
        omitted_reason = _focused_rendering.primary_omission_reason(payload, primary_file)
    elif primary_file and not rendered_includes_primary_symbol:
        omitted_reason = "primary_symbol_omitted_from_rendered_context"

    confidence_downgraded = False
    if primary_file and (
        not render_matches_primary_target
        or not rendered_includes_primary
        or not rendered_includes_primary_symbol
        or primary_symbol_truncated
    ):
        ranking_quality = str(payload.get("ranking_quality", "weak"))
        downgraded_quality = _repo_map._downgrade_ranking_quality(ranking_quality)
        if downgraded_quality != ranking_quality:
            payload["ranking_quality"] = downgraded_quality
        confidence_downgraded = True
        if isinstance(edit_plan_seed, dict):
            confidence = dict(edit_plan_seed.get("confidence", {}))
            for key, value in list(confidence.items()):
                try:
                    confidence[key] = round(float(value) * 0.75, 3)
                except (TypeError, ValueError):
                    continue
            edit_plan_seed["confidence"] = confidence

    validation_alignment = (
        dict(edit_plan_seed.get("validation_alignment", {}))
        if isinstance(edit_plan_seed, dict)
        and isinstance(edit_plan_seed.get("validation_alignment"), dict)
        else {}
    )
    validation_filtered_count = int(validation_alignment.get("filtered_count", 0) or 0)
    validation_kept_count = int(validation_alignment.get("kept_count", 0) or 0)
    if validation_filtered_count > 0 and validation_kept_count == 0:
        confidence_downgraded = True
    primary_target_language = _repo_map._target_language_for_path(primary_file)

    payload["context_consistency"] = {
        "primary_file": primary_file or None,
        "navigation_primary_file": navigation_primary_file or None,
        "query_language_hints": _repo_map._query_language_hints(
            str(payload.get("query", "") or "")
        ),
        "primary_target_language": primary_target_language,
        "validation_alignment": validation_alignment,
        "validation_filtered_count": validation_filtered_count,
        "primary_file_included": included,
        "primary_symbol": primary_symbol_name or None,
        "primary_symbol_included": primary_symbol_included,
        "primary_symbol_truncated": primary_symbol_truncated,
        "render_matches_primary_target": render_matches_primary_target,
        "rendered_context_includes_primary": rendered_includes_primary,
        "rendered_context_includes_primary_symbol": rendered_includes_primary_symbol,
        "confidence_downgraded": confidence_downgraded,
        "omitted_primary_reason": omitted_reason,
    }
    if omitted_reason:
        omitted_sections = _repo_map._list_of_dicts(payload.get("omitted_sections"))
        omitted_sections.append({
            "kind": "primary",
            # Emit JSON null (not the string "None") when no primary file
            # resolved, matching follow_up_reads[].file semantics.
            "file": primary_file or None,
            "reason": omitted_reason,
        })
        payload["omitted_sections"] = omitted_sections
    return payload


def build_context_render_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    include_edit_plan_seed: bool = True,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    # #222 (call-2 enumeration-gap fix): `deadline_hit` is a NEW, additive, default-None
    # passthrough to `build_context_pack_from_map` -- every pre-existing caller (repo_map's own
    # `build_context_render`, session_store's warm-path renders) passes none and is byte-
    # identical to before. It lets a caller that already owns a `_DeadlineBreakFlag` (agent_
    # capsule's `build_agent_capsule_from_map`) observe "did ANY sibling stage INSIDE the pack
    # build (symbol-scoring, pagerank, or this render's own auto_deweight `_detect_vendored_
    # subtrees` call) cut short" -- previously unobservable here, so a truncation inside this
    # call only ever surfaced as the generic `payload["partial"]`, with nothing naming WHICH
    # assembly stage actually consumed the budget.
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    context_payload = _repo_map.build_context_pack_from_map(
        repo_map,
        query,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
        _profiling_collector=collector,
    )
    normalized_profile = _repo_map._normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)
    top_files = {str(current) for current in context_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    seen_source_files: set[str] = set()
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in context_payload.get("symbols", []):
        symbols_by_file.setdefault(str(symbol["file"]), []).append(dict(symbol))
    ordered_symbols: list[dict[str, Any]] = []
    for current_file in context_payload.get("files", [])[:max_files]:
        ordered_symbols.extend(symbols_by_file.get(str(current_file), []))
    for symbol in ordered_symbols:
        current_file = str(symbol["file"])
        if current_file not in top_files:
            continue
        if current_file in seen_source_files:
            continue
        symbol_key = (current_file, str(symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = _repo_map.build_symbol_source_from_map(
            repo_map,
            str(symbol["name"]),
            deadline_monotonic=deadline_monotonic,
            include_tests=False,
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
            seen_source_files.add(current_file)
            break
        if len(sources) >= max_sources:
            break

    if len(sources) < max_sources:
        for current_file in context_payload.get("files", [])[:max_files]:
            current_file = str(current_file)
            if current_file in seen_source_files:
                continue
            fallback_source = _repo_map._fallback_file_source(_repo_map.Path(current_file))
            if fallback_source is None:
                continue
            sources.append(
                _repo_map._render_source_block(
                    fallback_source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            seen_source_files.add(current_file)
            if len(sources) >= max_sources:
                break

    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    source_budget: dict[str, Any] | None = None
    source_omitted_sections: list[dict[str, Any]] = []
    payload = dict(context_payload)
    payload["routing_reason"] = "context-render"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    selected_file_set = {str(current) for current in payload["files"]}
    payload["tests"] = list(payload.get("tests", []))[:max_files]
    selected_test_set = {str(current) for current in payload["tests"]}
    payload["test_matches"] = list(payload.get("test_matches", []))[:max_files]
    payload["symbols"] = [
        dict(symbol)
        for symbol in payload.get("symbols", [])
        if str(symbol.get("file", "")) in selected_file_set
    ][: max_files * max_symbols_per_file]
    payload["imports"] = [
        dict(entry)
        for entry in payload.get("imports", [])
        if str(entry.get("file", "")) in selected_file_set
    ][:max_files]
    payload["related_paths"] = [
        str(path)
        for path in payload.get("related_paths", [])
        if str(path) in selected_file_set | selected_test_set
    ]
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["max_tokens"] = normalized_max_tokens
    payload["model"] = model
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    payload["semantic_provider"] = _repo_map._normalize_semantic_provider(semantic_provider)
    if include_edit_plan_seed:
        payload = _repo_map._attach_edit_plan_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
            max_depth=_repo_map._DEFAULT_EDIT_PLAN_MAX_DEPTH,
            semantic_provider=semantic_provider,
            deadline_monotonic=deadline_monotonic,
            _profiling_collector=collector,
            # Bound suggested edits in every profile, including those without downstream compaction.
            suggested_edits_max=max_files,
        )
    else:
        payload = _repo_map._attach_lightweight_navigation_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
            deadline_monotonic=deadline_monotonic,
        )
    payload["validation_commands"] = _repo_map._top_level_validation_commands(payload)
    payload["suggested_validation_commands"] = _repo_map._top_level_suggested_validation_commands(
        payload
    )
    sources = _repo_map._ensure_primary_source_in_sources(
        repo_map,
        payload,
        sources,
        max_sources=max_sources,
        render_profile=normalized_profile,
        optimize_context=optimize_context,
        _profiling_collector=collector,
    )
    if normalized_profile == "focused":
        sources = _focused_rendering.focus_sources(sources, query)
    focused_omissions = _focused_rendering.omissions(sources)
    sources, source_budget, source_omitted_sections = _repo_map._apply_source_output_budget(
        sources,
        max_tokens=normalized_max_tokens,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["sources"] = sources
    source_omitted_sections += focused_omissions
    if source_budget is not None:
        payload["source_budget"] = source_budget
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
    payload["truncated"] = truncated or bool(source_omitted_sections)
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = [*source_omitted_sections, *omitted_sections]
    payload = _repo_map._apply_context_consistency_invariants(payload)
    payload = _repo_map._compact_context_render_payload(
        payload,
        render_profile=normalized_profile,
        max_files=max_files,
        max_sources=max_sources,
    )
    # #642 gate nit-1 fast-follow: #642 added this SAME final wall-clock catch-all to
    # build_agent_capsule_from_map (agent_capsule.py) ONLY -- a caller reaching THIS function
    # directly (the shared render core behind `tg context-render`, both the cold build_context_
    # render path and the warm session-daemon route in session_store.py) never got the return-time
    # recheck. `payload.get("partial")` already folds in every stage this function's own callees
    # thread a deadline flag through (build_context_pack_from_map's own_deadline_hit above, and --
    # as of this same fix -- the edit-plan-seed's validation-plan chain via
    # _attach_edit_plan_metadata); OR that with one absolute wall-clock recheck here so ANY sibling
    # stage this function calls -- instrumented or not -- can never silently return exit 0 once the
    # caller's budget is gone. Mirrors agent_capsule.py's `deadline_exceeded_at_return` verbatim.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic
    )
    _repo_map._stamp_partial_status(payload, deadline_exceeded=deadline_exceeded_at_return)
    return _repo_map._attach_profiling(payload, collector)


def build_context_render_json(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
) -> str:
    payload = _repo_map.build_context_render(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        semantic_provider=semantic_provider,
        deadline_seconds=deadline_seconds,
        profile=profile,
    )
    if payload.get("render_profile") == "llm":
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, indent=2)
