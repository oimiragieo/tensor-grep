from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_call_sites import (
    _collect_capsule_call_site_evidence as _collect_capsule_call_site_evidence,
)
from tensor_grep.cli.agent_capsule_call_sites import (
    _collect_capsule_call_site_evidence_from_map as _collect_capsule_call_site_evidence_from_map,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _apply_capsule_token_budget_confidence_uplift as _apply_capsule_token_budget_confidence_uplift,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_context_consistency as _capsule_context_consistency,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_low_confidence_ask_reason as _capsule_low_confidence_ask_reason,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_scan_incomplete as _capsule_scan_incomplete,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_validation_evidence_ask_reason as _capsule_validation_evidence_ask_reason,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _confidence as _confidence,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _follow_up_reads as _follow_up_reads,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_BASIS as _BEST_EFFORT_PRIMARY_BASIS,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_EVIDENCE as _BEST_EFFORT_PRIMARY_EVIDENCE,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE as _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_CAP as _CAPSULE_LSP_CONFIDENCE_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_LANGUAGES as _CAPSULE_LSP_CONFIDENCE_LANGUAGES,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_ASK_REASON as _CAPSULE_SCAN_TRUNCATED_ASK_REASON,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON as _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _alternative_targets as _alternative_targets,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _command_ref as _command_ref,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _build_snippets as _build_snippets,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _raw_context_ref as _raw_context_ref,
)
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
    _best_effort_primary_target_from_map as _best_effort_primary_target_from_map,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_alternative_target_confidences as _cap_alternative_target_confidences,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_primary_target_confidence as _cap_primary_target_confidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_lsp_confidence_boost_enabled as _capsule_lsp_confidence_boost_enabled,
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
    _lsp_tie_resolution_evidence as _lsp_tie_resolution_evidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _numeric_confidence as _numeric_confidence,
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
    _suggested_scope_from_tied_targets as _suggested_scope_from_tied_targets,
)
from tensor_grep.cli.agent_capsule_targets import (
    _target_has_lsp_confidence_proof as _target_has_lsp_confidence_proof,
)
from tensor_grep.cli.agent_capsule_targets import (
    _target_lsp_boost_language as _target_lsp_boost_language,
)
from tensor_grep.cli.agent_capsule_targets import (
    _targeted_validation_evidence as _targeted_validation_evidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _tied_alternative_targets as _tied_alternative_targets,
)


