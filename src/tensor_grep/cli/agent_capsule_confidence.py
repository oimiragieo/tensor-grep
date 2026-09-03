from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS as _CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP as _CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_ASK_REASON as _CAPSULE_SCAN_TRUNCATED_ASK_REASON,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON as _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP as _CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_TOKEN_BUDGET_OMISSION_REASON as _CAPSULE_TOKEN_BUDGET_OMISSION_REASON,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _alternative_targets as _alternative_targets,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _command_ref as _command_ref,
)
from tensor_grep.cli.agent_capsule_snippets import _source_refetch_ref as _source_refetch_ref
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_strings as _as_list_of_strings,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_alternative_target_confidences as _cap_alternative_target_confidences,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_primary_target_confidence as _cap_primary_target_confidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_trust_checks as _capsule_trust_checks,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_validation_alignment as _capsule_validation_alignment,
)
from tensor_grep.cli.agent_capsule_targets import (
    _dedupe as _dedupe,
)
from tensor_grep.cli.agent_capsule_targets import (
    _prefer_implementation_over_cli_dispatcher_helper as _prefer_implementation_over_cli_dispatcher_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _prefer_implementation_over_marker_helper as _prefer_implementation_over_marker_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _prefer_public_implementation_over_private_helper as _prefer_public_implementation_over_private_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _primary_target as _primary_target,
)
from tensor_grep.cli.agent_capsule_targets import (
    _primary_target_is_unrequested_marker_helper as _primary_target_is_unrequested_marker_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _targeted_validation_evidence as _targeted_validation_evidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _tied_alternative_targets as _tied_alternative_targets,
)
from tensor_grep.core.retrieval_lexical import split_terms


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


#: The highest confidence a result carrying ANY downgrade reason may report. Not a calibrated
#: severity -- the branch-specific clamps below own that, because they know WHY a result is
#: degraded. This is only the ceiling that keeps "here is why I am degraded" and "I am certain"
#: from appearing in the same payload, so it is set as close to 1.0 as a strict inequality allows.
_DEGRADED_CONFIDENCE_CEILING = 0.99


