"""Canonical vocabulary for recognising a DISCLOSED incomplete result.

Task #276 slice C0. When the native search path learns to exit 2 on an incomplete walk, every
exit-code consumer must become three-state aware: ``0``/``1`` parse output, ``2`` parse output
AND read the incompleteness marker, ``>2`` error.

This module exists so that vocabulary lives in exactly ONE place. It is deliberately a package
module rather than a copy-pasted tuple: `scripts/agent_readiness.py`, `benchmarks/
run_rg_parity_benchmarks.py` and `benchmarks/run_gpu_native_benchmarks.py` are three separate
entry points that all already import from ``tensor_grep.cli``, and three private copies of a
marker list would drift the first time a fifth cause is added. Fix the class, not the instance.

THE DESIGN POINT -- this is an ALLOW-LIST, never a bare ``returncode == 2`` tolerance. Exit 2 is
overloaded: it is what an honest incomplete scan returns, but it is ALSO what a catastrophic
failure returns -- a regex syntax error, an unresolvable engine -- exactly as in ripgrep, whose
own documentation describes 2 as "true for both catastrophic errors ... and soft errors". A
consumer that tolerates the bare code would swallow every one of those and become a check that
cannot fail.
"""

from __future__ import annotations

from typing import Any

# Two homes, because the marker's location differs by route and BOTH are load-bearing:
#   * the JSON envelope's keys, written only when the result is genuinely incomplete
#     (`formatters/json_fmt.py:127`, `:140`) -- so a complete envelope never carries them;
#   * the plain-text route's stderr sentinel (`backends/ripgrep_backend.py:143`, `:324`, `:443`,
#     and the timeout variant at `:187`).
# An independent audit caught why the second is required: a JSON-key-only check has a treatment
# arm that can never fire for a caller that only exercises plain-text routes.
INCOMPLETENESS_MARKERS: tuple[str, ...] = (
    "result_incomplete",
    "incomplete_reason_class",
    "keeping partial results",
)


# Causes a bigger budget CAN fix, in BOTH spellings the product ships. The two vocabularies are
# deliberately NOT unified (#293): `truncation_cause` is hyphenated, `incomplete_reason_class` /
# `partial_reason` are underscored, each is internally consistent, and renaming either breaks a
# documented contract for no correctness gain. So this maps both rather than normalising.
_BUDGET_REMEDIABLE_CAUSES: frozenset[str] = frozenset({
    "project-files",  # the --max-files/--max-repo-files count cap -- raise it
    "max-scan-entries",  # DirectoryScanner's own entry budget -- raise it
    "scan_limit",  # underscored sibling of the above
    "deadline",  # the --deadline wall-clock bound -- raise it
    "timeout",  # a per-file/subprocess wall-clock bound -- raise it
})


def budget_remediable(cause: str | None) -> bool:
    """Can a BIGGER budget fix this truncation cause? Fail-closed.

    Task #307-C. The knowledge this encodes already existed -- but only inside
    `tests/unit/test_truncation_cause_vocabulary_ratchet.py`, which meant CI could branch on it and
    the shipped CLI could not. `budget_remediable` was emitted by exactly ONE surface (the MCP
    `scan_limit` object, `mcp_server.py`, task #283) while every CLI route stamped a cause with no
    machine-branchable "is a retry worth it?" flag. A consumer that cannot tell
    `raise --max-repo-files` from `you will never read that directory` retries forever or gives up
    on a fixable scan.

    ALLOW-LIST, never a deny-list (#282). ``unreadable-path``/``unreadable_path`` is the value that
    must return False, but enumerating the *unsafe* cases fails OPEN on any cause a future author
    adds and this function has not been taught -- an unrecognised cause would be advertised as
    "just raise the limit", which is the wrong-knob advice #283 exists to prevent. So: name the
    SAFE causes, and return False for everything else including ``None`` and ``"unknown"``.
    """
    return cause in _BUDGET_REMEDIABLE_CAUSES


