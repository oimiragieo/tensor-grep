from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_SCAN_CAP as _BEST_EFFORT_PRIMARY_SCAN_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_BOOST_ENV as _CAPSULE_LSP_CONFIDENCE_BOOST_ENV,
)
from tensor_grep.core.retrieval_lexical import split_terms


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


def _suggested_scope_from_tied_targets(
    root: Path,
    target: dict[str, Any],
    tied_alternative_targets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Fallback narrowing hint for a genuine ``tie_requires_confirmation`` ambiguity when
    ``orient_capsule._suggested_scope_from_map``'s whole-repo centrality rollup declines to answer
    (it returns ``None`` whenever the signal is flat or the top two directories are tied/near-tied --
    exactly the common shape on a big/ambiguous repo: the confirmation-tie case this fallback
    targets). Derives the deepest common parent DIRECTORY of every tied candidate's file path (the
    primary target plus each ``tied_alternative_targets`` entry) instead of a centrality guess, so
    the hint is always anchored to the files the tie itself implicates, never a repo-wide heuristic
    that may have nothing to do with the ambiguous symbols.

    Returns ``None`` (never fabricates a guess) when:
      * there are no candidate file paths to compare;
      * the paths don't share a common parent at all (``ValueError`` from ``os.path.commonpath``,
        e.g. mixed drives on Windows);
      * the common parent is ``root`` itself or lies OUTSIDE ``root`` -- re-suggesting the scan root
        is not a narrowing hint, and a path outside root is nonsensical for a re-scoped
        ``tg agent <suggested_scope>`` re-run.
    """
    candidate_files = [str(target.get("file") or "")]
    candidate_files.extend(
        str(alternative.get("file") or "") for alternative in tied_alternative_targets
    )
    directories = sorted({
        str(Path(file_path).parent) for file_path in candidate_files if file_path
    })
    if not directories:
        return None
    try:
        common_parent = Path(os.path.commonpath(directories))
    except ValueError:
        return None  # e.g. mixed drives on Windows -- no meaningful common parent
    # Defense-in-depth: lexically collapse any ``..`` before the containment check so this
    # confinement guard is self-enforcing and never silently depends on callers pre-resolving
    # paths (every current caller does, but a future refactor might not -- a latent path-escape
    # if it ever regresses). ``os.path.normpath``, NOT ``Path.resolve()``: resolve() touches the
    # filesystem and would inject a drive letter on the synthetic paths used in unit tests; we
    # only want a lexical ``..`` collapse here.
    normalized_parent = Path(os.path.normpath(str(common_parent)))
    normalized_root = Path(os.path.normpath(str(root)))
    try:
        relative = normalized_parent.relative_to(normalized_root)
    except ValueError:
        return None  # common parent is not under the scan root (incl. a ``..``-escape past it)
    if relative == Path() or ".." in relative.parts:
        return None  # common parent IS the scan root, or escaped it via ``..`` -- suggest neither
    return {"dirs": [str(normalized_parent)], "confidence": "heuristic"}


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


def _cli_dispatcher_implementation_candidate(
    primary_target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Task #250: return the alternative a GENUINELY THIN CLI-dispatcher primary target provably
    calls through to, or ``None`` if the primary is not a provable thin pass-through.

    Conservative by design: a bare "primary lives under cli/" is NOT enough on its own --
    `cli/main.py` is the CORRECT target for plenty of tasks (e.g. "add a --flag to tg search",
    where the flag registration itself lives in that Typer command's own signature). This only
    fires when ALL THREE hold (all three are checked inside
    ``repo_map._thin_cli_dispatcher_call_targets``, gate NIT-1 on #693):

      1. the primary symbol is decorated as a Typer/Click command (``@x.command(...)``) -- the
         structural signature of a dispatcher, not a guess from its name or lexical score;
      2. the primary symbol is STRUCTURALLY small -- at most
         ``repo_map._THIN_DISPATCHER_MAX_BODY_STATEMENTS`` top-level body statements (docstring
         excluded) and at most ``repo_map._THIN_DISPATCHER_MAX_CALL_TARGETS`` distinct callee
         names. Decoration alone is NOT sufficient: an independent review found
         ``search_command`` (the real, ~1500-line implementation of ``tg search`` in
         ``cli/main.py``) is ALSO a ``.command``-decorated function that calls dozens of names --
         without this size gate, the swap's safety would be an EMERGENT property of which names
         happen to rank as alternatives, not a real structural guarantee; and
      3. the primary symbol's OWN body (``_thin_cli_dispatcher_call_targets`` -- a bounded,
         already-selected span, not a new scan) contains a direct call to a SPECIFIC alternative
         candidate's symbol, defined in a DIFFERENT file -- i.e. the dispatcher hands off to that
         exact implementation.

    Real repro (task #250): ``tg prepare`` resolving "fix the ledger claim TTL logic" to
    ``cli/main.py``'s ``ledger_claim`` (a ``@ledger_app.command("claim")`` dispatcher whose body
    is a single call to ``ledger_store.submit_claim(...)``) instead of the real implementation in
    ``cli/ledger_store.py``, purely because ``ledger_claim`` happens to lexically match both
    query words at once. This helper recognizes that shape and prefers the callee.
    """
    file_path = str(primary_target.get("file") or "")
    symbol_name = str(primary_target.get("symbol") or "")
    if not file_path or not symbol_name:
        return None
    if str(primary_target.get("kind") or "") not in {"function", "method"}:
        return None

    candidates = [
        alternative
        for alternative in alternatives
        if str(alternative.get("symbol") or "")
        and str(alternative.get("file") or "") not in ("", file_path)
        and str(alternative.get("kind") or "") in {"function", "method"}
    ]
    if not candidates:
        return None

    line = primary_target.get("line")
    expected_line: int | None = None
    if isinstance(line, int):
        expected_line = line
    elif isinstance(line, str) and line.isdigit():
        expected_line = int(line)
    called_names = repo_map._thin_cli_dispatcher_call_targets(
        file_path, symbol_name, expected_line=expected_line
    )
    if not called_names:
        return None

    matched = [candidate for candidate in candidates if str(candidate["symbol"]) in called_names]
    if not matched:
        return None
    matched.sort(key=lambda candidate: _numeric_confidence(candidate.get("confidence"), 0.0))
    return matched[-1]


