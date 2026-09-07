"""One internal representation of "was this result complete", derived from --
never replacing -- the existing per-surface projections.

AGT-07 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 09). The CLI/MCP
incompleteness vocabulary already has a correct projection function,
``tensor_grep.cli.incompleteness.unified_incomplete_envelope``, but it returns an untyped
``dict[str, Any]`` with an overloaded boolean ``status`` and a free-text ``cause`` -- so a new
caller cannot tell "scan incomplete" from "output capped but scan complete" from "unknown legacy
evidence" without re-deriving the same string comparisons that function already does. This module
adds a typed, immutable record ON TOP of that existing projection: :func:`from_legacy_envelope`
is a pure adapter from the dict form to :class:`CompletenessEvidence`, and
:meth:`CompletenessEvidence.to_legacy_dict` is the inverse. Neither producer (``main.py``,
``mcp_server.py``) nor consumer of the legacy dict shape is migrated by this slice -- see
docs/plans/2026-09-07-agentic-quality-simplification.md Task 09's remaining checkboxes.

THE FROZEN TRUTH TABLE (do not reorder predicates without re-reading this):
    complete empty scan       -> COMPLETE; no fabricated matches
    display cap only          -> output limited; scan still complete (COMPLETE, OUTPUT_CAPPED)
    file scan cap             -> scan incomplete; disclose scan limit (INCOMPLETE, SCAN_LIMIT)
    deadline                  -> scan/assembly state as observed; preserve cause (INCOMPLETE, DEADLINE)
    unreadable path           -> incomplete; not repaired merely by a larger budget
                                  (INCOMPLETE, UNREADABLE_PATH, retry_kind=NOT_REMEDIABLE)
    mixed roots, one partial  -> aggregate cannot claim all roots complete (INCOMPLETE, NESTED)
    unknown legacy evidence   -> preserve unknown; never manufacture complete (INCOMPLETE, UNKNOWN)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from tensor_grep.cli.incompleteness import unified_incomplete_envelope


class ScanState(Enum):
    """Whether the underlying walk/search itself finished."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class Cause(Enum):
    """Closed vocabulary for WHY a result is incomplete. Mirrors the legacy free-text
    ``cause`` strings byte-for-byte via :data:`_CAUSE_TO_LEGACY_STRING` -- this enum does not
    invent new spellings, per incompleteness.py's documented "two vocabularies, not unified"
    stance (task #293).
    """

    NONE = "none"
    SCAN_LIMIT = "scan_limit"
    UNREADABLE_PATH = "unreadable_path"
    DEADLINE = "deadline"
    TIMEOUT = "timeout"
    TRUNCATED = "truncated"
    PARTIAL = "partial"
    NESTED_INCOMPLETE = "nested_incomplete"
    UNKNOWN = "unknown"


class RetryKind(Enum):
    """Is a bigger budget worth trying? Never inferred from cause spelling alone at the
    record level -- callers pass the already-computed legacy ``budget_remediable`` bool through
    :func:`from_legacy_envelope`, so this record cannot silently disagree with
    ``incompleteness.budget_remediable``'s allow-list.
    """

    NOT_APPLICABLE = "not_applicable"  # scan_state is COMPLETE; retry has no meaning
    BUDGET_REMEDIABLE = "budget_remediable"
    NOT_REMEDIABLE = "not_remediable"


_CAUSE_TO_LEGACY_STRING: dict[Cause, str | None] = {
    Cause.NONE: None,
    Cause.SCAN_LIMIT: "scan_limit",
    Cause.UNREADABLE_PATH: "unreadable_path",
    Cause.DEADLINE: "deadline",
    Cause.TIMEOUT: "timeout",
    Cause.TRUNCATED: "truncated",
    Cause.PARTIAL: "partial",
    Cause.NESTED_INCOMPLETE: "nested_incomplete",
    Cause.UNKNOWN: "unknown",
}
_LEGACY_STRING_TO_CAUSE: dict[str, Cause] = {
    "scan_limit": Cause.SCAN_LIMIT,
    "unreadable_path": Cause.UNREADABLE_PATH,
    "deadline": Cause.DEADLINE,
    "timeout": Cause.TIMEOUT,
    "truncated": Cause.TRUNCATED,
    "partial": Cause.PARTIAL,
    "nested_incomplete": Cause.NESTED_INCOMPLETE,
}