def _confidence(
    payload: dict[str, Any],
    snippets: list[dict[str, Any]] | None,
    downgrade_reasons: list[str],
    consistency: dict[str, Any],
) -> dict[str, Any]:
    """``snippets=None`` (edit-plan parity fix, CEO v1.72.1 dogfood) is a distinct sentinel from
    ``[]``: it means the caller's contract has NO rendered-snippet concept at all (edit-plan
    emits no rendered source text -- see docs/harness_api.md), so the "no snippets" degrade below
    must not fire. ``[]`` keeps its existing meaning for agent -- snippets ARE part of the
    contract but none survived rendering, a genuine degrade signal. Every existing caller passes
    a real list (never ``None``), so this widening is additive and does not change agent's
    output -- see ``_capsule_confidence_and_ask_without_render``, the only ``None`` caller."""
    edit_confidence = _as_dict(_as_dict(payload.get("edit_plan_seed")).get("confidence"))
    raw_overall = edit_confidence.get("overall")
    if isinstance(raw_overall, (int, float)) and math.isfinite(float(raw_overall)):
        # CLAMP AT THE DOOR, not at the exit. `confidence.overall` is a 0..1 contract, and the
        # branch clamps below all use `min(...)` -- which cannot bound a value that is already
        # out of range in the wrong direction, and cannot bound NaN at all (`min(NaN, x)` is NaN).
        #
        # Measured on this function before the guard, with values arriving via
        # `edit_plan_seed.confidence.overall`:
        #     NaN -> nan     inf -> inf     5.0 -> 5.0
        #
        # The 5.0 case is the dangerous one, and an adversarial review that raised the NaN case
        # did not name it. Downstream logic compares this number against thresholds (`< 0.75`
        # decides `ask_user_before_editing`), so an out-of-range 5.0 does not merely look wrong --
        # it WINS every such comparison and reads as maximally certain while carrying no
        # downgrade reason to contradict it. NaN is the safer failure of the two, because NaN
        # loses every comparison instead of winning it.
        #
        # A non-finite value is treated as ABSENT rather than clamped to a number: we do not know
        # what it meant, and inventing 1.0 or 0.0 would be asserting a confidence nobody computed.
        # Falling through to the derived defaults below is the honest handling.
        overall = min(1.0, max(0.0, float(raw_overall)))
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
    if snippets is not None and not snippets:
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
    if any(isinstance(reason, str) and "primary file" in reason for reason in downgrade_reasons):
        overall = min(overall, 0.55)
    valid_reasons = [r.strip() for r in downgrade_reasons if isinstance(r, str) and r.strip()]
    deduped_reasons = list(dict.fromkeys(valid_reasons))

    # THE INVARIANT, ENFORCED AT THE SINGLE EXIT: a result that lists reasons it is degraded may
    # never also claim certainty. `ask_user_before_editing` keys off this number, so the
    # certain-but-degraded shape tells an agent "edit this, no question needed" about a target
    # derived from a scan that did not finish. Two external dogfoods reported exactly that
    # (2026-08-22 v1.111.7, again 2026-08-23 v1.113.0).
    #
    # This is a GUARD, not another branch-specific clamp, and that distinction is the fix. Of the
    # six reason-appending branches above, four clamp and two do not -- `confidence_downgraded`
    # and any reason handed in by the CALLER both fall straight through to the return with
    # `overall` untouched, which is how a caller-supplied 1.0 survives. Adding a fifth clamp would
    # close today's hole and do nothing for the seventh branch someone adds next year. A guard at
    # the exit holds for every branch that exists and every branch not written yet.
    #
    # The ceiling is deliberately the SMALLEST change that makes the statement true. Calibrating a
    # meaningful number is the job of the branch-specific clamps, which know WHY the result is
    # degraded; this guard only knows THAT it is, so it must not invent a severity it cannot
    # justify or it would silently overwrite a better-informed value.
    if deduped_reasons:
        overall = min(overall, _DEGRADED_CONFIDENCE_CEILING)

    return {"overall": round(overall, 3), "downgrade_reasons": deduped_reasons}


def _capsule_validation_evidence_ask_reason(
    validation_commands: list[str],
    suggested_validation_commands: list[dict[str, Any]],
) -> str | None:
    """Factored out of `build_agent_capsule_from_map`'s ask-reasons ladder (mechanical extraction,
    text/behavior unchanged) so `_capsule_confidence_and_ask_without_render` -- edit-plan's
    non-render counterpart -- can reuse the identical text instead of re-deriving it."""
    if validation_commands:
        return None
    if suggested_validation_commands:
        # Confidence/tie logic never sees this — the strict field stays empty and
        # `required` stays True either way; this only softens the human-facing text.
        return (
            "no validation command evidence "
            "(an unverified suggested_validation_commands entry is available)"
        )
    return "no validation command evidence"


def _capsule_low_confidence_ask_reason(confidence_overall: float) -> str | None:
    """Factored out of `build_agent_capsule_from_map`'s ask-reasons ladder alongside
    `_capsule_validation_evidence_ask_reason` above -- same rationale."""
    if confidence_overall < 0.75:
        return "confidence below 0.75"
    return None


