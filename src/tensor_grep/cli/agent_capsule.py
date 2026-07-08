from __future__ import annotations

import builtins
import json
import keyword
import os
import re
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
# Historical uplift ceiling for a *corroborated* token-budget-only primary omission. Deliberately
# below the uncapped 0.9 default and matched to the >=0.75 "no ask-user" threshold (agent_capsule.py
# `ask_user_before_editing` construction) -- this is a bounded relief from the 0.55 safety floor,
# not a return to full confidence. Kept for documentation of the original F4 floor; the active
# uplift ceiling used by `_apply_capsule_token_budget_confidence_uplift` is
# `_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP` below.
_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP = 0.75
# T2: a render-token-budget-only cut (`payload["truncated"]` cut some OTHER, lower-ranked source --
# the primary's OWN snippet still fits) is the SAME class of artifact as the capsule-own-budget
# primary omission above: a render/token-budget signal, not a resolution-quality signal. Once
# blast-radius call-site collection has graph-corroborated the primary, BOTH cases may rise to this
# higher ceiling rather than sitting at the historical 0.75 floor.
_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP = 0.8
# The exact downgrade-reason strings that mean "confidence was reduced ONLY by a token/render
# budget artifact, not by a genuine resolution-ambiguity signal". Any OTHER downgrade reason
# (language mismatch, validation misalignment, alternative-target tie, marker-helper demotion)
# must disqualify the corroborated-resolution uplift below.
_CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS = frozenset({
    "primary file omitted from capsule snippets by token budget",
    "context omitted by token or render budget",
})

# PR-1 (1D): a truncated repo SCAN (as opposed to the capsule's own render/token OUTPUT budget) is
# a genuine ambiguity signal, never a budget-only artifact -- deliberately kept OUT of
# `_CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS` above so the T2 uplift's `other_reasons` scan
# disqualifies the corroborated-resolution uplift even if the dedicated `scan_truncated`
# early-return in `_capsule_token_budget_uplift_eligible` is ever refactored away.
_CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON = "repository scan truncated before ranking completed"
_CAPSULE_SCAN_TRUNCATED_ASK_REASON = (
    "repository scan was truncated; the ranked primary may not be the true target"
)


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
    seed_confidence: float,
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
    # T2: gate on the caller-supplied PRE-cap seed confidence, not `target["confidence"]` -- by the
    # time this runs, `target["confidence"]` may already have been mutated down by this module's
    # OWN trust/tie/budget caps (`build_agent_capsule`). Gating on that post-cap value is circular:
    # a target capped below 0.75 could never earn the very call-site evidence that would justify
    # relief from that cap. See `_apply_capsule_token_budget_confidence_uplift`.
    if seed_confidence < 0.75:
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


# DAR (Dependency-Aware Retrieval, arxiv steal #4): surface the primary target's OUTBOUND
# dependencies (imports + callees) as budget-isolated related-context, so an agent can edit
# without extra file reads. THE TRAP: `payload["symbols"]`/`payload["imports"]` (the whole-repo
# tables) are POPPED by compact rendering (`repo_map._COMPACT_CONTEXT_RENDER_OMITTED_KEYS`) --
# `build_agent_capsule` always requests `render_profile="full"` + `optimize_context=True`, which
# `repo_map._normalize_render_profile` downgrades to "compact". A naive `payload["imports"]` read
# is therefore SILENTLY EMPTY FOREVER. The data sources below are the ones that survive compact:
# a fresh single-file parse of the primary (`repo_map._imports_and_symbols_for_path`, cached), a
# call-token scan of the primary's OWN rendered snippet source, and the two compact survivors
# `file_summaries` / `candidate_edit_targets.symbols` for resolving callees to file+line.
_CAPSULE_OUTBOUND_DEPENDENCIES_ENV = "TG_CAPSULE_OUTBOUND_DEPS"
_CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT = 240
_CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS = frozenset(keyword.kwlist) | frozenset(dir(builtins))
_CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY = {"call+import": 0, "call": 1, "import": 2}


