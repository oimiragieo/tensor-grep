from __future__ import annotations

import math
from typing import Any

from tensor_grep.cli import repo_map as _repo_map


def _estimate_tokens_for_len(length: int) -> int:
    """The SINGLE source of the chars->tokens estimate. `_estimate_tokens` (the text-taking
    wrapper) and `_truncate_source_text_to_budget`'s running-length fast path MUST both go
    through here -- a duplicated formula would let the truncation loop silently desync from
    the budget check if the estimate ever changes."""
    if length <= 0:
        return 0
    return max(1, math.ceil(length / 3.5))


def _estimate_tokens(
    text: str,
    *,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> int:
    with _repo_map._profiling_phase(_profiling_collector, "token_estimation"):
        return _repo_map._estimate_tokens_for_len(len(text))


def _line_map_for_budgeted_lines(
    line_map: list[dict[str, int]],
    selected_line_numbers: list[int],
) -> list[dict[str, int]]:
    rendered_to_original: dict[int, int] = {}
    for segment in line_map:
        rendered_start = int(segment.get("rendered_start_line", 0))
        rendered_end = int(segment.get("rendered_end_line", 0))
        original_start = int(segment.get("original_start_line", 0))
        if rendered_start <= 0 or rendered_end < rendered_start or original_start <= 0:
            continue
        for rendered_line in range(rendered_start, rendered_end + 1):
            rendered_to_original[rendered_line] = original_start + rendered_line - rendered_start

    budgeted: list[dict[str, int]] = []
    for new_rendered_line, previous_rendered_line in enumerate(selected_line_numbers, start=1):
        original_line = rendered_to_original.get(previous_rendered_line)
        if original_line is None:
            continue
        if budgeted and original_line == budgeted[-1]["original_end_line"] + 1:
            budgeted[-1]["rendered_end_line"] = new_rendered_line
            budgeted[-1]["original_end_line"] = original_line
            continue
        budgeted.append({
            "rendered_start_line": new_rendered_line,
            "rendered_end_line": new_rendered_line,
            "original_start_line": original_line,
            "original_end_line": original_line,
        })
    return budgeted


def _source_text_within_budget(
    text: str,
    *,
    max_tokens: int | None,
    max_chars: int | None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> bool:
    if max_chars is not None and len(text) > max_chars:
        return False
    if (
        max_tokens is not None
        and _repo_map._estimate_tokens(
            text,
            _profiling_collector=_profiling_collector,
        )
        > max_tokens
    ):
        return False
    return True


def _truncate_source_text_to_budget(
    text: str,
    *,
    max_tokens: int | None,
    max_chars: int | None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> tuple[str, list[int], bool]:
    if _repo_map._source_text_within_budget(
        text,
        max_tokens=max_tokens,
        max_chars=max_chars,
        _profiling_collector=_profiling_collector,
    ):
        line_count = len(text.splitlines())
        return text, list(range(1, line_count + 1)), False

    # perf (O(k^2) -> O(k)): `_source_text_within_budget` (and the `_estimate_tokens` it calls) is
    # a PURE function of `len(text)` -- `_estimate_tokens` is `max(1, ceil(len(text)/3.5))` (0 for
    # an empty string) and never reads text content, only `len(text)`, `max_tokens`, `max_chars`
    # (see the two functions right above this one). The two loops below used to rebuild the FULL
    # candidate string on every iteration just to hand it to that length-only check
    # (`"".join([*selected_lines, line])`, and the same shape rebuilding `candidate_lines` in the
    # tail-rescue loop) -- each rebuild is O(current total length), making the loops O(k^2) on a
    # k-line file. `_within_budget_for_len` reproduces the identical pass/fail decision from a
    # running integer length instead, so `selected_lines`/`selected_indexes`/the final joined text
    # come out byte-identical while both loops become O(k) (see
    # tests/unit/test_token_budget.py::test_truncate_*_is_byte_identical for the pinned-output
    # regression coverage, captured from this function before the rewrite).
    def _within_budget_for_len(candidate_len: int) -> bool:
        if max_chars is not None and candidate_len > max_chars:
            return False
        if (
            max_tokens is not None
            and _repo_map._estimate_tokens_for_len(candidate_len) > max_tokens
        ):
            return False
        return True

    lines = text.splitlines(keepends=True)
    selected_lines: list[str] = []
    selected_indexes: list[int] = []
    running_len = 0

    for index, line in enumerate(lines, start=1):
        candidate_len = running_len + len(line)
        if not _within_budget_for_len(candidate_len):
            break
        selected_lines.append(line)
        selected_indexes.append(index)
        running_len = candidate_len

    tail_line: tuple[int, str] | None = None
    for index in range(len(lines), 0, -1):
        line = lines[index - 1]
        stripped = line.strip()
        if stripped.startswith(("return ", "raise ", "yield ", "assert ")):
            tail_line = (index, line)
            break

    if tail_line is not None and tail_line[0] not in selected_indexes:
        tail_text = tail_line[1]
        tail_len = len(tail_text)
        last_selected = max(selected_indexes, default=0)
        omitted_between = max(0, tail_line[0] - last_selected - 1)
        marker = f"# ... {omitted_between} lines omitted by source budget ...\n"
        candidate_len = running_len + len(marker) + tail_len
        while selected_lines and not _within_budget_for_len(candidate_len):
            popped = selected_lines.pop()
            selected_indexes.pop()
            running_len -= len(popped)
            last_selected = max(selected_indexes, default=0)
            omitted_between = max(0, tail_line[0] - last_selected - 1)
            marker = f"# ... {omitted_between} lines omitted by source budget ...\n"
            candidate_len = running_len + len(marker) + tail_len
        if _within_budget_for_len(candidate_len):
            selected_lines = [*selected_lines, marker, tail_text]
            selected_indexes = [*selected_indexes, 0, tail_line[0]]

    if not selected_lines and lines:
        chunk = lines[0]
        if max_chars is not None:
            chunk = chunk[:max_chars]
        while (
            max_tokens is not None
            and _repo_map._estimate_tokens(
                chunk,
                _profiling_collector=_profiling_collector,
            )
            > max_tokens
            and len(chunk) > 1
        ):
            chunk = chunk[: max(1, int(len(chunk) * 0.75))]
        selected_lines = [chunk]
        selected_indexes = [1]

    return "".join(selected_lines).rstrip("\n"), selected_indexes, True


def _prepare_render_part(
    part: dict[str, Any],
    *,
    has_prior_content: bool,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any] | None:
    text = str(part.get("text", "")).strip()
    if not text:
        return None
    prefix = "" if not has_prior_content else "\n\n"
    chunk = f"{prefix}{text}"
    prepared = {key: value for key, value in part.items() if key != "text"}
    prepared["text"] = text
    prepared["chunk"] = chunk
    prepared["score"] = _repo_map._render_part_score(part)
    prepared["token_estimate"] = _repo_map._estimate_tokens(
        chunk,
        _profiling_collector=_profiling_collector,
    )
    return prepared


def _omitted_section_record(part: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(part.get("kind", "")),
        "file": _repo_map._render_part_path(part),
        "symbol": part.get("symbol"),
        "score": int(part.get("score", 0)),
        "token_estimate": int(part.get("token_estimate", 0)),
    }


def _render_context_string_and_sections(
    payload: dict[str, Any],
    *,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> tuple[str, list[dict[str, Any]], bool, int, list[dict[str, Any]]]:
    with _repo_map._profiling_phase(_profiling_collector, "render_packing"):
        max_render_chars = (
            max_render_chars if max_render_chars is not None and max_render_chars > 0 else None
        )
        resolved_max_tokens = max_tokens if max_tokens is not None else payload.get("max_tokens")
        max_tokens = (
            int(resolved_max_tokens)
            if resolved_max_tokens is not None and int(resolved_max_tokens) > 0
            else None
        )
        _ = model if model is not None else payload.get("model")
        primary_file = None
        edit_plan_seed = payload.get("edit_plan_seed")
        if isinstance(edit_plan_seed, dict):
            current_primary_file = edit_plan_seed.get("primary_file")
            if current_primary_file:
                primary_file = str(current_primary_file)
        if primary_file is None:
            files = payload.get("files", [])
            if isinstance(files, list) and files:
                primary_file = str(files[0])

        raw_parts = _repo_map._render_context_parts(payload)
        query_parts: list[dict[str, Any]] = []
        ranked_parts: list[tuple[dict[str, Any], int]] = []
        for index, part in enumerate(raw_parts):
            if str(part.get("kind", "")) == "query":
                query_parts.append(part)
                continue
            ranked_parts.append((part, index))
        ranked_parts.sort(
            key=lambda item: _repo_map._render_part_sort_key(
                item[0],
                primary_file=primary_file,
                original_index=item[1],
            )
        )
        parts = [*query_parts, *[part for part, _ in ranked_parts]]

        selected_parts: list[dict[str, Any]] = []
        omitted_parts: list[dict[str, Any]] = []
        non_query_selected = 0
        estimated_total_tokens = 0
        budget_exhausted = False

        for part in parts:
            prepared = _repo_map._prepare_render_part(
                part,
                has_prior_content=bool(selected_parts),
                _profiling_collector=_profiling_collector,
            )
            if prepared is None:
                continue
            if str(prepared.get("kind", "")) == "query":
                selected_parts.append(prepared)
                estimated_total_tokens += int(prepared["token_estimate"])
                continue
            if budget_exhausted:
                omitted_parts.append(prepared)
                continue
            current_token_estimate = estimated_total_tokens + int(prepared["token_estimate"])
            if max_tokens is None or current_token_estimate <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                continue
            if non_query_selected == 0 and estimated_total_tokens <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                budget_exhausted = True
                continue
            omitted_parts.append(prepared)

        sections: list[dict[str, Any]] = []
        rendered_parts: list[str] = []
        offset = 0
        total_token_estimate = 0
        truncated = bool(omitted_parts)
        char_omitted_parts: list[dict[str, Any]] = []
        for index, part in enumerate(selected_parts):
            chunk = str(part["chunk"])
            if max_render_chars is not None and offset + len(chunk) > max_render_chars:
                remaining = max_render_chars - offset
                partially_rendered_current = False
                if offset == 0 and remaining > 0:
                    chunk = chunk[:remaining]
                    section_token_estimate = _repo_map._estimate_tokens(
                        chunk,
                        _profiling_collector=_profiling_collector,
                    )
                    rendered_parts.append(chunk)
                    sections.append({
                        "kind": str(part["kind"]),
                        "start": offset,
                        "end": offset + len(chunk),
                        "token_estimate": section_token_estimate,
                        **{
                            key: value
                            for key, value in part.items()
                            if key not in {"text", "chunk", "score", "token_estimate"}
                        },
                    })
                    offset += len(chunk)
                    total_token_estimate += section_token_estimate
                    partially_rendered_current = True
                char_omitted_parts = selected_parts[
                    index + (1 if partially_rendered_current else 0) :
                ]
                truncated = True
                break
            rendered_parts.append(chunk)
            section_token_estimate = int(part["token_estimate"])
            sections.append({
                "kind": str(part["kind"]),
                "start": offset,
                "end": offset + len(chunk),
                "token_estimate": section_token_estimate,
                **{
                    key: value
                    for key, value in part.items()
                    if key not in {"text", "chunk", "score", "token_estimate"}
                },
            })
            offset += len(chunk)
            total_token_estimate += section_token_estimate
        omitted_sections = [
            _repo_map._omitted_section_record(part)
            for part in [*char_omitted_parts, *omitted_parts]
        ]
        return (
            "".join(rendered_parts).rstrip(),
            sections,
            truncated,
            total_token_estimate,
            omitted_sections,
        )


def _normalize_render_profile(render_profile: str, optimize_context: bool) -> str:
    profile = render_profile.strip().lower() or "full"
    if profile not in _repo_map._RENDER_PROFILES:
        raise ValueError(f"Unsupported render profile: {render_profile}")
    if optimize_context and profile == "full":
        return "compact"
    return profile


def _is_comment_line(path: _repo_map.Path, line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if path.suffix.lower() == ".py":
        return stripped.startswith("#")
    if path.suffix.lower() in _repo_map._JS_TS_SUFFIXES | _repo_map._RUST_SUFFIXES:
        return (
            stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.startswith("*/")
        )
    return False


def _ts_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    jsdoc_lines = _repo_map._js_ast_omitted_relative_lines(block)
    type_import_lines: set[int] = set()
    in_type_import = False

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not in_type_import:
            if not stripped.startswith("import type"):
                continue
            in_type_import = True

        type_import_lines.add(line_number)
        if ";" in stripped:
            in_type_import = False

    return jsdoc_lines, type_import_lines


def _confidence_from_score(score: int) -> float:
    if score <= 0:
        return 0.0
    return round(min(1.0, 0.35 + (score / 20.0)), 3)


def _derive_seed_overall(confidence: dict[str, Any]) -> float:
    """H4 audit: derive the seed's single ``overall`` confidence from the per-axis scores the
    ranking already computed, so consumers never fall back to a flat 0.9 default. ``file``
    anchors the primary target (the capsule edits a file even when no symbol matched); ``symbol``
    strengthens it and is legitimately 0.0 when a query had no symbol; ``test`` is a separate
    validation axis (0.0 whenever no test matched) and must not drag the answer-confidence down.
    Returns raw 0.0..1.0 -- callers apply their own caps (e.g. the 0.65 filtered-alignment cap in
    `_build_edit_plan_seed`, which runs BEFORE this so the derived overall inherits it)."""

    def _as_score(value: Any) -> float:
        try:
            numeric = float(value or 0.0)
        except (TypeError, ValueError):
            numeric = 0.0
        return numeric if numeric >= 0.0 else 0.0

    return round(
        max(_as_score(confidence.get("file")), _as_score(confidence.get("symbol"))),
        3,
    )


def _primary_span_for_symbol(symbol: dict[str, Any] | None) -> dict[str, int] | None:
    if symbol is None:
        return None
    start_line = symbol.get("start_line", symbol.get("line"))
    end_line = symbol.get("end_line", start_line)
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return None
    return {
        "start_line": start_line,
        "end_line": end_line,
    }


def build_context_render(
    query: str,
    path: str | _repo_map.Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
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
    _profiling_collector: _repo_map._ProfileCollector | None = None,
    ignore: tuple[str, ...] = (),
    include_suggested_scope: bool = False,
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline, used AS-IS instead of being recomputed from
    ``deadline_seconds`` when supplied. The CLI cold path (``main.context_render``) anchors it at
    command entry, before path resolution and the daemon gate, so front-door time is budgeted the
    same way scan time already is. Existing ``deadline_seconds``-only callers are unaffected: the
    fallback computation below is byte-identical to the prior behavior."""
    collector = _repo_map._resolve_profiling_collector(
        profile=profile, collector=_profiling_collector
    )
    # CLI consistency fix : `--deadline` used to be undefined on
    # `tg context-render`/`tg agent` (Click "No such option" exit-2). Converted ONCE (capability P0-6
    # step-3 pattern) and shared across the repo-map build AND the render's own symbol-scoring pass.
    if deadline_monotonic is None:
        deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = _repo_map.build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    if ignore:
        # Local import avoids a module-level circular import (orient_capsule imports this module);
        # reuses orient's tested glob filter (`tg orient --ignore`, PR #392) so `tg agent --ignore`
        # drops the same vendor/skill CODE trees from ranking before centrality/context-pack scoring.
        from tensor_grep.cli.orient_capsule import _apply_ignore_globs

        repo_map = _apply_ignore_globs(repo_map, ignore)
    render = _repo_map.build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        include_edit_plan_seed=include_edit_plan_seed,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        semantic_provider=semantic_provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    if include_suggested_scope:
        # suggested_scope (audit #93 SUB-2 / #133 dogfood): the SAME centrality-weighted directory
        # rollup `tg orient` emits, computed from the raw map we ALREADY built above -- no second
        # scan. Gated on the map's OWN unfinished-scan state (a complete scan has nothing
        # left to narrow), mirroring orient_capsule's gate. Task 332: that gate read
        # `scan_limit.possibly_truncated` directly and so never fired on a --deadline cutoff --
        # the render that most needs a "narrow to here" hint (the clock ran out) was the one
        # render that never offered one. Reuses orient's tested helper; the local
        # import avoids the module-level cycle (orient_capsule imports this module), exactly like the
        # `_apply_ignore_globs` reuse above. Additive + conditional: absent unless the scan was
        # truncated AND a clear winner exists, so a non-truncated render stays byte-identical.
        #
        # #179: also thread in the SAME auto-detected vendor/skill/tool-config tree set `tg orient`
        # (#168/#606) and `tg agent` (agent_capsule.py's own #179 fix) exclude from their own
        # suggested_scope rollup -- otherwise this wrapper (behind `tg context-render`, and any other
        # `include_suggested_scope=True` caller) could point an agent at a tree the sibling commands
        # already know to avoid, on the exact same repo map.
        if _repo_map._scan_did_not_finish(repo_map):
            from tensor_grep.cli.orient_capsule import (
                _detect_vendored_subtrees,
                _suggested_scope_from_map,
            )

            deweighted_trees = _detect_vendored_subtrees(
                repo_map, deadline_monotonic=deadline_monotonic
            )
            suggested_scope = _suggested_scope_from_map(
                repo_map,
                deweighted_trees=deweighted_trees,
                deadline_monotonic=deadline_monotonic,
            )
            if suggested_scope is not None:
                render["suggested_scope"] = suggested_scope
    return render


def _fallback_file_source(
    path: _repo_map.Path, *, max_lines: int = 120, max_chars: int = 6_000
) -> dict[str, Any] | None:
    source_suffixes = (
        _repo_map._FALLBACK_SOURCE_SUFFIXES
        | _repo_map._JS_TS_SUFFIXES
        | _repo_map._RUST_SUFFIXES
        | {".py"}
    )
    if path.suffix.lower() not in source_suffixes:
        return None
    try:
        text = _repo_map._read_text_with_receipt(path)
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    if not lines:
        return None
    snippet_lines: list[str] = []
    rendered_chars = 0
    for line in lines[:max_lines]:
        rendered_chars += len(line) + 1
        if rendered_chars > max_chars:
            break
        snippet_lines.append(line)
    if not snippet_lines:
        return None
    source = "\n".join(snippet_lines)
    if not source.endswith("\n"):
        source = f"{source}\n"
    return {
        "name": path.name,
        "kind": "file",
        "file": str(path),
        "start_line": 1,
        "end_line": len(snippet_lines),
        "source": source,
    }
