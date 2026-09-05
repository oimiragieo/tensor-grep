"""`tg prepare` payload/capsule composition and blast-radius floor builder.

Extracted verbatim from `cli/main.py` (Task 6 Step 0, docs/plans/2026-08-02-backlog-closeout-
implementation-plan.md): a refactoring-only move of `_build_prepare_blast_radius_floor` and
`_build_prepare_payload` so that a later task can extend `tg prepare` with edit-verification
without growing `main.py` further. No behavior changes -- the function bodies below are
byte-identical to their prior location in `main.py`. This module must never import
`tensor_grep.cli.main` back (that would reintroduce the cycle this extraction exists to avoid);
`main.py` imports these two names from here instead of defining them.

Task 8 Step 2 (docs/plans/2026-08-02-backlog-closeout-implementation-plan.md, Python-only slice,
2026-08-05): this session adds ONLY the strict typed composition API named in that step --
`PrepareSnapshotV1` / `build_prepare_snapshot` below -- as a pure, additive wrapper over the
existing `_build_prepare_payload`. It changes ZERO legacy behavior: `tg prepare`'s Typer adapter
in `main.py` still calls `_build_prepare_payload` directly and is untouched by this addition, so
its output stays byte-identical by construction (nothing on that call path changed). The full
`tg edit-ready` command (frozen argv, `EditReadyTicketV1`, the claims-only OS fence, atomic
no-clobber baseline publication, and the native/Rust half) is explicitly OUT OF SCOPE for this
slice -- see the PR description for the scope-down rationale. `build_prepare_snapshot` exists so
a *future* edit-ready composition can consume a stable typed projection of the same data this
module already produces, without that future work needing to re-parse `_build_prepare_payload`'s
untyped dict shape by hand.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _build_prepare_blast_radius_floor(
    *,
    path: str,
    rm: dict[str, Any],
    target: dict[str, Any],
    call_site_evidence: dict[str, Any],
    related_call_sites: list[dict[str, Any]],
    deadline_monotonic: float | None,
) -> tuple[dict[str, Any], bool]:
    """Blast-radius floor keyed on the capsule's SELECTED ``primary_target.symbol`` (CEO #5).

    ``_collect_capsule_call_site_evidence`` (agent_capsule.py:514) only collects call-site
    evidence when the query names the primary symbol AND its pre-cap confidence is >=0.75
    (agent_capsule.py:536,546) -- a natural-language task query fails that gate and leaves
    ``related_call_sites`` EMPTY even though the SELECTED primary target may have real callers.
    Reuse the capsule's own scan when it already ran (``status`` ``collected``/
    ``collected_no_call_sites``); otherwise fall to a supplementary blast-radius scan keyed on
    the symbol the capsule actually selected, not the raw query text.

    opt10 campaign ranked-queue item #2: that supplementary scan used to ALWAYS pay a second
    FS-backed ``build_repo_map`` walk+parse (via ``build_symbol_blast_radius``) even though
    ``_build_prepare_payload`` already built one moments earlier and now passes it in as ``rm``
    -- doubling prepare's dominant cost on every natural-language query (the common CUJ). Reuse
    ``rm`` via the map-reusing sibling ``build_symbol_blast_radius_from_map`` UNLESS ``rm``'s own
    truncation signal (``rm['scan_limit']['possibly_truncated']``, repo_map.py:6707-6715) says
    the capsule's ``DEFAULT_AGENT_REPO_MAP_LIMIT``-capped map may be missing files a full scan
    would find. THE LOAD-BEARING GUARD: the supplementary scan below is DELIBERATELY uncapped
    (``max_repo_files`` omitted, see that branch's own comment) for large-repo recall, while
    ``rm`` is capped at 2000 files -- blindly reusing a truncated ``rm`` would silently narrow
    blast-radius recall on a >2000-file repo, a real correctness regression, not just a missed
    speedup. When ``rm`` is complete (any repo at or under the cap, the common case), it already
    IS the same file universe an uncapped rescan would produce, so reuse is recall-identical, not
    just faster -- never stamps the rescue-less daemon ``TRAP A`` sentinel (see
    ``_collect_capsule_call_site_evidence_from_map``'s docstring) because a no_match on a
    ``possibly_truncated`` map is impossible on this branch by construction of the gate above it.

    Returns ``(floor, deadline_partial)``. ``deadline_partial`` is deliberately narrower than the
    floor's own ``possibly_incomplete`` field: it is True ONLY when a ``--deadline`` cutoff
    truncated the scan (folded into the caller's top-level ``partial``/exit-2 honesty gate,
    mirroring ``capsule.get("partial")``), never for a plain ``max_callers`` OUTPUT cap -- an
    output cap is a COMPLETE analysis capped only for display and must stay exit 0
    (``_scan_incomplete``'s own documented contract).
    """
    from tensor_grep.cli.agent_capsule import _related_call_site_record
    from tensor_grep.cli.repo_map import (
        _apply_blast_radius_output_limits,
        _copy_partial_signal,
        build_symbol_blast_radius,
        build_symbol_blast_radius_from_map,
    )

    symbol = str(target.get("symbol") or "")
    status = str(call_site_evidence.get("status") or "")

    def _floor(
        *,
        source: str,
        related: list[dict[str, Any]],
        graph_trust_summary: dict[str, Any],
        resolution_gaps: list[dict[str, Any]],
        deadline_partial: bool,
        omitted: int,
        error: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        top_callers = [
            {
                "file": record.get("file"),
                "line": record.get("line"),
                "symbol": record.get("symbol"),
                "provenance": record.get("provenance"),
            }
            for record in related
        ]
        floor: dict[str, Any] = {
            "symbol": symbol,
            "callers_count": len(top_callers),
            "top_callers": top_callers,
            "source": source,
            "graph_trust_summary": graph_trust_summary,
            "resolution_gaps": resolution_gaps,
            "possibly_incomplete": bool(deadline_partial or omitted),
        }
        if error is not None:
            floor["error"] = error
        return floor, bool(deadline_partial)

    def _floor_from_radius_payload(radius_payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # Shared tail for BOTH the from-map reuse branch and the FS-backed fallback branch below
        # -- one definition so the two paths cannot silently drift apart on how a radius_payload
        # becomes a floor.
        if radius_payload.get("no_match"):
            return _floor(
                source="supplementary_blast_radius",
                related=[],
                graph_trust_summary=dict(radius_payload.get("graph_trust_summary") or {}),
                resolution_gaps=list(radius_payload.get("resolution_gaps") or []),
                deadline_partial=bool(radius_payload.get("partial")),
                omitted=0,
            )
        related = [
            record
            for record in (
                _related_call_site_record(caller, target_symbol=symbol)
                for caller in (radius_payload.get("callers") or [])
                if isinstance(caller, dict)
            )
            if record is not None
        ]
        output_limit = radius_payload.get("output_limit") or {}
        return _floor(
            source="supplementary_blast_radius",
            related=related,
            graph_trust_summary=dict(radius_payload.get("graph_trust_summary") or {}),
            resolution_gaps=list(radius_payload.get("resolution_gaps") or []),
            deadline_partial=bool(radius_payload.get("partial")),
            omitted=int(output_limit.get("omitted_callers") or 0),
        )

    if status in ("collected", "collected_no_call_sites"):
        return _floor(
            source="capsule_call_site_evidence",
            related=related_call_sites,
            graph_trust_summary=dict(call_site_evidence.get("graph_trust_summary") or {}),
            resolution_gaps=list(call_site_evidence.get("resolution_gaps") or []),
            deadline_partial=bool(call_site_evidence.get("partial")),
            omitted=int(call_site_evidence.get("omitted_call_sites") or 0),
        )

    if not symbol:
        # Degenerate capsule (no primary symbol resolved at all) -- nothing to scan.
        return _floor(
            source="no_primary_symbol",
            related=[],
            graph_trust_summary={},
            resolution_gaps=[],
            deadline_partial=False,
            omitted=0,
        )

    if status == "skipped" and call_site_evidence.get("reason") == (
        "primary symbol definition was not found by blast-radius"
    ):
        # The capsule's own rescue-equipped scan (agent_capsule.py's cold path always passes
        # `_rescue_call_site_evidence=True`) already ran and confirmed no_match for this exact
        # symbol -- a second identical FS scan would just repeat it. Report honestly without
        # paying the cost twice.
        return _floor(
            source="capsule_call_site_evidence",
            related=[],
            graph_trust_summary=dict(call_site_evidence.get("graph_trust_summary") or {}),
            resolution_gaps=list(call_site_evidence.get("resolution_gaps") or []),
            deadline_partial=bool(call_site_evidence.get("partial")),
            omitted=0,
        )

    scan_limit = rm.get("scan_limit")
    map_possibly_truncated = isinstance(scan_limit, dict) and bool(
        scan_limit.get("possibly_truncated")
    )

    if not map_possibly_truncated:
        # opt10 #2: `rm` is a COMPLETE map (the file-count cap never bit), so it already IS the
        # file universe an uncapped rescan would produce below -- reuse it via the map-reusing
        # sibling instead of paying a second build_repo_map walk+parse. Mirrors the established
        # from_map reuse idiom this codebase already uses elsewhere (agent_capsule._collect_
        # capsule_call_site_evidence_from_map; main.py's own blast-radius daemon gate): a
        # build_symbol_blast_radius_from_map call, then the SAME _apply_blast_radius_output_
        # limits + _copy_partial_signal tail build_symbol_blast_radius itself applies internally.
        try:
            radius_payload = build_symbol_blast_radius_from_map(
                rm,
                symbol,
                max_depth=1,
                deadline_monotonic=deadline_monotonic,
            )
            radius_payload = _apply_blast_radius_output_limits(
                radius_payload, max_callers=8, max_files=8
            )
            _copy_partial_signal(radius_payload, rm)
        except Exception as exc:  # pragma: no cover - defensive evidence side path
            return _floor(
                source="supplementary_blast_radius",
                related=[],
                graph_trust_summary={},
                resolution_gaps=[],
                deadline_partial=False,
                omitted=0,
                error=str(exc),
            )
        return _floor_from_radius_payload(radius_payload)

    # `rm` is possibly_truncated (a repo bigger than DEFAULT_AGENT_REPO_MAP_LIMIT): PRESERVE the
    # exact pre-#2 uncapped FS rescan below rather than reusing a map that may be missing the
    # very caller this floor exists to find (the #2 load-bearing guard).
    deadline_seconds_remaining: float | None = None
    if deadline_monotonic is not None:
        deadline_seconds_remaining = max(0.1, deadline_monotonic - time.monotonic())
    try:
        # `max_repo_files` is deliberately OMITTED (max recall within the shared deadline, not a
        # 2nd cap): this scan's only truncation source must stay the deadline, whose signal we
        # already read back below (`radius_payload.get("partial")` -> `deadline_partial` ->
        # top-level `partial`/exit-2). A file-count cap would truncate via `scan_limit` instead,
        # which this floor never inspects -- that truncation would be silently invisible in
        # `tg prepare`'s output. Do not "fix" this by adding a cap here.
        radius_payload = build_symbol_blast_radius(
            symbol,
            path,
            max_depth=1,
            max_callers=8,
            max_files=8,
            deadline_seconds=deadline_seconds_remaining,
        )
    except Exception as exc:  # pragma: no cover - defensive evidence side path
        return _floor(
            source="supplementary_blast_radius",
            related=[],
            graph_trust_summary={},
            resolution_gaps=[],
            deadline_partial=False,
            omitted=0,
            error=str(exc),
        )
    return _floor_from_radius_payload(radius_payload)


def _build_prepare_payload(
    *,
    path: str,
    query: str,
    claim: bool,
    deadline_monotonic: float | None = None,
    include_next_action: bool = False,
) -> dict[str, Any]:
    """Thin composition (CEO #5): ONE repo-map build supplies primary target, confidence,
    ask-user, and validation verbatim; the only NEW scan is the blast-radius floor (see
    ``_build_prepare_blast_radius_floor``). No new ranking/scan logic lives here.

    opt10 campaign ranked-queue item #2: builds ``rm`` directly instead of calling
    ``build_agent_capsule`` -- byte-identical to that function's own 2-line body
    (agent_capsule.py:2724-2731: same ``DEFAULT_AGENT_REPO_MAP_LIMIT`` cap, since this command
    never overrides ``max_repo_files``; same ``deadline_monotonic``, already resolved by the
    caller) followed by ``build_agent_capsule_from_map(..., _rescue_call_site_evidence=True)`` --
    the exact cold-path value ``build_agent_capsule`` itself always passes internally. The
    returned ``capsule`` is therefore byte-identical to calling ``build_agent_capsule(query,
    path, deadline_monotonic=...)`` directly; this function additionally keeps its own ``rm``
    reference to hand to the blast-radius floor below, so the floor can reuse it instead of
    paying a second ``build_repo_map`` walk+parse for the common natural-language-query CUJ.
    ``build_agent_capsule``'s own public contract/return is completely untouched by this --
    `tg agent` / MCP / its existing tests keep calling it directly, exactly as before."""
    from tensor_grep.cli.agent_capsule import _command_ref, build_agent_capsule_from_map
    from tensor_grep.cli.repo_map import (
        DEFAULT_AGENT_REPO_MAP_LIMIT,
        _copy_scan_limit,
        build_repo_map,
    )

    rm = build_repo_map(
        path, max_repo_files=DEFAULT_AGENT_REPO_MAP_LIMIT, deadline_monotonic=deadline_monotonic
    )
    capsule = build_agent_capsule_from_map(
        rm, query, deadline_monotonic=deadline_monotonic, _rescue_call_site_evidence=True
    )

    target = dict(capsule.get("primary_target") or {})
    call_site_evidence = dict(capsule.get("call_site_evidence") or {})
    related_call_sites = list(capsule.get("related_call_sites") or [])
    resolved_path = str(capsule.get("path") or path)
    resolved_query = str(capsule.get("query") or query)
    floor, floor_deadline_partial = _build_prepare_blast_radius_floor(
        path=resolved_path,
        rm=rm,
        target=target,
        call_site_evidence=call_site_evidence,
        related_call_sites=related_call_sites,
        deadline_monotonic=deadline_monotonic,
    )

    symbol = str(target.get("symbol") or "")
    claim_argv: list[object] = ["tg", "ledger", "claim", resolved_path]
    if symbol:
        claim_argv += ["--symbol", symbol]
    claim_argv += ["--intent", "edit", "--json"]
    claim_ref = _command_ref(claim_argv)
    claim_hook: dict[str, Any] = {
        "command": claim_ref["command"],
        "argv": claim_ref["argv"],
        "submitted": False,
        "advisory": True,
    }

    # Task #306 W2: surface a LIVE FOREIGN CLAIM on the DEFAULT path.
    #
    # Before this, `prepare` learned about overlaps only when `--claim` was passed -- i.e. you
    # discovered another agent had claimed your target only by claiming it yourself. An agent
    # doing the ordinary read-only `tg prepare` to get edit-ready got no signal at all, which is
    # the coordination gap in miniature: the ledger held the answer and nothing asked it.
    #
    # REPORTS, NEVER REFUSES. The #306 verdict is STAY ADVISORY, and `docs/CONTRACTS.md:225` says
    # the ledger has "no enforcement mechanism of any kind". Turning this into an `ask_user` gate
    # would be enforcement without any of the safety machinery real locking needs (fencing tokens,
    # lease expiry handling) -- the plan's §5 "what NOT to build" names exactly that.
    #
    # `list_claims` is a pure read: no write lock, no writes, expired entries pruned for display
    # only (ledger_store.py:782). Failure is swallowed for the same reason the `--claim` path
    # swallows its own: an advisory hook must never fail prepare's primary read.
    try:
        from tensor_grep.cli import ledger_store as _ledger_store

        _self_agent = _ledger_store.resolve_agent_id(None)
        _live = _ledger_store.list_claims(resolved_path)
        _foreign = [
            {
                "agent_id": entry.get("agent_id"),
                "scope": entry.get("scope"),
                "symbols": entry.get("symbols") or [],
                "intent": entry.get("intent"),
                "expires_at": entry.get("expires_at"),
            }
            for entry in (_live.get("claims") or [])
            if entry.get("agent_id") and entry.get("agent_id") != _self_agent
        ]
    except Exception:
        # Additive-conditional: on failure the key is simply absent, exactly as it is when no
        # foreign claim exists. A reader must not be able to distinguish "nothing claimed" from
        # "the probe broke" by the presence of an empty list -- so neither emits one.
        _foreign = []
    if _foreign:
        claim_hook["foreign_claims"] = _foreign
        claim_hook["foreign_claim_count"] = len(_foreign)

    if claim:
        if not symbol:
            claim_hook["error"] = "no primary symbol resolved; nothing to claim"
        else:
            from tensor_grep.cli import ledger_store

            try:
                submitted = ledger_store.submit_claim(
                    resolved_path, symbols=[symbol], intent="edit"
                )
            except Exception as exc:
                # Advisory coordination hook: a claim failure must never fail prepare's primary
                # read (mirrors ledger_store.submit_claim's own "NEVER blocks" contract one level
                # up -- see ledger_store.py:591-597, submit_claim's docstring).
                claim_hook["error"] = str(exc)
            else:
                claim_hook["submitted"] = True
                claim_hook["result"] = {
                    "claim": submitted["claim"],
                    "overlaps": submitted["overlaps"],
                }
                # v1.92.1 dogfood item 6: `ledger_store.resolve_agent_id` falls back to the
                # literal "anonymous" (CONTRACTS.md section 9 "agent_id resolution"; also
                # ledger_store._DEFAULT_AGENT_ID, not imported here to stay decoupled from that
                # module's private surface) whenever neither TG_LEDGER_AGENT_ID nor
                # TG_EVIDENCE_AGENT_ID is set -- `prepare` has no --agent-id flag of its own, so
                # that env-var pair is the only way a caller sets identity through this path.
                # Read the ACTUAL recorded value back off the submitted claim (never re-derive
                # the resolution rule here) so this hint can never drift from what was really
                # written to the ledger. Additive-only field, present only in the anonymous
                # case -- mirrors the conditional install_hint/autostart precedent elsewhere in
                # this module.
                if str(submitted["claim"].get("agent_id") or "") == "anonymous":
                    # Strengthened from "set TG_LEDGER_AGENT_ID for a stable identity", which four
                    # consecutive dogfoods reported as present-but-easy-to-miss. It now says what
                    # the caller LOSES, not just what to set: attribution. Machine-branchable
                    # sibling field so a harness can gate on it without string-matching prose.
                    claim_hook["agent_id_hint"] = (
                        "this claim is filed as 'anonymous' and is NOT attributable to you. Set "
                        "TG_LEDGER_AGENT_ID (or TG_EVIDENCE_AGENT_ID) to a stable per-agent value "
                        "so other agents can see WHO holds it."
                    )
                    claim_hook["agent_id_is_anonymous"] = True

    evidence_ref = _command_ref([
        "tg",
        "evidence",
        "emit",
        resolved_path,
        "--capsule",
        "<capsule.json>",
        "--query",
        resolved_query,
        "--json",
    ])
    evidence_hook = {
        "command": evidence_ref["command"],
        "argv": evidence_ref["argv"],
        "note": (
            "tg prepare does not persist a capsule file. Save this payload's JSON to disk (e.g. "
            "capsule.json), then run the command above with --capsule pointing at it to attach "
            "blast-radius/confidence evidence to a signed receipt."
        ),
    }

    result: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "prepare",
        "prepare_version": 1,
        "path": resolved_path,
        "query": resolved_query,
        "primary_target": target,
        "alternative_targets": capsule.get("alternative_targets", []),
        "confidence": capsule.get("confidence", {}),
        "ask_user_before_editing": capsule.get(
            "ask_user_before_editing", {"required": False, "reasons": []}
        ),
        "validation_plan": capsule.get("validation_plan", []),
        "validation_commands": capsule.get("validation_commands", []),
        "context_consistency": capsule.get("context_consistency", {}),
        "rollback": capsule.get("rollback", {}),
        "blast_radius_floor": floor,
        "coordination": {"claim": claim_hook, "evidence": evidence_hook},
    }
    _copy_scan_limit(result, capsule)

    if include_next_action:
        target_path = str(target.get("file") or path)
        result["next_action"] = {
            "action": {
                "type": "edit_file",
                "path": target_path,
                "span": {
                    "start_line": target.get("line"),
                    "end_line": target.get("end_line", target.get("line")),
                },
                "instruction": f"Edit {target.get('symbol') or target_path} to address: {query}",
                "stop_if": ["ask_user_before_editing.required", "result_incomplete"],
            },
            "on_success": {
                "type": "run",
                "argv": ["uv", "run", "pytest", "-q"],
                "deadline_seconds": 300,
                "max_output_bytes": 1000000,
                "allow_network": False,
                "fail_closed_on_timeout": True,
            },
            "on_failure": {
                "type": "narrow_scope",
                "suggested_path": str(Path(target_path).parent).replace("\\", "/"),
            },
        }

    capsule_partial = bool(capsule.get("partial"))
    if capsule_partial or floor_deadline_partial:
        result["partial"] = True
        result["partial_reason"] = "deadline"
        result["deadline_limit"] = {
            "deadline_exceeded": True,
            "capsule": capsule_partial,
            "blast_radius_floor": floor_deadline_partial,
        }
    return result


@dataclass(frozen=True)
class PrepareSnapshotV1:
    """Strict typed projection of a `_build_prepare_payload` result (Task 8 Step 2).

    Additive-only: every field here is read from the existing untyped payload dict via
    `.get(...)`, never computed independently, so this type can never disagree with what
    `tg prepare` itself returns for the same call. `raw` retains the complete untyped payload
    for any caller that needs a field not yet promoted to a named attribute -- promoting a new
    field later is a pure addition to this dataclass, not a behavior change to `raw` or to
    `_build_prepare_payload`.

    This type has no CLI/MCP consumer yet (Task 8's `tg edit-ready` command is out of scope for
    this slice) -- it exists so that future composition work has a typed contract to build
    against instead of re-deriving field names from the dict shape by hand.
    """

    version: int
    path: str
    query: str
    primary_target: dict[str, Any]
    confidence: dict[str, Any]
    blast_radius_floor: dict[str, Any]
    coordination: dict[str, Any]
    partial: bool
    partial_reason: str | None
    raw: dict[str, Any] = field(repr=False)


def build_prepare_snapshot(
    *,
    path: str,
    query: str,
    claim: bool = False,
    deadline_monotonic: float | None = None,
    include_next_action: bool = False,
) -> PrepareSnapshotV1:
    """Build a `PrepareSnapshotV1` by calling the existing `_build_prepare_payload` and
    projecting its result onto typed fields. Delegates every computation to that function --
    this wrapper performs no scanning, ranking, or I/O of its own, so it carries none of
    `_build_prepare_payload`'s side effects beyond what that function already does (including,
    when `claim=True`, the same advisory ledger claim submission `_build_prepare_payload`
    already performs)."""
    payload = _build_prepare_payload(
        path=path,
        query=query,
        claim=claim,
        deadline_monotonic=deadline_monotonic,
        include_next_action=include_next_action,
    )
    return PrepareSnapshotV1(
        version=int(payload.get("version") or 1),
        path=str(payload.get("path") or path),
        query=str(payload.get("query") or query),
        primary_target=dict(payload.get("primary_target") or {}),
        confidence=dict(payload.get("confidence") or {}),
        blast_radius_floor=dict(payload.get("blast_radius_floor") or {}),
        coordination=dict(payload.get("coordination") or {}),
        partial=bool(payload.get("partial", False)),
        partial_reason=payload.get("partial_reason"),
        raw=payload,
    )
