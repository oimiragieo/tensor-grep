from __future__ import annotations

import time
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)
from tensor_grep.cli.agent_capsule_targets import (
    _dedupe as _dedupe,
)
from tensor_grep.cli.agent_capsule_targets import (
    _target_symbol_was_explicitly_requested as _target_symbol_was_explicitly_requested,
)


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
    deadline_monotonic: float | None = None,
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
    # CLI consistency fix (CEO v1.71.3 dogfood): this rescue scan is a SECOND, independent
    # FS-backed build_repo_map (via build_symbol_blast_radius) over the same `path` -- a `tg agent
    # --deadline N` request must not let this second scan run unbounded after the shared budget
    # was already spent on the primary render/ranking pass above. build_symbol_blast_radius already
    # accepts a RELATIVE deadline_seconds (it is one of the 7 pre-existing deadline'd commands), so
    # convert the shared ABSOLUTE deadline_monotonic to whatever budget remains at this call site.
    deadline_seconds_remaining: float | None = None
    if deadline_monotonic is not None:
        deadline_seconds_remaining = max(0.1, deadline_monotonic - time.monotonic())
    try:
        radius_payload = repo_map.build_symbol_blast_radius(
            target_symbol,
            path,
            max_depth=1,
            max_repo_files=max_repo_files,
            max_callers=max_callers,
            max_files=max_callers,
            deadline_seconds=deadline_seconds_remaining,
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
    # #639 Opus-gate nit 1 (dogfood #1 RESIDUAL): this rescue scan's own deadline_seconds budget
    # (floored to >=0.1s above) can itself truncate `radius_payload` -- reuse repo_map's own
    # `_copy_partial_signal` helper (the exact propagation every other symbol builder in this
    # codebase uses) so that signal survives into the evidence dict an agent actually reads,
    # instead of being silently dropped the moment it's repackaged here.
    repo_map._copy_partial_signal(evidence, radius_payload)
    return related_call_sites, evidence


def _collect_capsule_call_site_evidence_from_map(
    query: str,
    rm: dict[str, Any],
    target: dict[str, Any],
    *,
    include_blast_radius: bool,
    max_files: int,
    seed_confidence: float,
    deadline_monotonic: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Task #108 (Tier-2 daemon moat) map-based sibling of ``_collect_capsule_call_site_evidence``:
    identical gating + evidence shape, but resolves the blast radius against an already-built
    ``rm`` (e.g. the warm session daemon's cached map) via ``build_symbol_blast_radius_from_map``
    instead of re-scanning through the cold ``build_symbol_blast_radius`` wrapper.

    ``deadline_monotonic`` (SLA-honesty fix, +10% campaign #5): this warm branch used to call
    ``build_symbol_blast_radius_from_map`` with no deadline at all -- structurally unbounded even
    though the caller (``build_agent_capsule_from_map``) already has an in-scope
    ``deadline_monotonic`` it threads into every OTHER sibling stage (context-render, DAR outbound
    deps, vendored-subtree detection). The cold sibling above already threads its own
    ``deadline_monotonic`` into the FS-backed ``build_symbol_blast_radius`` (converted to a
    relative ``deadline_seconds`` there, since that is the shape ITS callee accepts); this warm
    callee, ``build_symbol_blast_radius_from_map``, already accepts ``deadline_monotonic`` directly
    (repo_map.py) and threads it into its own defs/callers/impact sub-calls, so no conversion is
    needed here -- a straight pass-through. ``None`` (the default, and every existing caller's
    behavior before this fix) is byte-identical to the pre-fix call.

    TRAP A (audit #107 class): the cold wrapper transparently retries a truncated no_match via
    ``_literal_symbol_seed_files`` (see ``build_symbol_blast_radius``); the map-based lookup here
    has NO such rescue available (there is no filesystem to re-scan -- only the map already in
    hand). A no_match on a possibly-truncated map is therefore UNRELIABLE here in a way it is not
    on the cold path: the symbol may simply live outside the daemon session's scan window rather
    than genuinely be absent. Returns a third element, ``daemon_unreliable``, True in exactly that
    case -- reusing ``repo_map._blast_radius_no_match_is_possibly_truncated`` so this arm and the
    cold arm's own rescue trigger agree verbatim on when a no_match is trustworthy. The caller
    (``build_agent_capsule_from_map``) surfaces this as a top-level sentinel so the daemon client
    wrapper (``main._maybe_agent_via_running_daemon``) can discard the whole response and fall
    back to cold, exactly like the existing #107 fix for the standalone ``blast-radius`` command.
    Every OTHER early return below mirrors the cold sibling's reasons exactly and is always
    reliable (nothing about them depends on the map's scan window), so they report False.
    """
    if not include_blast_radius:
        return (
            [],
            {
                "status": "disabled",
                "reason": "call-site evidence disabled by caller",
            },
            False,
        )
    target_symbol = str(target.get("symbol") or "")
    if not target_symbol:
        return (
            [],
            {
                "status": "skipped",
                "reason": "primary target has no symbol",
            },
            False,
        )
    if not _target_symbol_was_explicitly_requested(query, target):
        return (
            [],
            {
                "status": "skipped",
                "reason": "primary symbol was not explicitly requested by query",
            },
            False,
        )
    if seed_confidence < 0.75:
        return (
            [],
            {
                "status": "skipped",
                "reason": "primary target confidence below call-site collection threshold",
            },
            False,
        )

    max_callers = max(1, min(int(max_files) * 2, 8))
    try:
        radius_payload = repo_map.build_symbol_blast_radius_from_map(
            rm,
            target_symbol,
            max_depth=1,
            deadline_monotonic=deadline_monotonic,
        )
        radius_payload = repo_map._apply_blast_radius_output_limits(
            radius_payload,
            max_callers=max_callers,
            max_files=max_callers,
        )
    except Exception as exc:  # pragma: no cover - defensive evidence side path
        return (
            [],
            {
                "status": "error",
                "reason": "call-site evidence collection failed",
                "error": str(exc),
            },
            False,
        )

    if radius_payload.get("no_match"):
        daemon_unreliable = repo_map._blast_radius_no_match_is_possibly_truncated(radius_payload)
        return (
            [],
            {
                "status": "skipped",
                "reason": "primary symbol definition was not found by blast-radius",
                "symbol": target_symbol,
            },
            daemon_unreliable,
        )

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
        "resolution_gaps": _as_list_of_dicts(radius_payload.get("resolution_gaps")),
    }
    # #639 Opus-gate nit 1: structural parity with the cold sibling above -- the map-based
    # blast-radius lookup does not run a fresh time-bounded scan itself, but `rm` may already be
    # partial from an earlier deadline cutoff; propagate that forward the same way rather than
    # silently dropping it just because this collector was reached via the warm/daemon path.
    repo_map._copy_partial_signal(evidence, radius_payload)
    return related_call_sites, evidence, False