def _prefer_implementation_over_cli_dispatcher_helper(
    primary_target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Analogous to `_prefer_implementation_over_marker_helper` above (task #250): when the
    primary target is a provable thin CLI-dispatcher call-through
    (`_cli_dispatcher_implementation_candidate`), swap it with the specific implementation
    alternative it calls -- the dispatcher becomes a (still-surfaced) alternative instead of
    silently disappearing, so the same tie/ambiguity signals still apply downstream."""
    implementation = _cli_dispatcher_implementation_candidate(primary_target, alternatives)
    if implementation is None:
        return primary_target, alternatives
    demoted = [alternative for alternative in alternatives if alternative is not implementation]
    demoted.insert(0, primary_target)
    return implementation, demoted


def _prefer_public_implementation_over_private_helper(
    query: str,
    primary_target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Promote a genuine public implementation over an unrequested private helper primary (e.g. `_add`).

    When the query does NOT explicitly search for a `_`-prefixed identifier, but BM25/lexical
    scoring selects a private helper (`_add`) that happens to match a common verb ("add"),
    and viable public non-test function alternatives exist (confidence >= 0.7 with term overlap),
    prefer the public implementation.
    """
    symbol = str(primary_target.get("symbol") or "")
    if not (symbol.startswith("_") and not symbol.startswith("__")):
        return primary_target, alternatives

    query_terms = set(repo_map._query_terms(query))
    if any(term.startswith("_") for term in query_terms):
        return primary_target, alternatives

    best_index = -1
    best_confidence = -1.0
    for index, alternative in enumerate(alternatives):
        alt_symbol = str(alternative.get("symbol") or "")
        if not alt_symbol or alt_symbol.startswith("_"):
            continue
        alt_kind = str(alternative.get("kind") or "")
        if alt_kind not in {"function", "method", "class"}:
            continue
        alt_confidence = _numeric_confidence(alternative.get("confidence"), 0.0)
        if alt_confidence < 0.7:
            continue
        alt_terms = set(split_terms(alt_symbol))
        if not (alt_terms & query_terms):
            continue
        if alt_confidence > best_confidence:
            best_confidence = alt_confidence
            best_index = index

    if best_index < 0:
        return primary_target, alternatives

    public_impl = alternatives[best_index]
    demoted = [*alternatives[:best_index], *alternatives[best_index + 1 :]]
    demoted.insert(0, primary_target)
    return public_impl, demoted


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


def _best_effort_primary_target_from_map(rm: dict[str, Any], query: str) -> dict[str, Any] | None:
    """v20 dogfood gap #2: derive a BEST-EFFORT primary target straight from the already-scanned
    ``rm`` when the real ranking pass (``_primary_target``) came back empty on a truncated scan.
    No second scan, no I/O -- every candidate below is scored purely off data ``rm`` already
    holds. Cheapest/most-specific signal first:

      (a) a scanned SYMBOL whose name matches the query, scored with the exact
          ``repo_map._score_symbol`` a real ranking pass would use;
      (b) else a scanned FILE PATH that matches the query (``repo_map._score_file_path``);
      (c) else the single most-central scanned file -- a query-independent last resort, reusing
          the same composite ``orient_capsule._file_centrality_scores`` that ``tg orient``'s own
          ``suggested_scope`` already applies unconditionally in this exact truncated-scan tail
          (``build_agent_capsule_from_map``'s own ``suggested_scope_from_map`` call above does the
          same thing for the same reason).

    Each of (a)/(b) is capped to the first ``_BEST_EFFORT_PRIMARY_SCAN_CAP`` items ``rm`` holds --
    see that constant's comment for why this is an item-count cap, not a deadline re-check.
    Returns ``None`` (never fabricates) when ``rm`` has nothing to score at all. The caller owns
    stamping ``partial_primary``/``primary_basis`` and letting every existing confidence-cap/
    ask-reason gate re-run over the result -- this helper returns a bare best-guess location only,
    never a fully-shaped primary-target dict.
    """
    symbols = _as_list_of_dicts(rm.get("symbols"))[:_BEST_EFFORT_PRIMARY_SCAN_CAP]
    symbol_terms = repo_map._symbol_query_terms(query)
    if symbols and symbol_terms:
        # Task #254 heuristic 2: derived from the SAME already-capped `symbols` list (never a
        # fresh unbounded pass over `rm["symbols"]`) so this stays within the bounded-cost
        # contract `test_best_effort_helper_symbol_pass_never_looks_past_the_scan_cap` pins.
        non_test_definition_names = repo_map._non_test_definition_names(symbols)
        best_symbol: dict[str, Any] | None = None
        best_symbol_score = 0
        for symbol in symbols:
            name = symbol.get("name")
            file_path = symbol.get("file")
            if not name or not file_path:
                continue
            # Normalize defensively -- `_score_symbol` indexes "name"/"kind"/"file" directly, and
            # this helper must never crash the capsule even if a language-specific extractor ever
            # omits "kind" on some symbol record.
            scoreable = {"name": name, "kind": symbol.get("kind") or "unknown", "file": file_path}
            score = repo_map._score_symbol(
                scoreable, symbol_terms, non_test_definition_names=non_test_definition_names
            )
            if score > best_symbol_score:
                best_symbol_score = score
                best_symbol = symbol
        if best_symbol is not None:
            line = best_symbol.get("line") or best_symbol.get("start_line") or 1
            return {
                "file": str(best_symbol.get("file") or ""),
                "symbol": best_symbol.get("name"),
                "kind": str(best_symbol.get("kind") or "unknown"),
                "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
            }

    files = [str(current) for current in (rm.get("files") or [])][:_BEST_EFFORT_PRIMARY_SCAN_CAP]
    file_terms = repo_map._query_terms(query)
    if files and file_terms:
        best_file: str | None = None
        best_file_score = 0
        for candidate_file in files:
            score = repo_map._score_file_path(candidate_file, file_terms)
            if score > best_file_score:
                best_file_score = score
                best_file = candidate_file
        if best_file is not None:
            return {"file": best_file, "symbol": None, "kind": "unknown", "line": 1}

    # Local import avoids a module-level circular import -- same discipline every other
    # `orient_capsule` reuse in this module already follows (see e.g. `build_agent_capsule_
    # from_map`'s own docstring for why).
    from tensor_grep.cli.orient_capsule import _file_centrality_scores

    code_files, centrality = _file_centrality_scores(rm)
    if code_files:
        top_file = sorted(code_files, key=lambda current: (-centrality.get(current, 0.0), current))[
            0
        ]
        return {"file": top_file, "symbol": None, "kind": "unknown", "line": 1}

    return None


def _target_symbol_was_explicitly_requested(query: str, target: dict[str, Any]) -> bool:
    symbol = str(target.get("symbol") or "")
    return bool(symbol and repo_map._symbol_name_matches_query_exactly(symbol, query))


def _maybe_fuse_semantic_dense_target(
    query: str,
    target: dict[str, Any],
    alternatives: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """P0: Fuse semantic dense retrieval when lexical primary target confidence is below 0.60.

    If lexical confidence is already >= 0.60, or if alternatives are empty, or if dense
    extras/models are unavailable, cleanly bypasses or fails open to existing lexical targets.
    When dense ranking is available and an alternative ranks highest semantically, promotes
    that alternative to primary with reciprocal-rank-fused confidence.
    """
    primary_conf = _numeric_confidence(target.get("confidence"), 0.0)
    if primary_conf >= 0.60 or not alternatives:
        return target, alternatives

    try:
        from tensor_grep.core.retrieval_chunker import Chunk
        from tensor_grep.core.retrieval_dense import (
            DenseIndex,
            default_model_dir,
            dense_available,
            load_dense_model,
        )
        from tensor_grep.core.retrieval_fusion import reciprocal_rank_fusion

        available, _ = dense_available()
        if not available:
            return target, alternatives

        model = load_dense_model(default_model_dir())
        candidates = [target, *alternatives]
        chunks: list[Chunk] = []
        for _idx, cand in enumerate(candidates):
            sym = str(cand.get("symbol") or "")
            fpath = str(cand.get("file") or "")
            text = f"{sym} {fpath}" if sym else fpath
            chunks.append(Chunk(file_path=fpath, start_line=1, end_line=1, text=text))

        dense_idx = DenseIndex(chunks, model)
        dense_results = dense_idx.query(query, top_k=len(candidates))
        if not dense_results:
            return target, alternatives

        dense_order = [idx for idx, _ in dense_results]
        bm25_order = list(range(len(candidates)))
        # Confidence-calibrated fusion weights: when primary lexical confidence is low (<0.6),
        # weight the dense leg higher (1.0 vs primary_conf) so clear semantic alignment wins ties.
        lexical_weight = max(0.2, min(0.9, primary_conf))
        fused_order = reciprocal_rank_fusion(
            [bm25_order, dense_order],
            weights=[lexical_weight, 1.0],
            combine="max",
        )
        best_candidate_idx = fused_order[0] if fused_order else 0

        if best_candidate_idx != 0:
            promoted = dict(candidates[best_candidate_idx])
            promoted["semantic_fused"] = True
            promoted["confidence"] = round(max(0.72, primary_conf + 0.35), 3)
            promoted_evidence = list(promoted.get("evidence") or [])
            if "semantic-dense-fusion" not in promoted_evidence:
                promoted_evidence.append("semantic-dense-fusion")
            promoted["evidence"] = promoted_evidence

            new_alternatives = [target]
            for idx, cand in enumerate(candidates[1:], start=1):
                if idx != best_candidate_idx:
                    new_alternatives.append(cand)
            return promoted, new_alternatives

    except Exception:
        # Fail-closed / fail-safe: never crash agent capsule on optional dense model faults
        return target, alternatives

    return target, alternatives