def disclosed_incomplete(stdout: str | None, stderr: str | None) -> bool:
    """True when an exit-2 run DISCLOSED that its scan was incomplete.

    Merely naming a path is not a disclosure: a bare ``permission denied`` line with no sentinel
    is indistinguishable from a hard failure, so it deliberately returns False.
    """
    haystack = f"{stdout or ''} {stderr or ''}"
    return any(marker in haystack for marker in INCOMPLETENESS_MARKERS)


def incomplete_class_fragment(results: Any) -> dict[str, str]:
    """`incomplete_reason_class` as a splat-able fragment, emitted ONLY when classified.

    MCP carried `result_incomplete` (14 sites) and the free-text `incomplete_reason`, but never
    the closed-vocabulary CLASS the CLI has emitted since task #276 slice 1 -- so an agent on the
    most machine-facing surface tg has could learn THAT a result was partial but had to
    string-sniff prose to learn WHY. `docs/CONTRACTS.md` introduced the class precisely so callers
    would not have to do that.

    Returns an EMPTY dict when unclassified, so `**` contributes nothing and a payload without a
    classified cause stays byte-identical to contract 1.5.0. Emitting `null` instead would teach
    readers to skip the key, which is how a disclosure field quietly becomes decoration.

    NOTE the asymmetry, because it is deliberate and a caller must not get it backwards: an
    ABSENT class means "cause not classifiable", NOT "scan complete". Completeness is carried by
    `result_incomplete` alone. See the AST-backend-failure site, which sets `result_incomplete`
    and deliberately emits no class rather than mislabelling a backend bug as `unreadable_path`.

    Distinct from MCP's own `truncation_cause` on the `scan_limit` object, which has an explicit
    `"unknown"` member and its own hyphenated vocabulary. Task #293 settled that these two must
    NOT be unified; this helper only ever emits the CLI-family value it is handed.

    THE TWO ERROR ENVELOPES ARE NOT ALIKE, and an audit corrected me on this. Both set
    `result_incomplete: True` as a literal rather than from the aggregate:

    * `broad_scan_refused` DOES classify, as `"scan_limit"`. I first argued it should not --
      "it already has `error.code`, so the class adds nothing" -- but that reasoning was wrong.
      The site reports `truncated: true` AND `result_incomplete: true`, which is a scan-policy
      ceiling: precisely what `scan_limit` denotes. My objection was that "raise the limit" and
      "narrow the scope" are different remedies, but the class encodes BUDGET-REMEDIABILITY, and
      both of those are budget-remediable. `error.code` is a sibling signal, not a substitute.
    * `invalid_input` does NOT classify. Nothing was walked and the request never became a scan,
      so no member of a completeness vocabulary applies. It still routes through this helper so
      the coverage is structural: every serialized `result_incomplete` payload goes through one
      place, and this one legitimately contributes `{}`.

    Recorded because "already has an error code" is a seductive reason to skip a disclosure, and
    it was wrong once here already.

    Relocated from `mcp_server.py` (P3, MCP incompleteness envelope standardization) alongside
    `unified_incomplete_envelope` below, since both derive the MCP incompleteness vocabulary and
    belong in the module that owns that vocabulary rather than in the MCP tool-registration file.
    """
    value = getattr(results, "incomplete_reason_class", None)
    return {"incomplete_reason_class": value} if value else {}


def unified_incomplete_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """P3: derive the unified MCP `incomplete` envelope from a tool's existing,
    surface-specific incompleteness signals (`result_incomplete`,
    `incomplete_reason`, `budget_remediable`), so every MCP tool response gains
    ONE consistent field a client can check regardless of which tool it called --
    without touching any of those legacy fields (purely additive; the caller uses
    ``setdefault`` so a tool that already sets its own ``incomplete`` key wins
    outright).

    Falls back to ``{"status": False, "cause": None, "budget_remediable": False}``
    for a tool with no incompleteness concept at all.
    """
    status = bool(payload.get("result_incomplete", False))
    cause = payload.get("incomplete_reason")
    if cause is not None and not isinstance(cause, str):
        cause = str(cause)
    remediable = bool(payload.get("budget_remediable", False))
    return {
        "status": status,
        "cause": cause,
        "budget_remediable": remediable if status else False,
    }