def _capsule_outbound_dependencies_enabled() -> bool:
    """Always-ON kill-switch -- the OPPOSITE default polarity from
    `_capsule_lsp_confidence_boost_enabled` (that one is opt-IN), but the same off-value parsing:
    any of `{"", "0", "false", "no", "off"}` (case-insensitive) disables DAR.
    """
    raw = os.environ.get(_CAPSULE_OUTBOUND_DEPENDENCIES_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _outbound_dependency_import_tails(imports: list[str]) -> dict[str, str]:
    """tail (last dotted segment) -> the qualified import string it came from.

    Only DOTTED import strings corroborate a candidate: `from src.tax import compute_tax`
    produces "src.tax.compute_tax" (tail "compute_tax") in
    `repo_map._imports_and_symbols_for_path`'s output. A bare top-level `import module` entry
    (no dot) is deliberately excluded here -- "drop bare third-party/stdlib module strings": a
    bare module name alone (e.g. `import requests`) is too weak a signal and would otherwise let
    an unrelated third-party package corroborate a same-named local call token.
    """
    tails: dict[str, str] = {}
    for raw in imports:
        text = str(raw)
        if "." not in text:
            continue
        tail = text.rsplit(".", 1)[-1]
        if tail and tail not in tails:
            tails[tail] = text
    return tails


def _outbound_dependency_selected_symbol_locations(
    payload: dict[str, Any],
    *,
    exclude_file: str,
) -> dict[str, dict[str, Any]]:
    """symbol name -> {file, line, kind, provenance} for symbols defined in OTHER selected files.

    `file_summaries` and `candidate_edit_targets.symbols` are the two survivors of compact
    rendering (see module-level DAR comment above) and both are already scoped to files the
    ranking SELECTED -- exactly the corroboration DAR needs: a call token only counts as an
    outbound dependency when it resolves inside a file the agent is already looking at, never an
    arbitrary whole-repo symbol.
    """
    locations: dict[str, dict[str, Any]] = {}
    for summary in _as_list_of_dicts(payload.get("file_summaries")):
        file_path = str(summary.get("path") or "")
        if not file_path or file_path == exclude_file:
            continue
        for symbol in _as_list_of_dicts(summary.get("symbols")):
            name = str(symbol.get("name") or "")
            if not name or name in locations:
                continue
            raw_line = symbol.get("line") or 1
            try:
                line = max(1, int(str(raw_line)))
            except (TypeError, ValueError):
                line = 1
            locations[name] = {
                "file": file_path,
                "line": line,
                "kind": str(symbol.get("kind") or "unknown"),
                "provenance": "parser-backed",
            }

    candidate_targets = _as_dict(payload.get("candidate_edit_targets"))
    for symbol in _as_list_of_dicts(candidate_targets.get("symbols")):
        name = str(symbol.get("name") or "")
        file_path = str(symbol.get("file") or "")
        if not name or not file_path or file_path == exclude_file or name in locations:
            continue
        raw_line = symbol.get("line") or symbol.get("start_line") or 1
        try:
            line = max(1, int(str(raw_line)))
        except (TypeError, ValueError):
            line = 1
        locations[name] = {
            "file": file_path,
            "line": line,
            "kind": str(symbol.get("kind") or "unknown"),
            "provenance": str(symbol.get("provenance") or "parser-backed"),
        }
    return locations


def _outbound_dependency_call_tokens(source: str, start_line: int) -> list[tuple[str, int]]:
    """First-use `(name, line)` pairs for `name(` call-shaped tokens in `source`, source order.

    First occurrence per NAME wins -- feeds the "tie-break first-use line" selection rule.
    """
    seen: set[str] = set()
    tokens: list[tuple[str, int]] = []
    for offset, line_text in enumerate(source.splitlines()):
        for match in _CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE.finditer(line_text):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            tokens.append((name, start_line + offset))
    return tokens


def _outbound_dependency_line_preview(source: str, start_line: int, line: int) -> str:
    lines = source.splitlines()
    index = line - start_line
    if 0 <= index < len(lines):
        return lines[index].strip()
    return ""


def _collect_outbound_dependencies(
    query: str,
    path: str,
    target: dict[str, Any],
    payload: dict[str, Any],
    snippets: list[dict[str, Any]],
    related_call_sites: list[dict[str, Any]],
    *,
    max_files: int,
    preview_token_budget: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """DAR (arxiv steal #4): the primary target's outbound dependencies, corroboration-gated.

    A candidate call token is kept ONLY if it (i) resolves to a symbol defined in another
    SELECTED file (-> file+line+provenance, `dependency_kind` "call") OR (ii) matches an import
    tail (`dependency_kind` "import", `file` null, provenance "import-heuristic") -- or both
    (`dependency_kind` "call+import"). This is deliberately NOT a bare-regex scan: an unresolved,
    un-imported call token is dropped as noise (the diff-docs/DocPrism false-positive lesson).

    BUDGET ISOLATION (load-bearing): this function NEVER evicts a snippet, caller, or changes any
    omission reason -- the records it returns are metadata outside the snippet token budget, same
    as `related_call_sites`. Only the OPTIONAL `text` preview on each record is budgeted, from the
    caller-supplied `preview_token_budget` (upstream `max_tokens` leftover after snippets) --
    `None` means unlimited (no `max_tokens` cap was requested at all upstream either).

    FAIL-SAFE (byte-identical contract): every early return here is `([], {})` -- the caller MUST
    treat that as "emit NEITHER `outbound_dependencies` nor `outbound_dependency_evidence`", never
    an empty-but-present key. See `build_agent_capsule`.
    """
    if not _capsule_outbound_dependencies_enabled():
        return [], {}
    primary_file = str(target.get("file") or "")
    primary_symbol = str(target.get("symbol") or "")
    if not primary_file or not primary_symbol:
        return [], {}
    primary_snippet = next(
        (snippet for snippet in snippets if str(snippet.get("file") or "") == primary_file),
        None,
    )
    if primary_snippet is None:
        return [], {}
    source = str(primary_snippet.get("source") or "")
    if not source.strip():
        return [], {}
    try:
        start_line = max(1, int(str(primary_snippet.get("start_line") or 1)))
    except (TypeError, ValueError):
        start_line = 1

    try:
        imports, primary_symbols = repo_map._imports_and_symbols_for_path(Path(primary_file))
    except Exception:  # pragma: no cover - defensive; DAR must never break the capsule
        return [], {}

    locally_defined = {
        str(symbol.get("name") or "") for symbol in primary_symbols if symbol.get("name")
    }
    import_tails = _outbound_dependency_import_tails(imports)
    resolved_locations = _outbound_dependency_selected_symbol_locations(
        payload,
        exclude_file=primary_file,
    )
    excluded_pairs = {
        (str(record.get("file") or ""), str(record.get("symbol") or ""))
        for record in related_call_sites
    }

    candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for name, line in _outbound_dependency_call_tokens(source, start_line):
        if not name or name == primary_symbol:
            continue
        if name in _CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS or name in locally_defined:
            continue
        resolution = resolved_locations.get(name)
        import_source = import_tails.get(name)
        if resolution is None and import_source is None:
            continue
        resolved_file = str(resolution["file"]) if resolution else None
        key = (resolved_file or "", name)
        if key in seen_keys or key in excluded_pairs:
            continue
        seen_keys.add(key)
        if resolution is not None and import_source is not None:
            dependency_kind = "call+import"
        elif resolution is not None:
            dependency_kind = "call"
        else:
            dependency_kind = "import"
        candidates.append({
            "file": resolved_file,
            "line": int(resolution["line"]) if resolution else None,
            "symbol": name,
            "kind": str(resolution["kind"]) if resolution else "unknown",
            "relation": "outbound-dependency",
            "dependency_kind": dependency_kind,
            "provenance": str(resolution["provenance"]) if resolution else "import-heuristic",
            "reason": "primary target calls this symbol",
            "_first_use_line": line,
        })

    if not candidates:
        return [], {}

    candidates.sort(
        key=lambda item: (
            _CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY.get(str(item["dependency_kind"]), 3),
            int(item["_first_use_line"]),
        )
    )
    limit = max(1, min(int(max_files) * 2, 8))
    kept = candidates[:limit]
    omitted_count = max(0, len(candidates) - len(kept))

    unlimited_preview = preview_token_budget is None
    remaining_preview_budget: int | None = (
        None if preview_token_budget is None else max(0, int(preview_token_budget))
    )
    records: list[dict[str, Any]] = []
    for candidate in kept:
        record = {key: value for key, value in candidate.items() if key != "_first_use_line"}
        preview = _outbound_dependency_line_preview(
            source,
            start_line,
            int(candidate["_first_use_line"]),
        )[:_CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT]
        if preview:
            if unlimited_preview:
                record["text"] = preview
            else:
                token_cost = repo_map._estimate_tokens(preview)
                if remaining_preview_budget is not None and token_cost <= remaining_preview_budget:
                    record["text"] = preview
                    remaining_preview_budget -= token_cost
        refetch = _source_refetch_ref(
            {"file": candidate["file"], "symbol": candidate["symbol"]},
            query,
            path,
            max_files,
        )
        record["refetch"] = {"command": refetch["command"], "argv": refetch["argv"]}
        records.append(record)

    evidence = {
        "status": "collected",
        "symbol": primary_symbol,
        "returned_dependencies": len(records),
        "omitted_dependencies": omitted_count,
        "max_dependencies": limit,
        "provenance": _dedupe([str(record["provenance"]) for record in records]),
        "preview_token_budget_remaining": (None if unlimited_preview else remaining_preview_budget),
    }
    return records, evidence


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
    scan_truncated: bool,
) -> bool:
    """T2: eligibility for the corroborated-resolution uplift, covering BOTH the original
    capsule-own-budget primary omission (F4) and a render-truncated-only cut where the primary's
    OWN snippet is fully present (`payload["truncated"]` cut some other, lower-ranked source).

    Every genuine-ambiguity signal must disqualify this uplift: a primary the ranking never
    selected/rendered at all, an unresolved alternative-target tie, an unrequested marker-helper
    demotion, or any downgrade reason outside the render/token-budget family (language mismatch,
    validation misalignment, ...). Only a target independently corroborated by the query AND by
    verified call-site evidence is eligible.

    PR-1 (1D): a TRUNCATED repo scan (`scan_truncated`, from `_capsule_scan_incomplete` on the
    inner context-render payload) is ALSO a first-class disqualifier, checked first -- the ranking
    that produced this "corroborated" primary never saw the whole repository, so a capped-scan
    primary may simply be the best candidate among the files that were visible, not the true best
    candidate. Call-site evidence collected against an incomplete scan cannot repair that.

    NOTE: `other_reasons` deliberately scans only `consistency["downgrade_reasons"]`, not
    `confidence["downgrade_reasons"]` -- the latter also carries a generic "context consistency
    downgraded confidence" restatement whenever `consistency["confidence_downgraded"]` is set for
    ANY reason (including the very budget-only omission this uplift targets), so scanning it would
    make the check disqualify itself. Every genuine (non-budget) cause of that flag already leaves
    its own specific, non-generic text in `consistency["downgrade_reasons"]` (trust mismatches) or
    is checked explicitly above (ties, marker-helper demotion, never-ranked primary).
    """
    if scan_truncated:
        return False
    if not snippets:
        return False
    if consistency.get("primary_file_included") is False:
        return False
    if consistency.get("rendered_context_includes_primary") is False:
        return False
    if consistency.get("capsule_primary_file_omitted") and not (
        _capsule_primary_omission_is_token_budget_only(consistency)
    ):
        return False
    if consistency.get("alternative_confidence_tie"):
        return False
    if _primary_target_is_unrequested_marker_helper(query, target):
        return False
    # Require the confidence hit to be ENTIRELY explained by the render/token-budget family -- if a
    # trust-level conflict (language mismatch, validation misalignment) or any other genuine
    # ambiguity signal is ALSO present, leave the existing (conservative) behavior untouched rather
    # than trying to partially unwind a multi-cause downgrade.
    other_reasons = {
        str(reason) for reason in (consistency.get("downgrade_reasons") or []) if reason
    } - _CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS
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
    scan_truncated: bool,
) -> None:
    """Uplift a render/token-budget-only confidence clamp for a CORROBORATED resolution -- never
    for a genuine misroute or a genuine ambiguity (see `_capsule_token_budget_uplift_eligible`).

    T2 generalization: this originally only covered the 0.55 primary-omission clamp (the primary
    file cut from the capsule's OWN snippet budget). It now ALSO covers the 0.72 render-truncated
    tier (`payload["truncated"]` cut some OTHER, lower-ranked source, not the primary) -- both are
    render/token-budget artifacts, not resolution-quality signals, so both are eligible for the
    same corroborated-resolution relief up to `_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP`.

    STRUCTURAL note: this must run AFTER `_collect_capsule_call_site_evidence` (agent_capsule.py
    call order), since verified caller evidence -- the corroboration this uplift depends on --
    isn't available until that call returns. It mutates `confidence`, `target`, `alternatives`,
    `consistency`, and `ask_reasons` in place so `build_agent_capsule`'s already-assembled payload
    reflects the uplift without re-deriving `ask_user_before_editing` from scratch.
    """
    current_overall = float(confidence.get("overall", 0.0))
    uplift_cap = _CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP
    if current_overall >= uplift_cap:
        return
    if not _capsule_token_budget_uplift_eligible(
        query=query,
        target=target,
        snippets=snippets,
        consistency=consistency,
        call_site_evidence=call_site_evidence,
        scan_truncated=scan_truncated,
    ):
        return
    uplifted = round(min(uplift_cap, confidence_cap), 3)
    if uplifted <= current_overall:
        return
    confidence["overall"] = uplifted
    remaining_reasons = [
        reason
        for reason in confidence.get("downgrade_reasons", [])
        if reason not in _CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS
    ]
    remaining_reasons.append(
        "token budget limited rendering only; confidence reflects graph-corroborated resolution"
    )
    confidence["downgrade_reasons"] = remaining_reasons
    consistency["capsule_token_budget_confidence_uplifted"] = True
    consistency["confidence_basis"] = "resolution-quality"
    _cap_primary_target_confidence(target, uplifted)
    _cap_alternative_target_confidences(alternatives, target)
    # These ask-reasons were added solely because of the render/token-budget confidence clamp we
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