@dataclass(frozen=True)
class CompletenessEvidence:
    """Immutable projection of one result's completeness evidence.

    ``output_capped`` is independent of ``scan_state``: a display-cap-only result is
    ``(ScanState.COMPLETE, output_capped=True)``, distinct from a scan-cap result which is
    ``(ScanState.INCOMPLETE, cause=Cause.SCAN_LIMIT)``. Collapsing those two into one boolean
    is exactly the bug this record exists to prevent (truth-table row 2 vs row 3).
    """

    scan_state: ScanState
    cause: Cause
    retry_kind: RetryKind
    output_capped: bool = False
    # Only set when cause is Cause.UNKNOWN: the ORIGINAL unrecognised legacy string, preserved
    # verbatim so round-tripping never lies about what the source actually said ("preserve
    # unknown; never manufacture complete" -- collapsing every unrecognised spelling to the
    # literal word "unknown" would itself be a small manufacture).
    unknown_cause_text: str | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Inverse of :func:`from_legacy_envelope` -- must round-trip byte-for-byte through
        the ``status``/``cause``/``budget_remediable`` shape ``unified_incomplete_envelope``
        already emits, so an unmigrated consumer sees no difference.
        """
        status = self.scan_state is ScanState.INCOMPLETE
        if not status:
            cause_value = None
        elif self.cause is Cause.UNKNOWN and self.unknown_cause_text is not None:
            cause_value = self.unknown_cause_text
        else:
            cause_value = _CAUSE_TO_LEGACY_STRING[self.cause]
        return {
            "status": status,
            "cause": cause_value,
            "budget_remediable": (self.retry_kind is RetryKind.BUDGET_REMEDIABLE)
            if status
            else False,
        }


def from_legacy_envelope(envelope: dict[str, Any]) -> CompletenessEvidence:
    """Adapt the existing ``unified_incomplete_envelope`` dict shape into a typed record.

    Pure and total: every legal envelope shape that function can emit maps to exactly one
    :class:`CompletenessEvidence`. An unrecognised non-empty cause string (future vocabulary
    growth) maps to ``Cause.UNKNOWN`` rather than raising -- "unknown legacy evidence -> preserve
    unknown; never manufacture complete" from the frozen truth table.
    """
    status = bool(envelope.get("status", False))
    if not status:
        # unified_incomplete_envelope carries no output-cap signal (that is a separate,
        # downstream display concern); output_capped stays False here by construction. A future
        # migration that threads a real cap signal through the legacy dict should read it here.
        return CompletenessEvidence(
            scan_state=ScanState.COMPLETE,
            cause=Cause.NONE,
            retry_kind=RetryKind.NOT_APPLICABLE,
        )
    raw_cause = envelope.get("cause")
    if not raw_cause:
        # status=True but no cause was ever set (unified_incomplete_envelope's `status` and
        # `cause` are derived independently: some producer shapes flip status without ever
        # populating any of the cause fallbacks). Cause.NONE round-trips to legacy `None`
        # exactly, unlike Cause.UNKNOWN which round-trips to the literal string "unknown" --
        # collapsing this case into UNKNOWN would fabricate a cause the source never gave.
        known_cause = Cause.NONE
    else:
        known_cause = _LEGACY_STRING_TO_CAUSE.get(raw_cause)
    cause = known_cause if known_cause is not None else Cause.UNKNOWN
    remediable = bool(envelope.get("budget_remediable", False))
    return CompletenessEvidence(
        scan_state=ScanState.INCOMPLETE,
        cause=cause,
        retry_kind=RetryKind.BUDGET_REMEDIABLE if remediable else RetryKind.NOT_REMEDIABLE,
        unknown_cause_text=raw_cause if known_cause is None and raw_cause else None,
    )


def project(payload: dict[str, Any]) -> CompletenessEvidence:
    """Convenience: run a raw result payload through the existing
    ``unified_incomplete_envelope`` projection, then wrap it as a typed record. Equivalent to
    ``from_legacy_envelope(unified_incomplete_envelope(payload))``.
    """
    return from_legacy_envelope(unified_incomplete_envelope(payload))
