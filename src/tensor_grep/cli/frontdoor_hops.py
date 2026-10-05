"""Cross-door delegation depth marker (native <-> Python front door).

The native front door re-execs ``python -m tensor_grep`` for flags it routes to Python, and the
Python door delegates some searches back to the native binary. Neither routing table is a proof
that the other side will not bounce the request straight back (2026-10-05: ``-s`` / ``-N`` with
``--json`` ping-ponged 842 processes in ~60 s). ``TG_REEXEC_GUARD`` breaks the KNOWN cycle; this
counter is the generic backstop for every cycle nobody has found yet.

Contract (mirrored in rust_core/src/python_sidecar.rs):
  * ``TG_FRONTDOOR_HOPS`` = number of cross-door hops already taken; absent means 0.
  * A door about to spawn the OTHER door passes ``hops + 1`` to the child.
  * At ``hops >= MAX_FRONTDOOR_HOPS``, or on a malformed value, the door refuses: exit 2 with a
    message naming the variable. Never loop, never silently degrade.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

FRONTDOOR_HOPS_ENV = "TG_FRONTDOOR_HOPS"
#: tg -> py -> tg -> py is the deepest LEGITIMATE chain (native sidecar command whose Python
#: handler shells out to native search, which passes a Python-only flag through). 4 leaves one
#: hop of headroom and still bounds a runaway to a handful of processes.
MAX_FRONTDOOR_HOPS = 4


class FrontdoorHopLimitError(RuntimeError):
    exit_code = 2


def _current_hops(environ: Mapping[str, str]) -> int:
    raw = environ.get(FRONTDOOR_HOPS_ENV)
    if raw is None or raw.strip() == "":
        return 0
    try:
        value = int(raw.strip())
    except ValueError:
        value = -1
    if value < 0:
        raise FrontdoorHopLimitError(
            f"tensor-grep: refusing to delegate between the native and Python front doors: "
            f"{FRONTDOOR_HOPS_ENV}={raw!r} is not a non-negative integer.\n"
        )
    return value


def next_hop_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child environment for a cross-door spawn, or FrontdoorHopLimitError."""
    source = os.environ if environ is None else environ
    hops = _current_hops(source)
    if hops >= MAX_FRONTDOOR_HOPS:
        raise FrontdoorHopLimitError(
            "tensor-grep: refusing to delegate between the native and Python front doors: "
            f"{FRONTDOOR_HOPS_ENV}={hops} reached the limit of {MAX_FRONTDOOR_HOPS}. This is a "
            "routing loop (each door kept handing the request to the other); report the exact "
            "command line. Output was not produced.\n"
        )
    child = dict(source)
    child[FRONTDOOR_HOPS_ENV] = str(hops + 1)
    return child


def child_env_or_refusal() -> tuple[dict[str, str] | None, int]:
    """``(child_env, 0)`` for a cross-door spawn, or ``(None, 2)`` after writing the refusal to
    stderr. One call-site shape for every ``subprocess.run(native_tg, ...)`` in the Python door."""
    import sys

    try:
        return next_hop_env(), 0
    except FrontdoorHopLimitError as exc:
        sys.stderr.write(str(exc))
        return None, exc.exit_code


def stamp_bootstrap_hop_or_refuse() -> int | None:
    """For the bootstrap passthrough spawns, which inherit ``os.environ`` (``_popen_child`` takes no
    ``env``): stamp the next hop into THIS process's environment, or return exit code 2 after
    writing the refusal. Safe only because the bootstrap is a pure passthrough that exits with the
    child's code immediately afterwards."""
    import sys

    try:
        child_env = next_hop_env()
    except FrontdoorHopLimitError as exc:
        sys.stderr.write(str(exc))
        return exc.exit_code
    os.environ[FRONTDOOR_HOPS_ENV] = child_env[FRONTDOOR_HOPS_ENV]
    return None