def _capsule_scan_incomplete(payload: dict[str, Any]) -> bool:
    """PR-1 (1D): module-local twin of ``main._scan_incomplete`` (``cli/main.py``) -- NOT imported
    from there, since ``main`` imports THIS module and importing back would be circular.

    Checks ONLY the scan-side truncation signals a repo scan can carry: ``scan_limit`` /
    ``caller_scan_limit`` ``possibly_truncated``, and ``partial`` / ``caller_scan_truncated`` (a
    ``--deadline`` cutoff or the caller-scan file ceiling). Deliberately does NOT check
    ``result_incomplete`` -- that key also fires on a pure OUTPUT cap (this capsule's own
    ``--max-tokens``/``--max-files`` snippet budget) which must stay exit 0; only a SCAN
    truncation (the repo file list itself was capped or a parse deadline was hit) means the
    ranking never saw the whole repository. Kept byte-for-byte equivalent to
    ``main._scan_incomplete``'s scan-side checks; pinned by
    ``test_capsule_scan_incomplete_matches_main_scan_incomplete``.
    """
    for key in ("scan_limit", "caller_scan_limit"):
        limit = payload.get(key)
        if isinstance(limit, dict) and limit.get("possibly_truncated"):
            return True
    return bool(payload.get("partial") or payload.get("caller_scan_truncated"))


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
    # PR-1 (1D): whether the underlying repo scan itself (not the capsule's own snippet/token
    # output budget) was truncated -- gates the exit-2-on-scan-truncation contract below and
    # disqualifies the T2 corroborated-resolution confidence uplift (a capped-scan primary may
    # simply be the best candidate among the files that were visible, not the true best one).
    scan_truncated = _capsule_scan_incomplete(payload)
    target = _primary_target(payload)
    all_alternatives = _alternative_targets(payload, target, limit=None)
    alternatives = all_alternatives[:4]
    target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
    # T2: capture the RAW pre-cap seed confidence now, before any of this function's own trust/
    # tie/budget caps mutate `target["confidence"]` in place -- `_collect_capsule_call_site_evidence`
    # must gate on this seed value, not the post-cap one, or a capped target could never earn the
    # call-site evidence that would justify relief from that cap.
    primary_target_seed_confidence = _numeric_confidence(target.get("confidence"), 0.0)
    omitted_alternative_targets = max(0, len(all_alternatives) - len(alternatives))
    snippets, omitted_sources, used_tokens = _build_snippets(
        payload,
        query=query,
        path=resolved_path,
        max_files=max_files,
        max_tokens=max_tokens,
    )
    # DAR budget isolation: upstream (snippets/callers) keeps 100% of `max_tokens` -- DAR records
    # are metadata OUTSIDE that budget. Only the optional preview `text` on a DAR record is
    # budgeted, from whatever `max_tokens` leftover remains after `_build_snippets` above. `None`
    # (no `max_tokens` cap requested) means unlimited previews.
    outbound_dependency_preview_budget = (
        None if max_tokens is None else max(0, int(max_tokens) - int(used_tokens))
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
    # PR-1 (1D) belt+braces: a truncated repo scan is a genuine ambiguity signal on its own --
    # stamp it into context_consistency BEFORE `_confidence` runs, same pattern as the trust-check
    # block above, so it survives independently of the `scan_truncated` early-return disqualifier
    # in `_capsule_token_budget_uplift_eligible`.
    if scan_truncated:
        consistency["confidence_downgraded"] = True
        consistency["downgrade_reasons"] = _dedupe([
            *list(consistency.get("downgrade_reasons") or []),
            _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON,
        ])

    downgrade_reasons: list[str] = list(trust["downgrade_reasons"])
    if scan_truncated:
        downgrade_reasons.append(_CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON)
    confidence = _confidence(payload, snippets, downgrade_reasons, consistency)
    # PR-1 (1D) belt+braces: `_primary_target` seeds `target["confidence"]` from a hardcoded 0.9
    # fallback independent of `confidence["overall"]` (the 1A seed-real-overall fix is a separate,
    # later PR) -- without this explicit cap, a scan-truncated capsule could report
    # `confidence.overall` correctly downgraded while `primary_target.confidence` still reads 0.9,
    # which is exactly the "confident false zero" this fix exists to close.
    if scan_truncated:
        _cap_primary_target_confidence(target, float(confidence["overall"]))
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
    # PR-1 (1D) belt+braces: this string is deliberately distinct from every ask-reason the T2
    # uplift's reason-clearing removes ("confidence below 0.75", "primary file omitted from
    # capsule snippets", "context consistency requires confirmation") -- see
    # `_apply_capsule_token_budget_confidence_uplift`'s reason-clearing list -- so even if the
    # uplift somehow ran anyway, `ask_user_before_editing.required` still forces True here.
    if scan_truncated:
        ask_reasons.append(_CAPSULE_SCAN_TRUNCATED_ASK_REASON)
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
        seed_confidence=primary_target_seed_confidence,
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
        scan_truncated=scan_truncated,
    )
    # DAR (arxiv steal #4): runs AFTER call-site collection so it can dedupe against
    # `related_call_sites`, and deliberately does NOT touch `target`/`confidence`/`consistency`/
    # `ask_reasons` -- see `_collect_outbound_dependencies`'s fail-safe + budget-isolation
    # contract. Never mutates confidence/consistency/trust state (1A owns those).
    outbound_dependencies, outbound_dependency_evidence = _collect_outbound_dependencies(
        query,
        resolved_path,
        target,
        payload,
        snippets,
        related_call_sites,
        max_files=max_files,
        preview_token_budget=outbound_dependency_preview_budget,
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

    result: dict[str, Any] = {
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
    # PR-1 (1D): additively propagate the inner context-render payload's SCAN-side truncation
    # signals onto the capsule -- only when present, mirroring `repo_map._copy_scan_limit` /
    # `_copy_partial_signal`'s shapes without importing repo_map's private helpers (this module
    # already treats `payload` -- the `repo_map.build_context_render` result -- as its own scan
    # source of truth). `result_incomplete` is stamped ONLY on a genuine scan truncation, NEVER on
    # the capsule's own render/token OUTPUT budget (`payload["truncated"]`/`omitted_sections`) --
    # the output-cap-stays-0 contract `main._scan_incomplete` documents.
    scan_limit = payload.get("scan_limit")
    if isinstance(scan_limit, dict):
        result["scan_limit"] = dict(scan_limit)
        if "scan_remediation" in payload:
            result["scan_remediation"] = payload["scan_remediation"]
    if payload.get("partial"):
        result["partial"] = True
        deadline_limit = payload.get("deadline_limit")
        if isinstance(deadline_limit, dict):
            result["deadline_limit"] = dict(deadline_limit)
    if scan_truncated:
        result["result_incomplete"] = True
    # DAR: additive CONDITIONAL keys, same pattern as scan_limit/partial above -- zero deps (or
    # the kill-switch, or a fail-safe early return inside `_collect_outbound_dependencies`) means
    # `outbound_dependencies` is `[]`, and BOTH keys are omitted so the capsule stays
    # byte-identical to a pre-DAR build. Never stamp an empty-but-present key.
    if outbound_dependencies:
        result["outbound_dependencies"] = outbound_dependencies
        result["outbound_dependency_evidence"] = outbound_dependency_evidence
    return result


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
