from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary
from tensor_grep.core.retrieval_lexical import split_terms

_CAPSULE_LSP_CONFIDENCE_BOOST_ENV = "TG_CAPSULE_LSP_CONFIDENCE_BOOST"
_CAPSULE_LSP_CONFIDENCE_CAP = 0.85
_CAPSULE_LSP_CONFIDENCE_LANGUAGES = {"javascript", "php", "python", "rust", "typescript"}

# F4: the exact `_build_snippets` omission reason for a source cut by the capsule's OWN token
# budget (agent_capsule.py `_build_snippets`) -- distinct from the generic "not present in
# capsule snippets" fallback `_capsule_context_consistency` uses when the primary file never
# appeared among the rendered sources at all (a genuine ranking miss, not a budget cut).
_CAPSULE_TOKEN_BUDGET_OMISSION_REASON = "token budget exhausted"
# Uplift ceiling for a *corroborated* token-budget-only primary omission. Deliberately below the
# uncapped 0.9 default and matched to the >=0.75 "no ask-user" threshold (agent_capsule.py
# `ask_user_before_editing` construction) -- this is a bounded relief from the 0.55 safety floor,
# not a return to full confidence.
_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP = 0.75


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


def _capsule_lsp_confidence_boost_enabled() -> bool:
    raw = os.environ.get(_CAPSULE_LSP_CONFIDENCE_BOOST_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _target_has_lsp_confidence_proof(target: dict[str, Any]) -> bool:
    return target.get("lsp_proof") is True and target.get("lsp_provider_response") is True


def _target_lsp_boost_language(target: dict[str, Any]) -> str | None:
    file_path = str(target.get("file") or "")
    return repo_map._target_language_for_path(file_path) or repo_map._provider_language_for_path(
        file_path,
    )


def _lsp_tie_resolution_evidence(
    target: dict[str, Any],
    tied_alternatives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not _target_has_lsp_confidence_proof(target):
        return []
    evidence: dict[str, Any] = {
        "kind": "lsp-primary-target-proof",
        "file": str(target.get("file") or ""),
        "symbol": target.get("symbol"),
        "language": _target_lsp_boost_language(target),
        "lsp_proof": True,
        "lsp_provider_response": True,
        "tied_alternative_count": len(tied_alternatives),
        "tied_alternative_files": [
            str(alternative.get("file") or "")
            for alternative in tied_alternatives
            if alternative.get("file")
        ],
        "reason": "primary target has provider-backed LSP proof and tied alternatives do not",
    }
    for key in (
        "semantic_provider",
        "provenance",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in target:
            evidence[key] = target[key]
    return [evidence]


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


def _tied_alternative_targets(
    query: str,
    alternatives: list[dict[str, Any]],
    primary_target: dict[str, Any],
) -> list[dict[str, Any]]:
    query_language_hints = repo_map._query_language_hints(query)
    primary_file = str(primary_target.get("file") or "")
    primary_language = repo_map._target_language_for_path(primary_file)
    primary_name = Path(primary_file).name.lower()
    query_lower = query.lower()
    primary_confidence = _numeric_confidence(primary_target.get("confidence"))
    tied: list[dict[str, Any]] = []
    for alternative in alternatives:
        alternative_confidence = _numeric_confidence(alternative.get("confidence"), 0.0)
        if alternative_confidence < primary_confidence:
            continue
        alternative_file = str(alternative.get("file") or "")
        alternative_language = repo_map._target_language_for_path(alternative_file)
        if (
            query_language_hints
            and primary_language in query_language_hints
            and alternative_language not in query_language_hints
        ):
            continue
        alternative_name = Path(alternative_file).name.lower()
        if primary_name and primary_name in query_lower and alternative_name not in query_lower:
            continue
        tied_target: dict[str, Any] = {
            "file": alternative_file,
            "symbol": alternative.get("symbol"),
            "language": alternative.get("language") or alternative_language,
            "confidence": round(alternative_confidence, 3),
        }
        for proof_field in (
            "semantic_provider",
            "provenance",
            "lsp_provider_response",
            "lsp_proof",
            "lsp_operation",
            "lsp_resolution_basis",
        ):
            if proof_field in alternative:
                tied_target[proof_field] = alternative[proof_field]
        tied.append(tied_target)
    return tied


def _primary_target_is_unrequested_marker_helper(
    query: str,
    primary_target: dict[str, Any],
) -> bool:
    symbol = str(primary_target.get("symbol") or "")
    if not symbol:
        return False
    query_terms = set(repo_map._query_terms(query))
    symbol_terms = set(split_terms(symbol))
    return "marker" in symbol_terms and "marker" not in query_terms


def _prefer_implementation_over_marker_helper(
    query: str,
    primary_target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Promote a genuine implementation over an unrequested marker-helper primary.

    Corpus-IDF shifts can transiently rank a ``*_marker`` helper above the implementation it
    marks (the BM25 score gap is sensitive to the whole corpus, not just the two symbols). When
    the primary target is an unrequested marker-helper AND a non-marker implementation candidate
    exists among the alternatives, swap them: the implementation becomes primary and the marker
    becomes an alternative — being higher-confidence it then surfaces as a *tied* alternative, so
    the ambiguity is still flagged for confirmation instead of the marker being confidently picked.
    This keeps the "prefer implementation over marker" contract robust to corpus growth.
    """
    if not _primary_target_is_unrequested_marker_helper(query, primary_target):
        return primary_target, alternatives
    best_index = -1
    best_confidence = -1.0
    for index, alternative in enumerate(alternatives):
        alt_symbol = str(alternative.get("symbol") or "")
        if not alt_symbol or "marker" in set(split_terms(alt_symbol)):
            continue
        alt_confidence = _numeric_confidence(alternative.get("confidence"), 0.0)
        if alt_confidence > best_confidence:
            best_confidence = alt_confidence
            best_index = index
    if best_index < 0:
        return primary_target, alternatives
    implementation = alternatives[best_index]
    demoted = [*alternatives[:best_index], *alternatives[best_index + 1 :]]
    demoted.insert(0, primary_target)
    return implementation, demoted


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


def _validation_plan_has_targeted_primary_evidence(
    validation_plan: list[dict[str, Any]],
) -> bool:
    return bool(_targeted_validation_evidence(validation_plan))


def _targeted_validation_evidence(validation_plan: list[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    for step in validation_plan:
        scope = str(step.get("scope") or "").strip().lower()
        target = str(step.get("target") or "").strip()
        confidence = _numeric_confidence(step.get("confidence"))
        if scope in {"symbol", "file"} and target and confidence >= 0.7:
            command = str(step.get("command") or "").strip()
            if command:
                evidence.append(command)
            else:
                runner = str(step.get("runner") or "").strip()
                evidence.append(f"{runner}:{scope}:{target}" if runner else f"{scope}:{target}")
    return _dedupe(evidence)


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
    target_payload = {
        "file": str(target.get("file") or edit_plan_seed.get("primary_file") or ""),
        "symbol": target.get("symbol") or primary_symbol.get("name"),
        "kind": target.get("kind") or primary_symbol.get("kind") or "unknown",
        "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
        "confidence": confidence,
        "evidence": ["parser-backed", "heuristic"],
    }
    for key in (
        "semantic_provider",
        "provenance",
        "lsp_provider_response",
        "lsp_proof",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in target:
            target_payload[key] = target[key]
        elif key in primary_symbol:
            target_payload[key] = primary_symbol[key]
    if target_payload.get("lsp_proof") is True:
        target_payload["evidence"] = _dedupe([
            "lsp-confirmed",
            *[
                str(item)
                for item in target_payload.get("evidence", [])
                if item is not None and str(item)
            ],
        ])
    return target_payload


def _target_symbol_was_explicitly_requested(query: str, target: dict[str, Any]) -> bool:
    symbol = str(target.get("symbol") or "")
    return bool(symbol and repo_map._symbol_name_matches_query_exactly(symbol, query))


def _related_call_site_record(
    caller: dict[str, Any],
    *,
    target_symbol: str,
) -> dict[str, Any] | None:
    file_path = str(caller.get("file") or "")
    if not file_path:
        return None
    raw_line = caller.get("line") or caller.get("start_line") or 1
    try:
        line = int(str(raw_line))
    except (TypeError, ValueError):
        line = 1
    record: dict[str, Any] = {
        "file": file_path,
        "line": max(1, line),
        "symbol": target_symbol,
        "kind": str(caller.get("kind") or "call"),
        "ref_kind": str(caller.get("ref_kind") or "call"),
        "provenance": str(caller.get("provenance") or "heuristic"),
        "reason": "direct caller of primary target",
    }
    raw_end_line = caller.get("end_line")
    if raw_end_line is not None:
        try:
            record["end_line"] = max(line, int(str(raw_end_line)))
        except (TypeError, ValueError):
            pass
    text = str(caller.get("text") or "").strip()
    if text:
        record["text"] = text[:240]
    return record


def _collect_capsule_call_site_evidence(
    query: str,
    path: str,
    target: dict[str, Any],
    *,
    include_blast_radius: bool,
    max_files: int,
    max_repo_files: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not include_blast_radius:
        return [], {
            "status": "disabled",
            "reason": "call-site evidence disabled by caller",
        }
    target_symbol = str(target.get("symbol") or "")
    if not target_symbol:
        return [], {
            "status": "skipped",
            "reason": "primary target has no symbol",
        }
    if not _target_symbol_was_explicitly_requested(query, target):
        return [], {
            "status": "skipped",
            "reason": "primary symbol was not explicitly requested by query",
        }
    if _numeric_confidence(target.get("confidence"), 0.0) < 0.75:
        return [], {
            "status": "skipped",
            "reason": "primary target confidence below call-site collection threshold",
        }

    max_callers = max(1, min(int(max_files) * 2, 8))
    try:
        radius_payload = repo_map.build_symbol_blast_radius(
            target_symbol,
            path,
            max_depth=1,
            max_repo_files=max_repo_files,
            max_callers=max_callers,
            max_files=max_callers,
        )
    except Exception as exc:  # pragma: no cover - defensive evidence side path
        return [], {
            "status": "error",
            "reason": "call-site evidence collection failed",
            "error": str(exc),
        }

    if radius_payload.get("no_match"):
        return [], {
            "status": "skipped",
            "reason": "primary symbol definition was not found by blast-radius",
            "symbol": target_symbol,
        }

    related_call_sites = [
        record
        for record in (
            _related_call_site_record(caller, target_symbol=target_symbol)
            for caller in _as_list_of_dicts(radius_payload.get("callers"))
        )
        if record is not None
    ]
    output_limit = _as_dict(radius_payload.get("output_limit"))
    provenance = _dedupe([
        str(record.get("provenance") or "heuristic") for record in related_call_sites
    ])
    evidence = {
        "status": "collected" if related_call_sites else "collected_no_call_sites",
        "symbol": target_symbol,
        "routing_reason": str(radius_payload.get("routing_reason") or "symbol-blast-radius"),
        "max_callers": max_callers,
        "returned_call_sites": len(related_call_sites),
        "omitted_call_sites": int(output_limit.get("omitted_callers", 0) or 0),
        "provenance": provenance,
        "graph_trust_summary": _as_dict(radius_payload.get("graph_trust_summary")),
        # PATH A Stage 0 (additive): surface the same resolution_gaps floor blast-radius now
        # carries so an agent sees WHY graph_trust_summary was downgraded, not just that it was.
        "resolution_gaps": _as_list_of_dicts(radius_payload.get("resolution_gaps")),
    }
    return related_call_sites, evidence


def _alternative_targets(
    payload: dict[str, Any],
    target: dict[str, Any],
    *,
    limit: int | None = 4,
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
        alternative: dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name or None,
            "kind": symbol.get("kind") or "unknown",
            "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
            "language": repo_map._target_language_for_path(file_path),
            "confidence": repo_map._confidence_from_score(score),
            "reasons": list(match.get("reasons") or []),
            "evidence": list(match.get("provenance") or ["heuristic"]),
        }
        for proof_field in (
            "semantic_provider",
            "provenance",
            "lsp_provider_response",
            "lsp_proof",
            "lsp_operation",
            "lsp_resolution_basis",
        ):
            if proof_field in symbol:
                alternative[proof_field] = symbol[proof_field]
        if alternative.get("lsp_proof") is True:
            evidence_value = alternative.get("evidence")
            evidence_items = evidence_value if isinstance(evidence_value, list) else []
            alternative["evidence"] = _dedupe([
                "lsp-confirmed",
                *[str(item) for item in evidence_items if item is not None and str(item)],
            ])
        alternatives.append(alternative)

    return alternatives if limit is None else alternatives[:limit]


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


def _normalize_gpu_device_ids(device_ids: list[int] | None) -> list[int]:
    if not device_ids:
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_device_id in device_ids:
        try:
            device_id = int(raw_device_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid GPU device id: {raw_device_id!r}") from exc
        if device_id < 0:
            raise ValueError(
                f"Invalid GPU device id: {device_id}. Device IDs must be non-negative."
            )
        if device_id in seen:
            continue
        seen.add(device_id)
        normalized.append(device_id)
    return normalized


def _agent_gpu_query_terms(query: str, *, limit: int = 8) -> list[str]:
    terms: list[str] = []
    for term in repo_map._symbol_query_terms(query):
        cleaned = str(term).strip()
        if len(cleaned) < 3:
            continue
        terms.append(cleaned)
    return _dedupe(terms)[:limit]


def _run_agent_gpu_json_command(
    argv: list[object],
    *,
    timeout_s: float,
    valid_return_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    ref = _command_ref(argv)
    args = [str(arg) for arg in argv]
    try:
        completed = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(float(timeout_s), 0.1),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "reason": f"GPU evidence command timed out after {timeout_s:g}s.",
            "command": ref["command"],
            "argv": ref["argv"],
            "exit_code": None,
            "stderr": str(exc),
        }
    except OSError as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "command": ref["command"],
            "argv": ref["argv"],
            "exit_code": None,
            "stderr": str(exc),
        }

    stdout = completed.stdout or ""
    stderr = (completed.stderr or "").strip()
    result: dict[str, Any] = {
        "status": "ok",
        "command": ref["command"],
        "argv": ref["argv"],
        "exit_code": completed.returncode,
    }
    if stderr:
        result["stderr"] = stderr
    if completed.returncode not in valid_return_codes:
        result["status"] = "failed"
        result["reason"] = f"GPU evidence command exited with code {completed.returncode}."
        return result

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result["status"] = "malformed"
        result["reason"] = f"GPU evidence command did not return JSON: {exc}"
        if stdout.strip():
            result["stdout_preview"] = stdout.strip()[:400]
        return result
    if not isinstance(payload, dict):
        result["status"] = "malformed"
        result["reason"] = "GPU evidence command returned a non-object JSON payload."
        return result
    result["payload"] = payload
    return result


def _summarize_agent_gpu_json_result(
    result: dict[str, Any],
    *,
    match_preview_limit: int = 3,
    redact_probe_paths: bool = False,
) -> dict[str, Any]:
    summary = {key: value for key, value in result.items() if key != "payload"}
    if redact_probe_paths:
        if "argv" in summary:
            summary["argv"] = [
                "<agent-gpu-probe-root>" if "tg-agent-gpu-probe-" in str(arg) else str(arg)
                for arg in _as_list_of_strings(summary.get("argv"))
            ]
        if "command" in summary:
            summary["command"] = subprocess.list2cmdline([
                str(arg) for arg in summary.get("argv", [])
            ])

    payload = _as_dict(result.get("payload"))
    if not payload:
        return summary

    payload_summary: dict[str, Any] = {}
    for key in (
        "version",
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "query",
        "path",
        "total_matches",
        "total_files",
        "requested_gpu_device_ids",
        "routing_gpu_device_ids",
    ):
        if key in payload:
            if redact_probe_paths and key == "path":
                payload_summary[key] = "<agent-gpu-probe-root>"
            else:
                payload_summary[key] = payload[key]

    pipeline = _as_dict(payload.get("pipeline"))
    if pipeline:
        payload_summary["pipeline"] = {
            key: pipeline[key]
            for key in (
                "pattern_count",
                "pattern_batch_count",
                "single_dispatch",
                "cpu_staging_bytes",
                "transfer_time_ms",
                "kernel_time_ms",
                "wall_time_ms",
                "transfer_throughput_bytes_s",
            )
            if key in pipeline
        }

    matches = _as_list_of_dicts(payload.get("matches"))
    preview: list[dict[str, Any]] = []
    for match in matches[:match_preview_limit]:
        text = str(match.get("text") or "")
        preview.append({
            "file": (
                "<agent-gpu-probe-file>"
                if redact_probe_paths
                else match.get("file") or match.get("path")
            ),
            "line": match.get("line") or match.get("line_number"),
            "pattern_id": match.get("pattern_id"),
            "pattern_text": match.get("pattern_text") or match.get("pattern"),
            "text_preview": text[:160] if text else None,
        })
    payload_summary["matches_preview"] = preview
    payload_summary["matches_omitted"] = max(0, len(matches) - len(preview))
    summary["payload"] = payload_summary
    return summary


def _agent_gpu_tg_command() -> str:
    native_tg = resolve_native_tg_binary()
    return str(native_tg) if native_tg is not None else "tg"


def _native_gpu_route_rejection(payload: dict[str, Any]) -> str | None:
    backend = str(payload.get("routing_backend") or "")
    sidecar_used = bool(payload.get("sidecar_used"))
    if backend == "NativeGpuBackend" and not sidecar_used:
        return None
    if sidecar_used or "Sidecar" in backend:
        return (
            "sidecar-routed GPU result is unsupported for agent evidence; "
            "use a CUDA-enabled native tg route."
        )
    return (
        "GPU evidence command did not use NativeGpuBackend "
        f"(routing_backend={backend or 'unknown'})."
    )


def _gpu_route_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "routing_backend": str(payload.get("routing_backend") or "unknown"),
        "routing_reason": str(payload.get("routing_reason") or "unknown"),
        "sidecar_used": bool(payload.get("sidecar_used")),
    }


def _resolve_match_file(file_value: object, search_root: Path) -> str | None:
    raw_file = str(file_value or "").strip()
    if not raw_file:
        return None
    candidate = Path(raw_file)
    if not candidate.is_absolute():
        candidate = search_root / candidate
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate)


def _agent_gpu_evidence(
    query: str,
    path: str,
    *,
    gpu_device_ids: list[int] | None,
    max_files: int,
    timeout_s: float,
) -> dict[str, Any]:
    requested_device_ids = _normalize_gpu_device_ids(gpu_device_ids)
    if not requested_device_ids:
        return {
            "status": "not_requested",
            "requested_device_ids": [],
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": "No GPU evidence scan requested.",
        }

    try:
        tg_command = _agent_gpu_tg_command()
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(exc),
        }

    device_arg = ",".join(str(device_id) for device_id in requested_device_ids)
    with tempfile.TemporaryDirectory(prefix="tg-agent-gpu-probe-") as probe_tmp:
        probe_dir = Path(probe_tmp)
        (probe_dir / "probe.log").write_text(
            "tg agent gpu probe sentinel\n",
            encoding="utf-8",
        )
        probe_command: list[object] = [
            tg_command,
            "search",
            "--gpu-device-ids",
            device_arg,
            "--json",
            "-F",
            "tg agent gpu probe sentinel",
            str(probe_dir),
        ]
        probe = _run_agent_gpu_json_command(probe_command, timeout_s=timeout_s)

    if probe["status"] != "ok":
        return {
            "status": str(probe["status"]),
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(probe.get("reason") or "GPU route probe failed."),
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
        }

    probe_payload = _as_dict(probe.get("payload"))
    route_rejection = _native_gpu_route_rejection(probe_payload)
    route_fields = _gpu_route_fields(probe_payload)
    if route_rejection is not None:
        return {
            "status": "unsupported",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": route_rejection,
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            **route_fields,
        }

    query_terms = _agent_gpu_query_terms(query)
    if not query_terms:
        return {
            "status": "ready",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": "Native GPU route passed, but the query produced no evidence terms.",
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            **route_fields,
        }

    evidence_command: list[object] = [
        tg_command,
        "search",
        "--gpu-device-ids",
        device_arg,
        "--json",
        "-F",
    ]
    for term in query_terms:
        evidence_command.extend(["-e", term])
    evidence_command.append(path)
    evidence = _run_agent_gpu_json_command(
        evidence_command,
        timeout_s=timeout_s,
        valid_return_codes=(0, 1),
    )
    if evidence["status"] != "ok":
        return {
            "status": str(evidence["status"]),
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(evidence.get("reason") or "GPU evidence scan failed."),
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            "evidence": _summarize_agent_gpu_json_result(evidence),
            **route_fields,
        }

    evidence_payload = _as_dict(evidence.get("payload"))
    evidence_route_rejection = _native_gpu_route_rejection(evidence_payload)
    evidence_route_fields = _gpu_route_fields(evidence_payload)
    if evidence_route_rejection is not None:
        return {
            "status": "unsupported",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": evidence_route_rejection,
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            "evidence": _summarize_agent_gpu_json_result(evidence),
            **evidence_route_fields,
        }

    search_root = Path(path)
    matched_files: list[str] = []
    evidence_matches: list[dict[str, Any]] = []
    for match in _as_list_of_dicts(evidence_payload.get("matches")):
        matched_file = _resolve_match_file(match.get("file") or match.get("path"), search_root)
        if matched_file is None:
            continue
        if matched_file not in matched_files:
            matched_files.append(matched_file)
        if len(evidence_matches) < max_files:
            evidence_matches.append({
                "file": matched_file,
                "line": match.get("line") or match.get("line_number"),
                "pattern_text": match.get("pattern_text") or match.get("pattern"),
            })

    total_matches = int(evidence_payload.get("total_matches", len(evidence_matches)) or 0)
    status = "used" if matched_files else "ready_no_matches"
    reason = (
        "Native GPU route produced batched query-term evidence."
        if matched_files
        else "Native GPU route ran, but no query-term evidence matched."
    )
    return {
        "status": status,
        "requested_device_ids": requested_device_ids,
        "used_for_evidence": bool(matched_files),
        "promotion_claim": False,
        "reason": reason,
        "query_terms": query_terms,
        "matched_files": matched_files[:max_files],
        "total_matches": total_matches,
        "matches": evidence_matches,
        "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
        "evidence": _summarize_agent_gpu_json_result(evidence),
        **evidence_route_fields,
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

    fallback_line: int | None = (
        None if _as_dict(source.get("source_budget")).get("truncated") else 0
    )
    return [
        {
            "line": rendered_to_original.get(index)
            if index in rendered_to_original
            else (index if fallback_line == 0 else fallback_line),
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
    source_path = str(source.get("file") or "").strip()
    refetch_path = source_path or path
    if symbol:
        return _command_ref(["tg", "source", refetch_path, symbol, "--json"])
    return _command_ref([
        "tg",
        "context-render",
        refetch_path,
        query,
        "--json",
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
    semantic_provider: str,
) -> dict[str, Any]:
    argv: list[object] = [
        "tg",
        "context-render",
        path,
        query,
        "--json",
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
    if semantic_provider != "native":
        argv.extend(["--provider", semantic_provider])
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
        source_budget = source.get("source_budget")
        budget_token_estimate = token_estimate
        if isinstance(source_budget, dict) and source_budget.get("truncated"):
            budget_token_estimate = int(
                source_budget.get("original_token_estimate") or token_estimate
            )
        if max_tokens is not None and used_tokens + budget_token_estimate > max_tokens:
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
            path,
            query,
            "--json",
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


def _primary_target_matches_query(query: str, target: dict[str, Any]) -> bool:
    """True when the primary target's symbol or file stem is actually named by the query.

    Corroboration signal for `_apply_capsule_token_budget_confidence_uplift`: a token-budget
    omission is only safe to uplift when the primary target itself is independently confirmed
    by the query, not merely by ranking.
    """
    query_terms = set(repo_map._query_terms(query))
    if not query_terms:
        return False
    symbol = str(target.get("symbol") or "")
    if symbol and set(split_terms(symbol)) & query_terms:
        return True
    file_path = str(target.get("file") or "")
    if file_path and set(split_terms(Path(file_path).stem)) & query_terms:
        return True
    return False


def _capsule_primary_omission_is_token_budget_only(consistency: dict[str, Any]) -> bool:
    """True when the ONLY primary-file downgrade signal is a corroborable token-budget cut.

    This is the split at the heart of F4: `primary_file_included is False` /
    `rendered_context_includes_primary is False` mean ranking never selected or rendered the
    primary at all -- a genuine misroute, and this function must return False so the 0.55
    degrade-to-ask safety floor (v1.17.13) keeps holding. `capsule_primary_file_omitted` with
    the SPECIFIC `_CAPSULE_TOKEN_BUDGET_OMISSION_REASON` means the primary WAS selected/rendered
    upstream and only the capsule's own snippet token budget cut it -- a much weaker signal.
    The generic "primary file not present in capsule snippets" fallback text (used when the
    primary never appeared among `_build_snippets`' omitted sources either) intentionally does
    NOT match here, so it still falls through to the safety floor.
    """
    if consistency.get("primary_file_included") is False:
        return False
    if consistency.get("rendered_context_includes_primary") is False:
        return False
    if not consistency.get("capsule_primary_file_omitted"):
        return False
    return (
        consistency.get("capsule_primary_file_omission_reason")
        == _CAPSULE_TOKEN_BUDGET_OMISSION_REASON
    )


def _capsule_token_budget_uplift_eligible(
    *,
    query: str,
    target: dict[str, Any],
    snippets: list[dict[str, Any]],
    consistency: dict[str, Any],
    call_site_evidence: dict[str, Any],
) -> bool:
    if not snippets:
        return False
    if not _capsule_primary_omission_is_token_budget_only(consistency):
        return False
    # Require the token-budget cut to be the ONLY confidence-downgrading signal in play -- if a
    # trust-level conflict (language mismatch, validation misalignment), an alternative-target
    # tie, or a marker-helper demotion is ALSO present, leave the existing (conservative)
    # behavior untouched rather than trying to partially unwind a multi-cause downgrade.
    other_reasons = {
        str(reason) for reason in (consistency.get("downgrade_reasons") or []) if reason
    } - {"primary file omitted from capsule snippets by token budget"}
    if other_reasons:
        return False
    if not _primary_target_matches_query(query, target):
        return False
    return call_site_evidence.get("status") == "collected"


def _apply_capsule_token_budget_confidence_uplift(
    *,
    query: str,
    target: dict[str, Any],
    alternatives: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
    consistency: dict[str, Any],
    confidence: dict[str, Any],
    confidence_cap: float,
    call_site_evidence: dict[str, Any],
    ask_reasons: list[str],
) -> None:
    """Uplift the 0.55 primary-omission clamp to <=0.75 for a CORROBORATED token-budget-only
    omission -- never for a genuine misroute (see `_capsule_primary_omission_is_token_budget_only`).

    STRUCTURAL note: this must run AFTER `_collect_capsule_call_site_evidence` (agent_capsule.py
    call order), since verified caller evidence -- the corroboration this uplift depends on --
    isn't available until that call returns. It mutates `confidence`, `target`, `alternatives`,
    and `ask_reasons` in place so `build_agent_capsule`'s already-assembled payload reflects the
    uplift without re-deriving `ask_user_before_editing` from scratch.
    """
    current_overall = float(confidence.get("overall", 0.0))
    if current_overall > 0.55:
        return
    if not _capsule_token_budget_uplift_eligible(
        query=query,
        target=target,
        snippets=snippets,
        consistency=consistency,
        call_site_evidence=call_site_evidence,
    ):
        return
    uplifted = round(min(_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP, confidence_cap), 3)
    if uplifted <= current_overall:
        return
    confidence["overall"] = uplifted
    remaining_reasons = [
        reason
        for reason in confidence.get("downgrade_reasons", [])
        if reason != "primary file omitted from capsule snippets by token budget"
    ]
    remaining_reasons.append(
        "primary file omitted by token budget only; uplifted to "
        f"{uplifted} on corroborated call-site evidence"
    )
    confidence["downgrade_reasons"] = remaining_reasons
    consistency["capsule_token_budget_confidence_uplifted"] = True
    _cap_primary_target_confidence(target, uplifted)
    _cap_alternative_target_confidences(alternatives, target)
    # These three ask-reasons were added solely because of the primary-file-omission clamp we
    # just uplifted (asserted by the "no other downgrade reason" eligibility check above) -- clear
    # them so `ask_user_before_editing.required` deliberately flips to False for this case.
    ask_reasons[:] = [
        reason
        for reason in ask_reasons
        if reason
        not in {
            "confidence below 0.75",
            "primary file omitted from capsule snippets",
            "context consistency requires confirmation",
        }
    ]


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
    semantic_provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    ignore: tuple[str, ...] = (),
) -> dict[str, Any]:
    resolved_path = str(Path(path).resolve())
    requested_semantic_provider = semantic_provider
    effective_semantic_provider = (
        "hybrid"
        if semantic_provider == "native" and _capsule_lsp_confidence_boost_enabled()
        else semantic_provider
    )
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
        semantic_provider=effective_semantic_provider,
        ignore=ignore,
    )
    target = _primary_target(payload)
    all_alternatives = _alternative_targets(payload, target, limit=None)
    alternatives = all_alternatives[:4]
    target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
    omitted_alternative_targets = max(0, len(all_alternatives) - len(alternatives))
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
    # Additive, unverified suggestion (test-neighbor filename probe) — read straight from the
    # payload/edit-plan seed with NO language-alignment filtering and NO influence on trust
    # checks, confidence caps, or tie resolution. Never merged into `validation_commands` above.
    suggested_validation_commands = _as_list_of_dicts(
        payload.get("suggested_validation_commands")
        or edit_plan_seed.get("suggested_validation_commands"),
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
    consistency["alternative_targets_total"] = len(all_alternatives)
    consistency["alternative_targets_returned"] = len(alternatives)
    consistency["alternative_targets_omitted_count"] = omitted_alternative_targets
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
    consistency["confidence_cap"] = trust["confidence_cap"]
    if trust["downgrade_reasons"]:
        consistency["confidence_downgraded"] = True
        consistency["downgrade_reasons"] = _dedupe([
            *list(consistency.get("downgrade_reasons") or []),
            *trust["downgrade_reasons"],
        ])

    downgrade_reasons: list[str] = list(trust["downgrade_reasons"])
    confidence = _confidence(payload, snippets, downgrade_reasons, consistency)
    confidence_cap = float(trust["confidence_cap"])
    lsp_confidence_boost_enabled = _capsule_lsp_confidence_boost_enabled()
    primary_target_lsp_proof = _target_has_lsp_confidence_proof(target)
    lsp_boost_language = _target_lsp_boost_language(target)
    lsp_confidence_boost_eligible = (
        lsp_confidence_boost_enabled
        and primary_target_lsp_proof
        and lsp_boost_language in _CAPSULE_LSP_CONFIDENCE_LANGUAGES
    )
    consistency["lsp_confidence_boost_enabled"] = lsp_confidence_boost_enabled
    consistency["lsp_confidence_boost_eligible"] = lsp_confidence_boost_eligible
    consistency["lsp_confidence_boost_language"] = lsp_boost_language
    if primary_target_lsp_proof:
        consistency["primary_target_lsp_proof"] = True
    if lsp_confidence_boost_eligible:
        consistency["lsp_confidence_cap"] = _CAPSULE_LSP_CONFIDENCE_CAP
        confidence["overall"] = round(
            min(float(confidence["overall"]), _CAPSULE_LSP_CONFIDENCE_CAP),
            3,
        )
        _cap_primary_target_confidence(target, _CAPSULE_LSP_CONFIDENCE_CAP)
    if confidence_cap < 1.0:
        confidence["overall"] = round(min(float(confidence["overall"]), confidence_cap), 3)
        _cap_primary_target_confidence(target, confidence_cap)
    _cap_alternative_target_confidences(alternatives, target)
    tied_alternatives = _tied_alternative_targets(query, alternatives, target)
    tie_candidates = list(tied_alternatives)
    marker_helper_tie = bool(tied_alternatives) and _primary_target_is_unrequested_marker_helper(
        query,
        target,
    )
    validation_alignment_status = str(validation_alignment.get("status") or "")
    validation_kept_count = int(validation_alignment.get("kept_count", 0) or 0)
    targeted_validation_evidence = _targeted_validation_evidence(validation_plan)
    tie_resolved_by_validation = (
        bool(tied_alternatives)
        and not marker_helper_tie
        and bool(validation_commands)
        and bool(targeted_validation_evidence)
        and (
            validation_alignment_status == "aligned"
            or (validation_alignment_status == "mismatch-filtered" and validation_kept_count > 0)
        )
    )
    tie_resolved_by_lsp = (
        bool(tied_alternatives)
        and not marker_helper_tie
        and lsp_confidence_boost_eligible
        and not any(
            _target_has_lsp_confidence_proof(alternative) for alternative in tied_alternatives
        )
    )
    if marker_helper_tie:
        consistency["confidence_downgraded"] = True
        consistency["downgrade_reasons"] = _dedupe([
            *list(consistency.get("downgrade_reasons") or []),
            "primary target is an unrequested marker helper with equal-confidence alternatives",
        ])
    tie_resolved_by: str | None = None
    if tied_alternatives and tie_resolved_by_lsp:
        tie_resolved_by = "lsp"
    elif tied_alternatives and tie_resolved_by_validation:
        tie_resolved_by = "targeted-validation"
    lsp_resolution_evidence = (
        _lsp_tie_resolution_evidence(target, tie_candidates) if tie_resolved_by == "lsp" else []
    )
    if tied_alternatives and tie_resolved_by is not None:
        consistency["alternative_confidence_tie_resolved_by"] = tie_resolved_by
        if tie_resolved_by == "targeted-validation":
            consistency["alternative_confidence_tie_resolution_evidence"] = (
                targeted_validation_evidence
            )
        elif tie_resolved_by == "lsp":
            consistency["alternative_confidence_tie_resolution_evidence"] = lsp_resolution_evidence
        tied_alternatives = []
    if tied_alternatives:
        confidence["overall"] = round(min(float(confidence["overall"]), 0.74), 3)
        confidence["downgrade_reasons"] = _dedupe([
            *list(confidence.get("downgrade_reasons") or []),
            "alternative target confidence tie",
        ])
        consistency["confidence_downgraded"] = True
        consistency["downgrade_reasons"] = _dedupe([
            *list(consistency.get("downgrade_reasons") or []),
            "alternative target confidence tie",
        ])
        _cap_primary_target_confidence(target, 0.74)
        _cap_alternative_target_confidences(alternatives, target)
        tied_alternatives = _tied_alternative_targets(query, alternatives, target)
        tie_candidates = list(tied_alternatives)
    consistency["alternative_confidence_tie"] = bool(tied_alternatives)
    consistency["alternative_confidence_tie_count"] = len(tied_alternatives)
    consistency["tied_alternative_targets"] = tied_alternatives
    consistency["alternative_confidence_tie_candidate_count"] = len(tie_candidates)
    consistency["alternative_confidence_tie_candidates"] = tie_candidates
    ambiguity = {
        "status": "none",
        "requires_confirmation": False,
        "tie_count": 0,
        "tied_alternative_targets": [],
    }
    if tied_alternatives:
        ambiguity = {
            "status": "tie_requires_confirmation",
            "requires_confirmation": True,
            "tie_count": len(tied_alternatives),
            "tied_alternative_targets": tied_alternatives,
        }
    elif tie_candidates and tie_resolved_by is not None:
        ambiguity = {
            "status": "tie_resolved",
            "resolved_by": tie_resolved_by,
            "requires_confirmation": False,
            "tie_count": len(tie_candidates),
            "tied_alternative_targets": tie_candidates,
        }
        if tie_resolved_by == "targeted-validation":
            ambiguity["resolution_evidence"] = targeted_validation_evidence
        elif tie_resolved_by == "lsp":
            ambiguity["resolution_evidence"] = lsp_resolution_evidence
    ask_reasons: list[str] = []
    ask_reasons.extend(trust["ask_reasons"])
    # Degrade-to-ask safety floor: if ranking buried the implementation so the swap helper found no
    # candidate to promote and the post-swap primary is STILL an unrequested marker-helper, never
    # confidently auto-edit it — gate behind ask-user. No-op once the swap promoted the impl.
    if _primary_target_is_unrequested_marker_helper(query, target):
        ask_reasons.append(
            "primary target is an unrequested marker-helper; confirm the intended edit target"
        )
    if tied_alternatives:
        ask_reasons.append("alternative target confidence ties primary target")
    if not validation_commands:
        if suggested_validation_commands:
            # Confidence/tie logic never sees this — the strict field stays empty and
            # `required` stays True either way; this only softens the human-facing text.
            ask_reasons.append(
                "no validation command evidence "
                "(an unverified suggested_validation_commands entry is available)"
            )
        else:
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
        semantic_provider=str(
            payload.get("semantic_provider")
            or effective_semantic_provider
            or requested_semantic_provider
        ),
    )
    related_call_sites, call_site_evidence = _collect_capsule_call_site_evidence(
        query,
        resolved_path,
        target,
        include_blast_radius=include_blast_radius,
        max_files=max_files,
        max_repo_files=max_repo_files,
    )
    # F4: verified call-site evidence is only available NOW (after the collection above), so the
    # token-budget-omission confidence uplift must happen here, post-hoc, rather than inside
    # `_confidence` -- see `_apply_capsule_token_budget_confidence_uplift`'s docstring.
    _apply_capsule_token_budget_confidence_uplift(
        query=query,
        target=target,
        alternatives=alternatives,
        snippets=snippets,
        consistency=consistency,
        confidence=confidence,
        confidence_cap=confidence_cap,
        call_site_evidence=call_site_evidence,
        ask_reasons=ask_reasons,
    )
    rollback_ref = _command_ref(["tg", "checkpoint", "create", resolved_path])
    route_rationale: list[dict[str, Any]] = [
        {
            "strategy": "context-render",
            "evidence": "heuristic",
            "reason": "highest ranked edit target from context-render",
        }
    ]
    if call_site_evidence.get("status") == "collected":
        route_rationale.append({
            "strategy": "blast-radius-call-sites",
            "evidence": ", ".join(_as_list_of_strings(call_site_evidence.get("provenance"))),
            "reason": "verified direct call-site evidence collected for explicit primary symbol",
        })
    gpu_acceleration = _agent_gpu_evidence(
        query,
        resolved_path,
        gpu_device_ids=gpu_device_ids,
        max_files=max_files,
        timeout_s=gpu_timeout_s,
    )
    if gpu_acceleration["status"] != "not_requested":
        matched_files = {
            str(Path(file_path).resolve())
            for file_path in _as_list_of_strings(gpu_acceleration.get("matched_files"))
        }
        primary_file = str(target.get("file") or "")
        primary_matched = bool(primary_file and str(Path(primary_file).resolve()) in matched_files)
        consistency["gpu_evidence_primary_file_matched"] = primary_matched
        consistency["gpu_evidence_matched_files"] = list(matched_files)
        if gpu_acceleration["status"] == "used":
            route_rationale.append({
                "strategy": "gpu-native-evidence",
                "evidence": gpu_acceleration.get("routing_backend", "NativeGpuBackend"),
                "reason": "batched query terms matched via explicit native GPU route",
            })
        else:
            route_rationale.append({
                "strategy": "gpu-evidence-probe",
                "evidence": str(
                    gpu_acceleration.get("routing_backend") or gpu_acceleration.get("status")
                ),
                "reason": str(gpu_acceleration.get("reason") or ""),
            })

    return {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "agent-context-capsule",
        "capsule_version": 1,
        "capsule_schema_version": 1,
        "capsule_kind": "actionable_context",
        "query": query,
        "path": resolved_path,
        "semantic_provider": str(
            payload.get("semantic_provider")
            or effective_semantic_provider
            or requested_semantic_provider
        ),
        "ambiguity": ambiguity,
        "primary_target": target,
        "alternative_targets": alternatives,
        "route_rationale": route_rationale,
        "snippets": snippets,
        "related_call_sites": related_call_sites,
        "call_site_evidence": call_site_evidence,
        "gpu_acceleration": gpu_acceleration,
        "validation_plan": validation_plan,
        "validation_commands": validation_commands,
        "suggested_validation_commands": suggested_validation_commands,
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
    semantic_provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    ignore: tuple[str, ...] = (),
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
            semantic_provider=semantic_provider,
            gpu_device_ids=gpu_device_ids,
            gpu_timeout_s=gpu_timeout_s,
            ignore=ignore,
        ),
        ensure_ascii=False,
        indent=2,
    )
