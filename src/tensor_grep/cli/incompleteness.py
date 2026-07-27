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