def build_agent_capsule_from_map(
    rm: dict[str, Any],
    query: str,
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
    deadline_monotonic: float | None = None,
    _rescue_call_site_evidence: bool = False,
) -> dict[str, Any]:
    """Task #108 (Tier-2 daemon moat): the map-based core of ``build_agent_capsule``, taking an
    already-built ``rm`` (e.g. the warm session daemon's cached ``repo_map``) instead of scanning
    the filesystem itself. The RANKING (context-render) + suggested-scope sub-steps here read only
    ``rm``, so they are byte-identical cold-vs-warm for the same map.

    ``_rescue_call_site_evidence`` (the ONE sub-step that MUST differ cold-vs-warm, Opus-gate
    FIX-FIRST):
      * ``True`` -- the COLD (direct ``build_agent_capsule``) path. Collect call-site evidence
        through the RESCUE-equipped ``_collect_capsule_call_site_evidence`` -> the FS-backed
        ``build_symbol_blast_radius`` wrapper, which on a truncated no_match literal-seed-recovers
        an out-of-window symbol def (``repo_map._literal_symbol_seed_files`` -> rebuild w/
        ``extra_files`` -> re-run) and thus the in-window callers. This is a SECOND repo scan, but
        it is the pre-PR ``main`` behavior and is what keeps recall from regressing on a repo
        larger than the ``--max-repo-files`` cap. Never stamps the ``daemon_evidence_unreliable``
        sentinel (the rescue already ran here).
      * ``False`` (default) -- the WARM/DAEMON (``session_store._serve_session_request_from_
        payload``) path. Collect through the RESCUE-LESS
        ``_collect_capsule_call_site_evidence_from_map``, which resolves against the ONE cached
        ``rm`` (no second FS scan -- the daemon-moat win) and CANNOT rescue an out-of-window def.
        On that exact untrustworthy no_match it flags ``daemon_evidence_unreliable``, which the
        client (``main._maybe_agent_via_running_daemon``) treats like a transport error and falls
        back to the cold path above -- which DOES have the rescue.

    ``include_blast_radius``/``gpu_device_ids``/``gpu_timeout_s`` are accepted for full parity
    with ``build_agent_capsule``'s signature (a direct caller may still want them), but the
    session daemon's own dispatch never forwards a non-default ``gpu_device_ids`` -- the client
    wrapper refuses to route a ``--gpu-device-ids`` request to the daemon at all, because the GPU
    evidence probe shells out to a fresh ``tg search`` subprocess (``_agent_gpu_evidence`` below)
    that must never run inside a long-lived daemon worker thread.
    """
    # Local import avoids a module-level circular import (orient_capsule imports repo_map, which
    # this module also imports) -- same discipline repo_map.build_context_render's own reuse of
    # this helper uses, and the same reasoning as the _suggested_scope_from_map import below.
    # Wave-4 file-size split (#file_size_budget): this function moved out of the
    # agent_capsule.py facade, but 3 of its bare calls target names tests monkeypatch
    # directly on the facade module (`_collect_outbound_dependencies`,
    # `_apply_inline_caller_annotation`, `_agent_gpu_evidence`) -- a bare reference from
    # this module's own globals would silently keep calling the ORIGINAL unpatched
    # function after a test patches the facade attribute. Qualified late lookup through
    # the facade module object (not `from ... import X` at module load time) makes every
    # call re-read the current facade attribute, so a monkeypatch on
    # `agent_capsule.<name>` is observed here exactly as it would be if this function had
    # stayed physically in the facade.
    from tensor_grep.cli import agent_capsule
    from tensor_grep.cli.orient_capsule import (
        _apply_ignore_globs,
        _detect_vendored_subtrees,
        _detect_workspace_root,
    )

    rm = _apply_ignore_globs(rm, ignore)
    # Cold-path assembly-tail SLA fix (#220): the top tail consumer profiled on a 25k-file/
    # 40-sibling-project synthetic tree -- 1.2-3.6s PER CALL depending on manifest-directory
    # density, called from two places (here AND `repo_map._build_context_pack_from_map`'s own
    # `auto_deweight` pass inside the `build_context_render_from_map` call just below), so it can
    # burn multiple seconds of wall-clock AFTER the shared --deadline budget has already been
    # exhausted by the repo-map scan above -- OR, since its own internal loops are each
    # independently expensive, blow the budget in a single uninterrupted call even when it hadn't
    # been exhausted YET at entry. `deadline_hit` (not just a pre-call time check) catches BOTH
    # shapes uniformly, since `_detect_vendored_subtrees` sets it on its own entry-skip AND on
    # either of its two internal per-iteration breaks -- surfaced in `deadline_limit.assembly_
    # stages_skipped` below (see that block's comment).
    skipped_assembly_stages: list[str] = []
    detect_vendored_deadline_hit = repo_map._DeadlineBreakFlag()
    # #179: compute the SAME auto-detected vendor/skill/tool-config tree set ONCE, here, against the
    # final (already `--ignore`-filtered) `rm`, and thread it through every `_suggested_scope_from_map`
    # call below plus the `suggested_ignore` build near the end of this function. Previously each
    # `_suggested_scope_from_map(rm)` call below ran with no exclusion set at all (defaulting to
    # "nothing excluded"), while `suggested_ignore` independently re-ran `_detect_vendored_subtrees(rm)`
    # for the SAME `rm` -- so `suggested_scope` could point an agent straight at a tree `suggested_
    # ignore` already flagged in the very same capsule (#179, the tg-agent/context-render sibling of
    # orient's #168/#606 fix). Sourcing both fields from one shared call makes them provably
    # consistent, not just coincidentally so, and also drops a redundant second detection pass.
    deweighted_trees = _detect_vendored_subtrees(
        rm, deadline_monotonic=deadline_monotonic, deadline_hit=detect_vendored_deadline_hit
    )
    if detect_vendored_deadline_hit.hit:
        skipped_assembly_stages.append("vendored_subtree_detection")
    # CEO #2 auto-narrow (advisory, additive): the SAME multi-project-workspace-root detection
    # `tg orient` uses (see `orient_capsule._detect_workspace_root`'s docstring) -- computed once,
    # here, so both the scan-limit-truncation `suggested_scope` gate below and the final result
    # assembly (near this function's return) can read it without a second call.
    workspace_root_detected = _detect_workspace_root(rm, deadline_monotonic=deadline_monotonic)
    resolved_path = str(rm["path"])
    requested_semantic_provider = semantic_provider
    effective_semantic_provider = (
        "hybrid"
        if semantic_provider == "native" and _capsule_lsp_confidence_boost_enabled()
        else semantic_provider
    )
    # #222 (call-2 enumeration-gap fix, Opus-gate N4 nit on #669/#220): `build_context_render_
    # from_map` -> `build_context_pack_from_map` -> `_build_context_pack_from_map` runs its OWN
    # SECOND `_detect_vendored_subtrees` call (repo_map.py's `auto_deweight` pass) plus the
    # symbol-scoring and pagerank sibling loops, all sharing ONE internal `_DeadlineBreakFlag`
    # that -- before this fix -- was never passed in from here, so `build_context_pack_from_map`
    # always minted its own throwaway flag (see that function's "dogfood finding 1" comment) and
    # this capsule could only ever observe the RESULT (`payload["partial"]`), never WHICH stage
    # actually cut short. A dedicated flag here (deliberately NOT reusing
    # `detect_vendored_deadline_hit` above, which is call-1-specific and would mislabel a
    # symbol-scoring/pagerank trip as "vendored_subtree_detection") makes that whole render call
    # observable as one honestly-named unit.
    context_pack_deadline_hit = repo_map._DeadlineBreakFlag()
    payload = repo_map.build_context_render_from_map(
        rm,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        model=model,
        optimize_context=True,
        render_profile="full",
        semantic_provider=effective_semantic_provider,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
    )
    if context_pack_deadline_hit.hit:
        # Deliberately a DIFFERENT name than "vendored_subtree_detection" above: this flag covers
        # the WHOLE render call's internal sibling stages (symbol-scoring, pagerank, AND the
        # second _detect_vendored_subtrees call), not vendored-detection specifically -- naming it
        # generically here is honest about what is actually distinguishable from this signal
        # alone, rather than overclaiming precise attribution to one sub-stage.
        skipped_assembly_stages.append("context_pack_assembly")
    # TRAP B (task #108 design review): `suggested_scope` is computed in the WRAPPER
    # (`build_context_render`, repo_map.py) via `include_suggested_scope=True`, NOT inside
    # `build_context_render_from_map` -- replicate that exact block here (same gate, same
    # helper) against OUR OWN `rm`, or a warm capsule would silently drop `suggested_scope` on a
    # truncated scan. Mirrors repo_map.build_context_render's own comment/logic verbatim.
    #
    # CEO #2 auto-narrow (advisory, additive): OR in `workspace_root_detected` as a SECOND,
    # independent trigger -- a genuine multi-project workspace root gets the same proactive
    # suggested_scope narrowing even when the scan itself completed without truncating (see
    # `orient_capsule._detect_workspace_root`'s docstring). The scan-limit-truncation trigger
    # above is unchanged; this only widens when the block below can also run.
    scan_limit_for_suggested_scope = rm.get("scan_limit")
    if (
        isinstance(scan_limit_for_suggested_scope, dict)
        and scan_limit_for_suggested_scope.get("possibly_truncated")
    ) or workspace_root_detected:
        from tensor_grep.cli.orient_capsule import _suggested_scope_from_map

        # Cold-path assembly-tail SLA fix (#220): second-largest profiled tail consumer (a
        # whole-repo centrality rollup). Same skip-tracking shape as vendored_subtree_detection
        # above -- see the `deadline_limit.assembly_stages_skipped` block near this function's
        # return for why a pre-call check (not just the callee's own internal one) is needed here.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            skipped_assembly_stages.append("suggested_scope")
        suggested_scope_from_map = _suggested_scope_from_map(
            rm, deweighted_trees=deweighted_trees, deadline_monotonic=deadline_monotonic
        )
        if suggested_scope_from_map is not None:
            payload["suggested_scope"] = suggested_scope_from_map
    # PR-1 (1D): whether the underlying repo scan itself (not the capsule's own snippet/token
    # output budget) was truncated -- gates the exit-2-on-scan-truncation contract below and
    # disqualifies the T2 corroborated-resolution confidence uplift (a capped-scan primary may
    # simply be the best candidate among the files that were visible, not the true best one).
    scan_truncated = _capsule_scan_incomplete(payload)
    target = _primary_target(payload)
    # v20 dogfood gap #2: a truncated scan with no resolvable primary previously left `target` at
    # `_primary_target`'s empty-shape default (`file: ""`) -- safe, but useless to an agent even
    # though `rm` already holds every file/symbol the scan reached before the deadline cut it off.
    # Substitute a best-effort candidate derived straight from that already-scanned data (no
    # second scan) and flag it clearly non-authoritative via the additive `partial_primary`/
    # `primary_basis` fields. Every confidence cap / ask-reason gate below -- the scan-truncation
    # downgrade just above, `_cap_primary_target_confidence` a few lines down, the forced
    # `_CAPSULE_SCAN_TRUNCATED_ASK_REASON` ask-reason, and the T2 uplift's own unconditional
    # `scan_truncated` disqualifier -- still runs unmodified over this substitute exactly as it
    # would over a normally-ranked primary, so it can never surface at >=0.75 confidence or with
    # `ask_user_before_editing.required = False`. A COMPLETE (non-truncated) scan, or a truncated
    # one that still resolved a normal primary, never enters this block -- byte-identical to
    # before this fix.
    if scan_truncated and not target.get("file"):
        best_effort_target = _best_effort_primary_target_from_map(rm, query)
        if best_effort_target is not None:
            target["file"] = best_effort_target["file"]
            target["symbol"] = best_effort_target["symbol"]
            target["kind"] = best_effort_target["kind"]
            target["line"] = best_effort_target["line"]
            target["partial_primary"] = True
            target["primary_basis"] = _BEST_EFFORT_PRIMARY_BASIS
            target["evidence"] = _dedupe([
                *[
                    str(item)
                    for item in target.get("evidence", [])
                    if item is not None and str(item)
                ],
                _BEST_EFFORT_PRIMARY_EVIDENCE,
            ])
    # NIT-2 (Opus gate): `partial_primary`/`primary_basis` live on `primary_target` ONLY -- `edit_
    # order` and `rollback` below still carry this same best-effort `target["file"]` WITHOUT the
    # flag. That is intentionally safe, not an oversight: both are advisory (a suggested edit
    # order / a recommended checkpoint command), never an auto-apply, and `ask_user_before_editing.
    # required` is forced True by the scan-truncated ask-reason below regardless, so nothing reads
    # those two fields as a green light to act without a human first seeing the low-confidence,
    # flagged primary_target.
    all_alternatives = _alternative_targets(payload, target, limit=None)
    alternatives = all_alternatives[:4]
    target, alternatives = _prefer_implementation_over_marker_helper(query, target, alternatives)
    target, alternatives = _prefer_implementation_over_cli_dispatcher_helper(target, alternatives)
    target, alternatives = _prefer_public_implementation_over_private_helper(
        query, target, alternatives
    )

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
    # Dogfood fix: a genuine confirmation-tie on a big/ambiguous repo previously left
    # `suggested_scope` null -- it was populated ONLY on a scan-LIMIT truncation (the block right
    # after `payload = repo_map.build_context_render_from_map(...)` above), never on a tie. That
    # was a dead end for the caller: 0 actionable validation commands AND no hint to narrow and
    # re-run scoped. Additive-only: never runs when `suggested_scope` is already populated (e.g. by
    # the scan-truncation path above -- that hint wins), never touches confidence, tie detection,
    # or validation commands. Prefer the same centrality-weighted rollup `tg orient` uses; when that
    # whole-repo signal is flat/tied (the common shape on the very repo that produced this tie), fall
    # back to the tied candidates' own common parent directory -- never a guess (see
    # `_suggested_scope_from_tied_targets`'s None cases, including "never suggest the root").
    if ambiguity.get("requires_confirmation") and not payload.get("suggested_scope"):
        from tensor_grep.cli.orient_capsule import _suggested_scope_from_map

        # Cold-path assembly-tail SLA fix (#220): same skip-tracking as the scan-truncation
        # `suggested_scope` block above -- reuses the "suggested_scope" tag (deduped below) since
        # it is the same underlying rollup. A deadline-skip here (returns None) falls through to
        # the cheap `_suggested_scope_from_tied_targets` fallback just below unchanged -- that
        # fallback was ALREADY the "no clear winner" path, so this adds no new branch.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            skipped_assembly_stages.append("suggested_scope")
        tie_suggested_scope = _suggested_scope_from_map(
            rm, deweighted_trees=deweighted_trees, deadline_monotonic=deadline_monotonic
        )
        if tie_suggested_scope is None:
            # `tied_alternatives` (not `ambiguity["tied_alternative_targets"]`) is the definitive
            # source: `requires_confirmation` is only ever True from the `if tied_alternatives:`
            # branch above, which stamped `ambiguity["tied_alternative_targets"] = tied_alternatives`
            # verbatim -- reading the local keeps this well-typed (`ambiguity` is a dict literal
            # union across its branches, so `.get(...)` widens to `object` for mypy).
            tie_suggested_scope = _suggested_scope_from_tied_targets(
                Path(resolved_path),
                target,
                tied_alternatives,
            )
        if tie_suggested_scope is not None:
            payload["suggested_scope"] = tie_suggested_scope
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
    # Mechanical extraction (CEO v1.72.1 dogfood, edit-plan confidence/ask parity): these two
    # checks now live in `_capsule_validation_evidence_ask_reason` / `_capsule_low_confidence_ask_
    # reason` so `_capsule_confidence_and_ask_without_render` (edit-plan's non-render counterpart)
    # can reuse the identical text/thresholds instead of re-deriving them -- text, order, and
    # behavior here are unchanged; see those functions' docstrings.
    validation_evidence_ask_reason = _capsule_validation_evidence_ask_reason(
        validation_commands, suggested_validation_commands
    )
    if validation_evidence_ask_reason is not None:
        ask_reasons.append(validation_evidence_ask_reason)
    if not snippets:
        ask_reasons.append("no snippets included")
    low_confidence_ask_reason = _capsule_low_confidence_ask_reason(float(confidence["overall"]))
    if low_confidence_ask_reason is not None:
        ask_reasons.append(low_confidence_ask_reason)
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
    if _rescue_call_site_evidence:
        # COLD path (Opus-gate FIX-FIRST): recover out-of-window callers via the RESCUE-equipped
        # collector (a second FS-backed build_symbol_blast_radius scan that literal-seed-rescues a
        # truncated no_match), exactly like pre-PR main. resolved_path is str(rm["path"]);
        # max_repo_files is the caller's cap so the rescue scan uses the same window as ranking.
        # The sentinel below CANNOT arise on this path (the rescue already ran) -- hardcode False.
        related_call_sites, call_site_evidence = _collect_capsule_call_site_evidence(
            query,
            resolved_path,
            target,
            include_blast_radius=include_blast_radius,
            max_files=max_files,
            max_repo_files=max_repo_files,
            seed_confidence=primary_target_seed_confidence,
            deadline_monotonic=deadline_monotonic,
        )
        call_site_evidence_daemon_unreliable = False
    else:
        # WARM/DAEMON path: rescue-less _from_map collector (single cached map, no second scan);
        # on a truncated no_match it flags daemon_unreliable so the client falls back to cold.
        # SLA-honesty fix (+10% campaign #5): thread the SAME in-scope deadline_monotonic this
        # function's other sibling stages already receive (cold branch above, DAR outbound deps
        # below) -- previously dropped here, leaving this branch's build_symbol_blast_radius_from_
        # map call structurally unbounded even under an explicit --deadline.
        related_call_sites, call_site_evidence, call_site_evidence_daemon_unreliable = (
            _collect_capsule_call_site_evidence_from_map(
                query,
                rm,
                target,
                include_blast_radius=include_blast_radius,
                max_files=max_files,
                seed_confidence=primary_target_seed_confidence,
                deadline_monotonic=deadline_monotonic,
            )
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
        targeted_validation_evidence=targeted_validation_evidence,
        validation_alignment_status=validation_alignment_status,
        validation_kept_count=validation_kept_count,
    )
    # NIT-1 (Opus gate, structural hardening): runs LAST -- after every existing confidence
    # mutation in this function, including the T2 uplift immediately above, which is the ONLY
    # place `confidence["overall"]` can be RAISED (via direct assignment) rather than merely
    # clamped. Today a best-effort primary happens to land at confidence 0.55 EMERGENTLY, purely
    # because the empty upstream primary forces `_confidence`'s existing downgrade ladder (empty
    # snippets / primary-omitted-from-snippets); that chain relies on the T2 uplift's own
    # `scan_truncated` disqualifier (`_capsule_token_budget_uplift_eligible`) continuing to hold,
    # which is correct today but not a promise this function makes elsewhere. `partial_primary`
    # (set only in the guarded best-effort block above) forces BOTH `confidence["overall"]` and
    # `target["confidence"]` down to `_BEST_EFFORT_PRIMARY_MAX_CONFIDENCE` regardless of what every
    # earlier stage computed, so "partial_primary implies confidence.overall <= 0.55 AND primary_
    # target.confidence <= 0.55" holds BY CONSTRUCTION, independent of upstream. `min(...)` only ever
    # LOWERS an existing value -- this can never raise a confidence some other gate pushed lower,
    # and it is a no-op (byte-identical) whenever `partial_primary` is unset.
    if target.get("partial_primary"):
        confidence["overall"] = round(
            min(float(confidence["overall"]), _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE), 3
        )
        _cap_primary_target_confidence(target, _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE)
    # H4 audit (1A, final reconciliation): `primary_target.confidence` must never exceed the
    # FINAL `confidence.overall` AFTER every ladder AND the corroborated token-budget lift have
    # run. The lift lowers a high-seeded target to `uplifted` via `_cap_primary_target_confidence`
    # above, and every other gate here lowers -- so this closing pass covers the DOWNWARD-only
    # ladders (empty-snippets / primary-omitted / token-budget) that lower `confidence.overall`
    # without touching the target, which is the H4 "confident false zero" (a weak lexical hit
    # still reported a 0.9 target confidence). Runs LAST so it never blocks a legitimate raise;
    # `_cap_primary_target_confidence` is min-only and never raises.
    _cap_primary_target_confidence(target, float(confidence["overall"]))
    # DAR (arxiv steal #4): runs AFTER call-site collection so it can dedupe against
    # `related_call_sites`, and deliberately does NOT touch `target`/`confidence`/`consistency`/
    # `ask_reasons` -- see `_collect_outbound_dependencies`'s fail-safe + budget-isolation
    # contract. Never mutates confidence/consistency/trust state (1A owns those).
    # dogfood finding 1 / council must-fix #5+#2: share the SAME deadline_monotonic this
    # function's other post-map stages already use, and fold an early bail into the capsule's
    # own `result["partial"]` below -- mirrors the callers/impact/blast-radius N-way fold-in
    # pattern (repo_map.py), scoped here to this capsule's own sibling stages.
    outbound_dependencies_deadline_hit = repo_map._DeadlineBreakFlag()
    outbound_dependencies, outbound_dependency_evidence = (
        agent_capsule._collect_outbound_dependencies(
            query,
            resolved_path,
            target,
            payload,
            snippets,
            related_call_sites,
            max_files=max_files,
            preview_token_budget=outbound_dependency_preview_budget,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=outbound_dependencies_deadline_hit,
        )
    )
    # CodeAnchor inline structural annotation (arXiv 2606.26979): MUST run after DAR above, not
    # before -- see `_apply_inline_caller_annotation`'s ordering-contract docstring. Mutates the
    # primary snippet only, and only when `TG_CAPSULE_INLINE_CALLERS` opts in.
    agent_capsule._apply_inline_caller_annotation(
        snippets,
        target,
        call_site_evidence,
        related_call_sites,
        rm,
        max_tokens=max_tokens,
        used_tokens=used_tokens,
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
    gpu_acceleration = agent_capsule._agent_gpu_evidence(
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
    # dogfood finding 1 / council must-fix #2: fold DAR's own deadline break in alongside the
    # inner context-render's -- either one broke on --deadline makes this capsule partial, same
    # "any one of N sibling stages" fold-in the callers/impact/blast-radius seams already use.
    # #639 Opus-gate nit 1 (dogfood #1 RESIDUAL): that fold-in only named the sibling stages it
    # explicitly threaded a deadline-break flag through -- the call-site-evidence rescue scan's
    # OWN partial signal (now propagated onto `call_site_evidence` above) was silently dropped,
    # and nothing re-checked the shared wall-clock budget one FINAL time before this capsule
    # returns. Add both: `call_site_evidence.get("partial")` as a third named sibling source, and
    # an absolute-deadline recheck as the honesty BACKSTOP -- if the budget has been blown by the
    # time we reach this return, regardless of which stage (checkpointed or not) actually
    # consumed the time, this capsule must never silently report exit 0 / partial-not-True (the
    # exact silent lie dogfood #1 originally flagged). Mirrors codemap.py's own `tail_deadline_hit`
    # catch-all for the same class of gap.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )
    if (
        payload.get("partial")
        or outbound_dependencies_deadline_hit.hit
        # #220: a fourth named sibling source -- `_detect_vendored_subtrees` broke early (entry
        # skip or a mid-loop break) even though `time.monotonic()` is a strictly-increasing clock
        # so `deadline_exceeded_at_return` below would already catch this too; named explicitly
        # anyway for the same defense-in-depth reason `call_site_evidence.get("partial")` was.
        or detect_vendored_deadline_hit.hit
        or call_site_evidence.get("partial")
        or deadline_exceeded_at_return
    ):
        result["partial"] = True
        result["partial_reason"] = "deadline"
        deadline_limit = payload.get("deadline_limit") or call_site_evidence.get("deadline_limit")
        result["deadline_limit"] = (
            dict(deadline_limit)
            if isinstance(deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
        # Cold-path assembly-tail SLA fix (#220): additive observability for the post-deadline
        # ASSEMBLY stages this fix bounds (vendored_subtree_detection, suggested_scope,
        # context_pack_assembly -- the last added by #222) -- distinct
        # from `scan_limit`/`deadline_limit.files_scanned` above, which describe the COLLECTION
        # (repo-map walk/parse) stage only. Only stamped when at least one assembly stage actually
        # skipped work, so a capsule with no assembly-tail impact stays byte-identical to before
        # this fix -- and always nested under the SAME `deadline_limit` dict that already carries
        # `deadline_exceeded`, never a standalone top-level key implying honesty independent of it.
        if skipped_assembly_stages:
            result["deadline_limit"]["assembly_stages_skipped"] = _dedupe(skipped_assembly_stages)
    if scan_truncated:
        result["result_incomplete"] = True
    # suggested_scope (#133 dogfood): the same centrality-weighted directory narrowing `tg orient`
    # offers, carried onto the agent capsule from the inner render (`build_context_render` computed
    # it from the raw map it already built, gated on scan truncation -- NO second scan). Additive +
    # conditional (same shape as scan_limit/result_incomplete above): present only when the scan was
    # truncated AND a clear winner exists, so a complete-scan capsule stays byte-identical.
    suggested_scope = payload.get("suggested_scope")
    if suggested_scope:
        result["suggested_scope"] = suggested_scope
    # CEO #2 auto-narrow (advisory, additive): present only when the scanned root itself looks
    # like a multi-project workspace parent -- absent (never `False`) otherwise, so a non-
    # workspace repo's capsule stays byte-identical to before this field existed (mirrors
    # `suggested_scope`'s/`suggested_ignore`'s own additive-conditional convention).
    if workspace_root_detected:
        result["workspace_root_detected"] = True
    # suggested_ignore (M2): parity with `tg orient`, which already surfaces auto-deweighted vendor/
    # skill/tool-config subtree roots as ready-to-paste `--ignore` globs. `tg agent` already runs
    # the SAME de-weight during ranking (`_build_context_pack_from_map`'s own `auto_deweight` pass,
    # repo_map.py) but, pre-M2, never surfaced the glob hint itself -- an agent had to hand-derive
    # `--ignore` globs or fall back to `tg orient` first. Reuse the `deweighted_trees` set already
    # computed once, near the top of this function (#179), against the SAME (already
    # `--ignore`-filtered) `rm` the ranking pass used above, and feed it into orient's exact
    # glob-builder (`_suggested_ignore_from_deweighted_trees`) -- never a second, independently
    # hand-rolled hint that could drift from what `tg orient` would say for the same repo, and never
    # a second `_detect_vendored_subtrees` walk over the same `rm` (#179 dedupes what was previously
    # two independent calls into one shared result, which is also what makes `suggested_scope` above
    # provably consistent with this hint rather than coincidentally so). Additive + conditional, same
    # shape as `suggested_scope` above: present only when non-empty, so a capsule with nothing
    # deweighted stays byte-identical to a pre-M2 build.
    from tensor_grep.cli.orient_capsule import _suggested_ignore_from_deweighted_trees

    suggested_ignore = _suggested_ignore_from_deweighted_trees(deweighted_trees)
    if suggested_ignore:
        result["suggested_ignore"] = suggested_ignore
    # DAR: additive CONDITIONAL keys, same pattern as scan_limit/partial above -- zero deps (or
    # the kill-switch, or a fail-safe early return inside `_collect_outbound_dependencies`) means
    # `outbound_dependencies` is `[]`, and BOTH keys are omitted so the capsule stays
    # byte-identical to a pre-DAR build. Never stamp an empty-but-present key.
    if outbound_dependencies:
        result["outbound_dependencies"] = outbound_dependencies
        result["outbound_dependency_evidence"] = outbound_dependency_evidence
    # TRAP A (task #108, the audit #107 divergence class): the WARM/DAEMON call-site-evidence
    # collector above (_from_map, taken only when _rescue_call_site_evidence is False) hit a
    # no_match it cannot trust (possibly-truncated map, no literal-seed rescue available on that
    # path). Stamp an internal-only sentinel the daemon client wrapper
    # (main._maybe_agent_via_running_daemon) checks to discard this WHOLE response and fall back to
    # cold, which DOES run the rescue. This is structurally impossible on the COLD path:
    # _rescue_call_site_evidence=True there hardcodes call_site_evidence_daemon_unreliable=False
    # above (the rescue-equipped collector returns no such flag), so a direct-cold capsule -- and
    # a warm-fell-to-cold capsule -- NEVER carries this key. Additive + conditional, same pattern
    # as scan_limit/suggested_scope above, so a reliable capsule stays byte-identical.
    if call_site_evidence_daemon_unreliable:
        result["daemon_evidence_unreliable"] = True
    return result
