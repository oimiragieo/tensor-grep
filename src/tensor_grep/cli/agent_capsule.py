from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _numeric_confidence(value: object, fallback: float = 0.9) -> float:
    if not isinstance(value, str | int | float):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _cap_primary_target_confidence(target: dict[str, Any], cap: float) -> None:
    target["confidence"] = round(min(_numeric_confidence(target.get("confidence")), cap), 3)


def _cap_alternative_target_confidences(
    alternatives: list[dict[str, Any]],
    primary_target: dict[str, Any],
) -> None:
    primary_confidence = _numeric_confidence(primary_target.get("confidence"))
    for alternative in alternatives:
        alternative["confidence"] = round(
            min(_numeric_confidence(alternative.get("confidence")), primary_confidence),
            3,
        )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _capsule_validation_alignment(
    target: dict[str, Any],
    validation_plan: list[dict[str, Any]],
    validation_commands: list[str],
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    aligned_plan, computed_alignment = repo_map._align_validation_plan_for_primary_language(
        validation_plan,
        str(target.get("file") or ""),
    )
    edit_alignment = _as_dict(_as_dict(payload.get("edit_plan_seed")).get("validation_alignment"))
    payload_alignment = _as_dict(
        _as_dict(payload.get("context_consistency")).get("validation_alignment")
    )
    alignment = edit_alignment or payload_alignment or computed_alignment
    if int(computed_alignment.get("filtered_count", 0) or 0) > int(
        alignment.get("filtered_count", 0) or 0
    ):
        alignment = computed_alignment

    if aligned_plan:
        allowed_commands = {str(step.get("command") or "") for step in aligned_plan}
        aligned_commands = [
            command for command in validation_commands if command in allowed_commands
        ]
        if not aligned_commands:
            aligned_commands = [str(step["command"]) for step in aligned_plan]
    elif int(alignment.get("filtered_count", 0) or 0) > 0:
        aligned_commands = []
    else:
        aligned_commands = validation_commands
    return aligned_plan, aligned_commands, alignment


def _capsule_trust_checks(
    query: str,
    target: dict[str, Any],
    snippets: list[dict[str, Any]],
    validation_commands: list[str],
    validation_alignment: dict[str, Any],
) -> dict[str, Any]:
    query_language_hints = repo_map._query_language_hints(query)
    primary_target_language = repo_map._target_language_for_path(str(target.get("file") or ""))
    snippet_languages = {
        language
        for language in (
            repo_map._target_language_for_path(str(snippet.get("file") or ""))
            for snippet in snippets
        )
        if language is not None
    }

    confidence_cap = 1.0
    downgrade_reasons: list[str] = []
    ask_reasons: list[str] = []
    validation_filtered_count = int(validation_alignment.get("filtered_count", 0) or 0)
    validation_kept_count = int(validation_alignment.get("kept_count", 0) or 0)

    if (
        query_language_hints
        and primary_target_language is not None
        and primary_target_language not in query_language_hints
    ):
        confidence_cap = min(confidence_cap, 0.55)
        reason = (
            "query language intent conflicts with primary target language "
            f"({', '.join(query_language_hints)} vs {primary_target_language})"
        )
        downgrade_reasons.append(reason)
        ask_reasons.append(reason)

    if validation_filtered_count > 0 and validation_kept_count == 0:
        confidence_cap = min(confidence_cap, 0.65)
        reason = "validation commands did not align with primary target language"
        downgrade_reasons.append(reason)
        ask_reasons.append(reason)

    if (
        primary_target_language is not None
        and any(language != primary_target_language for language in snippet_languages)
        and not validation_commands
    ):
        confidence_cap = min(confidence_cap, 0.72)
        reason = "cross-language context lacks matching validation evidence"
        downgrade_reasons.append(reason)
        ask_reasons.append(reason)

    return {
        "query_language_hints": query_language_hints,
        "primary_target_language": primary_target_language,
        "validation_filtered_count": validation_filtered_count,
        "confidence_cap": confidence_cap,
        "downgrade_reasons": downgrade_reasons,
        "ask_reasons": ask_reasons,
    }


def _primary_target(payload: dict[str, Any]) -> dict[str, Any]:
    navigation_pack = _as_dict(payload.get("navigation_pack"))
    target = _as_dict(navigation_pack.get("primary_target"))
    edit_plan_seed = _as_dict(payload.get("edit_plan_seed"))
    primary_symbol = _as_dict(edit_plan_seed.get("primary_symbol"))
    primary_span = _as_dict(edit_plan_seed.get("primary_span"))
    if not target and edit_plan_seed.get("primary_file"):
        target = {
            "file": edit_plan_seed.get("primary_file"),
            "symbol": primary_symbol.get("name"),
            "kind": primary_symbol.get("kind"),
            "start_line": primary_span.get("start_line"),
            "end_line": primary_span.get("end_line"),
        }
    line = target.get("line") or target.get("start_line") or primary_span.get("start_line") or 1
    confidence = _as_dict(edit_plan_seed.get("confidence")).get("overall", 0.9)
    return {
        "file": str(target.get("file") or edit_plan_seed.get("primary_file") or ""),
        "symbol": target.get("symbol") or primary_symbol.get("name"),
        "kind": target.get("kind") or primary_symbol.get("kind") or "unknown",
        "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
        "confidence": confidence,
        "evidence": ["parser-backed", "heuristic"],
    }


def _alternative_targets(
    payload: dict[str, Any],
    target: dict[str, Any],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    primary_file = str(target.get("file") or "")
    candidate_targets = _as_dict(payload.get("candidate_edit_targets"))
    file_matches = {
        str(match.get("path") or ""): match
        for match in _as_list_of_dicts(payload.get("file_matches"))
        if match.get("path")
    }
    alternatives: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()

    for symbol in _as_list_of_dicts(candidate_targets.get("symbols")):
        file_path = str(symbol.get("file") or "")
        if not file_path or file_path == primary_file:
            continue
        symbol_name = str(symbol.get("name") or "")
        key = (file_path, symbol_name or None)
        if key in seen:
            continue
        seen.add(key)
        match = file_matches.get(file_path, {})
        score = max(int(symbol.get("score", 0) or 0), int(match.get("score", 0) or 0))
        line = symbol.get("line") or symbol.get("start_line") or 1
        alternatives.append({
            "file": file_path,
            "symbol": symbol_name or None,
            "kind": symbol.get("kind") or "unknown",
            "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
            "language": repo_map._target_language_for_path(file_path),
            "confidence": repo_map._confidence_from_score(score),
            "reasons": list(match.get("reasons") or []),
            "evidence": list(match.get("provenance") or ["heuristic"]),
        })

    for file_path in _as_list_of_strings(candidate_targets.get("files")):
        if file_path == primary_file:
            continue
        key = (file_path, None)
        if key in seen or any(item.get("file") == file_path for item in alternatives):
            continue
        seen.add(key)
        match = file_matches.get(file_path, {})
        score = int(match.get("score", 0) or 0)
        alternatives.append({
            "file": file_path,
            "symbol": None,
            "kind": "file",
            "line": 1,
            "language": repo_map._target_language_for_path(file_path),
            "confidence": repo_map._confidence_from_score(score),
            "reasons": list(match.get("reasons") or []),
            "evidence": list(match.get("provenance") or ["heuristic"]),
        })

    return alternatives[:limit]


def _line_map(source: str, start_line: object) -> list[dict[str, Any]]:
    try:
        current_line = int(str(start_line))
    except (TypeError, ValueError):
        current_line = 1
    return [
        {"line": current_line + index, "text": line}
        for index, line in enumerate(source.splitlines())
    ]


def _command_ref(argv: list[object]) -> dict[str, Any]:
    args = [str(arg) for arg in argv]
    return {
        "argv": args,
        "command": subprocess.list2cmdline(args),
    }


def _expanded_line_map(
    source: dict[str, Any],
    rendered_source: str,
) -> list[dict[str, Any]]:
    rendered_lines = rendered_source.splitlines()
    if not rendered_lines:
        return []

    raw_line_map = _as_list_of_dicts(source.get("line_map"))
    if not raw_line_map:
        return _line_map(rendered_source, source.get("start_line") or 1)

    rendered_to_original: dict[int, int] = {}
    for item in raw_line_map:
        if item.get("line") is not None:
            rendered_index = len(rendered_to_original) + 1
            try:
                rendered_to_original[rendered_index] = int(str(item["line"]))
            except (TypeError, ValueError):
                continue
            continue
        try:
            rendered_start = int(str(item["rendered_start_line"]))
            rendered_end = int(str(item["rendered_end_line"]))
            original_start = int(str(item["original_start_line"]))
        except (KeyError, TypeError, ValueError):
            continue
        for offset, rendered_line in enumerate(range(rendered_start, rendered_end + 1)):
            rendered_to_original[rendered_line] = original_start + offset

    if not rendered_to_original:
        return _line_map(rendered_source, source.get("start_line") or 1)

    return [
        {
            "line": rendered_to_original.get(index, index),
            "text": line,
        }
        for index, line in enumerate(rendered_lines, start=1)
    ]


def _source_refetch_ref(
    source: dict[str, Any],
    query: str,
    path: str,
    max_files: int,
) -> dict[str, Any]:
    symbol = source.get("symbol") or source.get("name")
    if symbol:
        return _command_ref(["tg", "source", "--symbol", symbol, "--json", path])
    return _command_ref([
        "tg",
        "context-render",
        "--query",
        query,
        "--json",
        path,
        "--max-files",
        max_files,
    ])


def _raw_context_ref(
    query: str,
    path: str,
    *,
    max_files: int,
    max_sources: int,
    max_tokens: int | None,
    max_repo_files: int | None,
    model: str | None,
) -> dict[str, Any]:
    argv: list[object] = [
        "tg",
        "context-render",
        "--query",
        query,
        "--json",
        path,
        "--max-files",
        max_files,
        "--max-sources",
        max_sources,
    ]
    if max_tokens is not None:
        argv.extend(["--max-tokens", max_tokens])
    if max_repo_files is not None:
        argv.extend(["--max-repo-files", max_repo_files])
    if model:
        argv.extend(["--model", model])
    return _command_ref(argv)


def _build_snippets(
    payload: dict[str, Any],
    *,
    query: str,
    path: str,
    max_files: int,
    max_tokens: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    snippets: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    used_tokens = 0
    for source in _as_list_of_dicts(payload.get("sources")):
        body = str(source.get("rendered_source") or source.get("source") or "")
        token_estimate = repo_map._estimate_tokens(body)
        if max_tokens is not None and used_tokens + token_estimate > max_tokens:
            ref = _source_refetch_ref(source, query, path, max_files)
            omitted.append({
                "kind": "source",
                "file": source.get("file"),
                "symbol": source.get("symbol") or source.get("name"),
                "reason": "token budget exhausted",
                "command": ref["command"],
                "argv": ref["argv"],
            })
            continue
        used_tokens += token_estimate
        snippets.append({
            "file": str(source.get("file") or ""),
            "symbol": source.get("symbol") or source.get("name"),
            "start_line": source.get("start_line") or 1,
            "end_line": source.get("end_line") or source.get("start_line") or 1,
            "source": body,
            "line_map": _expanded_line_map(source, body),
            "token_estimate": token_estimate,
            "evidence": ["parser-backed", "heuristic"],
        })
    return snippets, omitted, used_tokens


def _follow_up_reads(
    payload: dict[str, Any],
    omitted_sources: list[dict[str, Any]],
    *,
    query: str,
    path: str,
    max_files: int,
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    for item in _as_list_of_dicts(_as_dict(payload.get("navigation_pack")).get("follow_up_reads")):
        ref = _source_refetch_ref(item, query, path, max_files)
        reads.append({
            "file": item.get("file"),
            "symbol": item.get("symbol"),
            "role": item.get("role"),
            "command": ref["command"],
            "argv": ref["argv"],
        })
    for item in omitted_sources:
        command = str(item.get("command") or "")
        if command and not any(read.get("command") == command for read in reads):
            reads.append({
                "file": item.get("file"),
                "symbol": item.get("symbol"),
                "role": "omitted",
                "command": command,
                "argv": list(item.get("argv") or []),
            })
    if not reads and (payload.get("truncated") or payload.get("omitted_sections")):
        ref = _command_ref([
            "tg",
            "context-render",
            "--query",
            query,
            "--json",
            path,
            "--max-files",
            max_files,
        ])
        reads.append({
            "file": None,
            "symbol": None,
            "role": "context",
            "command": ref["command"],
            "argv": ref["argv"],
        })
    return reads


def _capsule_context_consistency(
    payload: dict[str, Any],
    target: dict[str, Any],
    snippets: list[dict[str, Any]],
    follow_up_reads: list[dict[str, Any]],
    omitted_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    consistency = _as_dict(payload.get("context_consistency"))
    primary_file = str(target.get("file") or "")
    if primary_file:
        consistency["primary_file"] = primary_file
    snippet_files = {str(item.get("file") or "") for item in snippets}
    follow_up_files = {str(item.get("file") or "") for item in follow_up_reads}
    omitted_by_file = {str(item.get("file") or ""): item for item in omitted_sources}
    primary_in_snippets = bool(primary_file and primary_file in snippet_files)
    primary_in_follow_up = bool(primary_file and primary_file in follow_up_files)
    primary_omitted = bool(primary_file and not primary_in_snippets)

    consistency["capsule_primary_file_in_snippets"] = primary_in_snippets
    consistency["capsule_primary_file_in_follow_up_reads"] = primary_in_follow_up
    consistency["capsule_primary_file_omitted"] = primary_omitted
    if primary_omitted:
        omitted = omitted_by_file.get(primary_file, {})
        consistency["capsule_primary_file_omission_reason"] = (
            omitted.get("reason") or "primary file not present in capsule snippets"
        )
        consistency["confidence_downgraded"] = True
        reasons = list(consistency.get("downgrade_reasons") or [])
        reason = "primary file omitted from capsule snippets by token budget"
        if reason not in reasons:
            reasons.append(reason)
        consistency["downgrade_reasons"] = reasons
    return consistency


def _confidence(
    payload: dict[str, Any],
    snippets: list[dict[str, Any]],
    downgrade_reasons: list[str],
    consistency: dict[str, Any],
) -> dict[str, Any]:
    edit_confidence = _as_dict(_as_dict(payload.get("edit_plan_seed")).get("confidence"))
    raw_overall = edit_confidence.get("overall")
    if isinstance(raw_overall, (int, float)):
        overall = float(raw_overall)
    else:
        if not consistency.get("primary_file_included", True) or not consistency.get(
            "rendered_context_includes_primary", True
        ):
            overall = 0.55
        elif payload.get("truncated"):
            overall = 0.72
        else:
            overall = 0.9
    if payload.get("truncated") or payload.get("omitted_sections"):
        downgrade_reasons.append("context omitted by token or render budget")
        overall = min(overall, 0.94)
    if not snippets:
        downgrade_reasons.append("no source snippets included")
        overall = min(overall, 0.55)
    if consistency.get("confidence_downgraded"):
        downgrade_reasons.append("context consistency downgraded confidence")
    if consistency.get("primary_file_included") is False:
        downgrade_reasons.append("primary file omitted from selected context")
    if consistency.get("rendered_context_includes_primary") is False:
        downgrade_reasons.append("primary file omitted from rendered context")
    if consistency.get("capsule_primary_file_omitted"):
        downgrade_reasons.append("primary file omitted from capsule snippets by token budget")
    if any("primary file" in reason for reason in downgrade_reasons):
        overall = min(overall, 0.55)
    deduped_reasons = list(dict.fromkeys(downgrade_reasons))
    return {"overall": round(overall, 3), "downgrade_reasons": deduped_reasons}


def build_agent_capsule(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int | None = None,
    model: str | None = None,
    include_blast_radius: bool = True,
) -> dict[str, Any]:
    resolved_path = str(Path(path).resolve())
    payload = repo_map.build_context_render(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        model=model,
        optimize_context=True,
        render_profile="full",
    )
    target = _primary_target(payload)
    alternatives = _alternative_targets(payload, target)
    snippets, omitted_sources, _used_tokens = _build_snippets(
        payload,
        query=query,
        path=resolved_path,
        max_files=max_files,
        max_tokens=max_tokens,
    )
    omitted_sections = [*(_as_list_of_dicts(payload.get("omitted_sections"))), *omitted_sources]
    follow_up_reads = _follow_up_reads(
        payload,
        omitted_sources,
        query=query,
        path=resolved_path,
        max_files=max_files,
    )
    edit_plan_seed = _as_dict(payload.get("edit_plan_seed"))
    validation_plan = _as_list_of_dicts(edit_plan_seed.get("validation_plan"))
    validation_commands = _as_list_of_strings(payload.get("validation_commands"))
    validation_plan, validation_commands, validation_alignment = _capsule_validation_alignment(
        target,
        validation_plan,
        validation_commands,
        payload,
    )
    edit_order = list(edit_plan_seed.get("edit_ordering") or [])
    if not edit_order and target["file"]:
        edit_order = [target["file"]]

    consistency = _capsule_context_consistency(
        payload,
        target,
        snippets,
        follow_up_reads,
        omitted_sources,
    )
    trust = _capsule_trust_checks(
        query,
        target,
        snippets,
        validation_commands,
        validation_alignment,
    )
    consistency["query_language_hints"] = trust["query_language_hints"]
    consistency["primary_target_language"] = trust["primary_target_language"]
    consistency["validation_alignment"] = validation_alignment
    consistency["validation_filtered_count"] = trust["validation_filtered_count"]
    if trust["downgrade_reasons"]:
        consistency["confidence_downgraded"] = True
        consistency["downgrade_reasons"] = _dedupe([
            *list(consistency.get("downgrade_reasons") or []),
            *trust["downgrade_reasons"],
        ])

    downgrade_reasons: list[str] = list(trust["downgrade_reasons"])
    confidence = _confidence(payload, snippets, downgrade_reasons, consistency)
    confidence_cap = float(trust["confidence_cap"])
    if confidence_cap < 1.0:
        confidence["overall"] = round(min(float(confidence["overall"]), confidence_cap), 3)
        _cap_primary_target_confidence(target, confidence_cap)
    _cap_alternative_target_confidences(alternatives, target)
    ask_reasons: list[str] = []
    ask_reasons.extend(trust["ask_reasons"])
    if not validation_commands:
        ask_reasons.append("no validation command evidence")
    if not snippets:
        ask_reasons.append("no snippets included")
    if confidence["overall"] < 0.75:
        ask_reasons.append("confidence below 0.75")
    if consistency.get("capsule_primary_file_omitted"):
        ask_reasons.append("primary file omitted from capsule snippets")
    if (
        consistency.get("confidence_downgraded")
        or consistency.get("primary_file_included") is False
        or consistency.get("rendered_context_includes_primary") is False
    ):
        ask_reasons.append("context consistency requires confirmation")

    raw_context_ref = _raw_context_ref(
        query,
        resolved_path,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        max_repo_files=max_repo_files,
        model=model,
    )
    call_site_evidence = {
        "status": "not_collected" if include_blast_radius else "disabled",
        "reason": "capsule v1 does not run blast-radius automatically",
    }
    rollback_ref = _command_ref(["tg", "checkpoint", "create", resolved_path])

    return {
        "version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "agent-context-capsule",
        "capsule_version": 1,
        "capsule_kind": "actionable_context",
        "query": query,
        "path": resolved_path,
        "primary_target": target,
        "alternative_targets": alternatives,
        "route_rationale": [
            {
                "strategy": "context-render",
                "evidence": "heuristic",
                "reason": "highest ranked edit target from context-render",
            }
        ],
        "snippets": snippets,
        "related_call_sites": [],
        "call_site_evidence": call_site_evidence,
        "validation_plan": validation_plan,
        "validation_commands": validation_commands,
        "edit_order": edit_order,
        "rollback": {
            "checkpoint_recommended": bool(target["file"]),
            "reason": "source edit target selected"
            if target["file"]
            else "no source target selected",
            "command": rollback_ref["command"],
            "argv": rollback_ref["argv"],
        },
        "omissions": {
            "token_budget": max_tokens,
            "omitted_section_count": len(omitted_sections),
            "omitted_sections": omitted_sections,
            "follow_up_reads": follow_up_reads,
        },
        "confidence": confidence,
        "ask_user_before_editing": {
            "required": bool(ask_reasons),
            "reasons": _dedupe(ask_reasons),
        },
        "context_consistency": consistency,
        "raw_context_ref": raw_context_ref,
    }


def build_agent_capsule_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int | None = None,
    model: str | None = None,
    include_blast_radius: bool = True,
) -> str:
    return json.dumps(
        build_agent_capsule(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
            model=model,
            include_blast_radius=include_blast_radius,
        ),
        ensure_ascii=False,
        indent=2,
    )