def _capsule_confidence_and_ask_without_render(
    payload: dict[str, Any],
    *,
    query: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parity fix (CEO v1.72.1 dogfood): the non-render counterpart to the top-level
    `confidence`/`ask_user_before_editing` derivation `build_agent_capsule_from_map` computes,
    for a caller (`tg edit-plan`, via `repo_map.build_context_edit_plan_from_map`) that never
    renders source text (docs/harness_api.md's Edit Plan JSON `max_tokens` note).

    It reproduces agent's ambiguity ladder AS FAITHFULLY as the non-render payload allows, reusing
    agent's OWN helpers against the same payload agent reads -- `_primary_target`,
    `_alternative_targets`, `_prefer_implementation_over_marker_helper`, `_capsule_trust_checks`,
    `_capsule_validation_alignment`, `_tied_alternative_targets`,
    `_primary_target_is_unrequested_marker_helper`, `_confidence`, and the shared ask-reason
    helpers -- so the >=0.75 no-ask threshold, the query-language / validation-alignment
    downgrades, the scan-truncation gate, AND (Opus-gate MUST-FIX) the alternative-target-TIE
    downgrade + unrequested-marker-helper ask-reason all fire IDENTICALLY to `tg agent`.

    The tie + marker-helper ambiguity signals need only the payload's `candidate_edit_targets`
    /`file_matches` alternatives and the `query` (see `_alternative_targets`) -- NO snippets, NO
    call-site evidence -- so they MUST be computed here: omitting them let edit-plan report
    `ask_user_before_editing.required = false` on an ambiguous plan where `tg agent` returns
    `true`, a safety under-report in the unsafe (auto-edit) direction (Opus gate on c63f509).

    Only the genuinely snippet-/call-site-/LSP-evidence-gated enrichments stay agent-only, because
    edit-plan structurally lacks the evidence they corroborate against (faking them would be
    dishonest, not "the same way agent does it"):
      * the graph-corroborated + token-budget confidence UPLIFTS (need rendered snippets +
        verified call-site evidence);
      * the LSP confidence BOOST and LSP-based tie RESOLUTION (need provider-backed LSP proof);
      * the snippet-only ask-reasons (`no snippets included`, `primary file omitted from capsule
        snippets`) and the rendered-context-consistency ask-reason -- every ambiguity cause
        edit-plan CAN have already contributes its own direct ask-reason above (trust / tie /
        marker / scan / no-validation / low-confidence), so excluding these never under-reports.
    Validation-based tie RESOLUTION is KEPT (edit-plan carries `validation_plan`/commands/
    alignment), so a tie agent legitimately resolves via targeted validation evidence is resolved
    here too rather than spuriously over-flagged.

    `_confidence` is called with `snippets=None` (the "no snippet contract" sentinel -- see its
    docstring) so the snippet-absence 0.55 degrade does not fire for a contract that has no
    snippets by design. Agent's output is byte-unchanged: it never calls this helper.
    """
    scan_truncated = _capsule_scan_incomplete(payload)
    # Build target + alternatives EXACTLY as `build_agent_capsule_from_map` does (agent_capsule.py:
    # `target = _primary_target(...)` -> `_alternative_targets(...)[:4]` ->
    # `_prefer_implementation_over_marker_helper` -> `_prefer_implementation_over_cli_dispatcher_
    # helper`), so tie/marker/dispatcher detection sees the same inputs.
    target = _primary_target(payload)
    alternatives = _alternative_targets(payload, target, limit=None)[:4]
    target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
    target, alternatives = _prefer_implementation_over_cli_dispatcher_helper(target, alternatives)
    target, alternatives = _prefer_public_implementation_over_private_helper(
        query, target, alternatives
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
    suggested_validation_commands = _as_list_of_dicts(
        payload.get("suggested_validation_commands")
        or edit_plan_seed.get("suggested_validation_commands"),
    )

    consistency: dict[str, Any] = {"primary_file_included": bool(str(target.get("file") or ""))}
    trust = _capsule_trust_checks(query, target, [], validation_commands, validation_alignment)
    downgrade_reasons: list[str] = list(trust["downgrade_reasons"])
    if scan_truncated:
        downgrade_reasons.append(_CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON)
    confidence = _confidence(payload, None, downgrade_reasons, consistency)
    confidence_cap = float(trust["confidence_cap"])
    if confidence_cap < 1.0:
        confidence["overall"] = round(min(float(confidence["overall"]), confidence_cap), 3)
        _cap_primary_target_confidence(target, confidence_cap)
    _cap_alternative_target_confidences(alternatives, target)

    # Ambiguity: alternative-target TIE detection + validation-based resolution, matching agent's
    # ladder (agent_capsule.py). LSP-based resolution is intentionally excluded (needs LSP proof).
    tied_alternatives = _tied_alternative_targets(query, alternatives, target)
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
    if tied_alternatives and tie_resolved_by_validation:
        tied_alternatives = []
    if tied_alternatives:
        confidence["overall"] = round(min(float(confidence["overall"]), 0.74), 3)
        confidence["downgrade_reasons"] = _dedupe([
            *list(confidence.get("downgrade_reasons") or []),
            "alternative target confidence tie",
        ])

    # Ask-reasons in agent's order: trust, marker-helper, tie, scan, validation-evidence,
    # low-confidence (the snippet-only + rendered-context-consistency reasons are agent-only).
    ask_reasons: list[str] = list(trust["ask_reasons"])
    if _primary_target_is_unrequested_marker_helper(query, target):
        ask_reasons.append(
            "primary target is an unrequested marker-helper; confirm the intended edit target"
        )
    if tied_alternatives:
        ask_reasons.append("alternative target confidence ties primary target")
    if scan_truncated:
        ask_reasons.append(_CAPSULE_SCAN_TRUNCATED_ASK_REASON)
    validation_evidence_ask_reason = _capsule_validation_evidence_ask_reason(
        validation_commands, suggested_validation_commands
    )
    if validation_evidence_ask_reason is not None:
        ask_reasons.append(validation_evidence_ask_reason)
    low_confidence_ask_reason = _capsule_low_confidence_ask_reason(float(confidence["overall"]))
    if low_confidence_ask_reason is not None:
        ask_reasons.append(low_confidence_ask_reason)

    ask = {"required": bool(ask_reasons), "reasons": _dedupe(ask_reasons)}
    return confidence, ask


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


def _targeted_validation_corroboration_qualifies(
    targeted_validation_evidence: list[str],
    validation_alignment_status: str,
    validation_kept_count: int,
) -> bool:
    """Dogfood #84: the SECOND corroboration channel for the token-budget uplift, alongside
    verified call-site evidence. `targeted_validation_evidence` (`_targeted_validation_evidence`)
    is non-empty only for steps SCOPED to the primary (symbol/file, non-empty target,
    confidence>=0.7) -- a repo-scope fallback step (e.g. `uv run pytest -q` @ 0.55) can never
    populate it, so it can never qualify here either. The alignment check mirrors the tie-break
    use of the same evidence elsewhere in this module: "aligned", or "mismatch-filtered" with at
    least one step actually kept for the primary's language.
    """
    if not targeted_validation_evidence:
        return False
    if validation_alignment_status == "aligned":
        return True
    return validation_alignment_status == "mismatch-filtered" and validation_kept_count > 0


def _capsule_token_budget_uplift_eligible(
    *,
    query: str,
    target: dict[str, Any],
    snippets: list[dict[str, Any]],
    consistency: dict[str, Any],
    call_site_evidence: dict[str, Any],
    scan_truncated: bool,
    targeted_validation_evidence: list[str],
    validation_alignment_status: str,
    validation_kept_count: int,
) -> bool:
    """T2: eligibility for the corroborated-resolution uplift, covering BOTH the original
    capsule-own-budget primary omission (F4) and a render-truncated-only cut where the primary's
    OWN snippet is fully present (`payload["truncated"]` cut some other, lower-ranked source).

    Every genuine-ambiguity signal must disqualify this uplift: a primary the ranking never
    selected/rendered at all, an unresolved alternative-target tie, an unrequested marker-helper
    demotion, or any downgrade reason outside the render/token-budget family (language mismatch,
    validation misalignment, ...). Only a target independently corroborated by the query AND by
    EITHER verified call-site evidence OR targeted validation evidence (dogfood #84) is eligible.

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

    Dogfood #84: EVERY disqualifier above this point gates BOTH corroboration channels
    identically -- the targeted-validation channel added below is only an alternative to the
    FINAL `call_site_evidence` check, never a bypass of scan-truncation, no-snippets, genuine
    misroute, alternative-target tie, marker-helper demotion, any non-budget downgrade reason, or
    the query-overlap check. This is deliberate: a validation step matching a WRONG primary's stem
    by coincidence must not corroborate anything the query itself never named.
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
    if call_site_evidence.get("status") == "collected":
        return True
    return _targeted_validation_corroboration_qualifies(
        targeted_validation_evidence,
        validation_alignment_status,
        validation_kept_count,
    )


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
    targeted_validation_evidence: list[str],
    validation_alignment_status: str,
    validation_kept_count: int,
) -> None:
    """Uplift a render/token-budget-only confidence clamp for a CORROBORATED resolution -- never
    for a genuine misroute or a genuine ambiguity (see `_capsule_token_budget_uplift_eligible`).

    T2 generalization: this originally only covered the 0.55 primary-omission clamp (the primary
    file cut from the capsule's OWN snippet budget). It now ALSO covers the 0.72 render-truncated
    tier (`payload["truncated"]` cut some OTHER, lower-ranked source, not the primary) -- both are
    render/token-budget artifacts, not resolution-quality signals, so both are eligible for the
    same corroborated-resolution relief up to `_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP`.

    Dogfood #84: eligibility is now shared by TWO corroboration channels -- verified call-site
    evidence (the original, strongest signal) and targeted validation evidence (a scoped pytest/
    jest/etc step that actually names the primary as its target). They are NOT treated as equally
    strong: the call-site channel keeps the higher `_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP`
    (0.8), while a targeted-validation-only corroboration is capped at the lower, historical
    `_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP` (0.75) -- still enough to clear the >=0.75
    no-ask threshold, but deliberately short of the graph-corroborated ceiling. Each channel also
    gets its own channel-distinct downgrade-reason text for telemetry.

    STRUCTURAL note: this must run AFTER `_collect_capsule_call_site_evidence` (agent_capsule.py
    call order), since verified caller evidence -- the corroboration this uplift depends on --
    isn't available until that call returns. It mutates `confidence`, `target`, `alternatives`,
    `consistency`, and `ask_reasons` in place so `build_agent_capsule`'s already-assembled payload
    reflects the uplift without re-deriving `ask_user_before_editing` from scratch.
    """
    current_overall = float(confidence.get("overall", 0.0))
    max_possible_uplift_cap = _CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP
    if current_overall >= max_possible_uplift_cap:
        return
    if not _capsule_token_budget_uplift_eligible(
        query=query,
        target=target,
        snippets=snippets,
        consistency=consistency,
        call_site_evidence=call_site_evidence,
        scan_truncated=scan_truncated,
        targeted_validation_evidence=targeted_validation_evidence,
        validation_alignment_status=validation_alignment_status,
        validation_kept_count=validation_kept_count,
    ):
        return
    call_site_corroborated = call_site_evidence.get("status") == "collected"
    if call_site_corroborated:
        uplift_cap = _CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP
        channel_reason = (
            "token budget limited rendering only; confidence reflects graph-corroborated resolution"
        )
    else:
        uplift_cap = _CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP
        channel_reason = (
            "token budget limited rendering only; confidence reflects validation-corroborated "
            "resolution"
        )
    uplifted = round(min(uplift_cap, confidence_cap), 3)
    if uplifted <= current_overall:
        return
    confidence["overall"] = uplifted
    remaining_reasons = [
        reason
        for reason in confidence.get("downgrade_reasons", [])
        if reason not in _CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS
    ]
    remaining_reasons.append(channel_reason)
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
