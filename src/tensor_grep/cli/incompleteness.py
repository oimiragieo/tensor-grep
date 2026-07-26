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


def disclosed_incomplete(stdout: str | None, stderr: str | None) -> bool:
    """True when an exit-2 run DISCLOSED that its scan was incomplete.

    Merely naming a path is not a disclosure: a bare ``permission denied`` line with no sentinel
    is indistinguishable from a hard failure, so it deliberately returns False.
    """
    haystack = f"{stdout or ''} {stderr or ''}"
    return any(marker in haystack for marker in INCOMPLETENESS_MARKERS)
