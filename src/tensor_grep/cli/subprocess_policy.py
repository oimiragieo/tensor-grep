from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from typing import Any


def _configured_positive_float(env_var: str, default: float) -> float:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def configured_subprocess_timeout_seconds(
    *,
    env_var: str = "TG_SUBPROCESS_TIMEOUT_SECONDS",
    default: float = 600.0,
) -> float:
    return _configured_positive_float(env_var, default)


def configured_git_timeout_seconds() -> float:
    return _configured_positive_float("TG_GIT_TIMEOUT_SECONDS", 120.0)


def deadline_capped_timeout_seconds(
    base_timeout_seconds: float, *, deadline_monotonic: float | None
) -> float | None:
    """Cap `base_timeout_seconds` (e.g. `configured_git_timeout_seconds()`'s 120s default) to
    whatever wall-clock budget remains before `deadline_monotonic` (an absolute
    ``time.monotonic()`` timestamp, the same pre-anchored value threaded through every other
    deadline-scoped seam in this codebase).

    tg-codemap 90s-timeout root cause: a single git subprocess call (`git status` on a large/
    slow working tree in particular) is bounded ONLY by its own `TG_GIT_TIMEOUT_SECONDS` (120s
    default) -- a budget totally decoupled from a caller's `--deadline`. A caller that threads
    `deadline_monotonic` through its per-iteration loops but calls `run_subprocess` with the
    raw, uncapped git timeout can still blow past its advertised deadline by up to ~120s per
    call, because a subprocess call is atomic (no per-iteration check is possible mid-call) --
    the only lever is capping the timeout passed in BEFORE the call starts.

    Returns `None` when the deadline has ALREADY passed -- the caller must skip the subprocess
    call entirely (never invoke `run_subprocess`/`subprocess.run` with a timeout of 0 or less;
    that raises immediately rather than degrading gracefully). Returns `base_timeout_seconds`
    unchanged when `deadline_monotonic is None` (every pre-existing caller) -- a byte-identical
    no-op.
    """
    if deadline_monotonic is None:
        return base_timeout_seconds
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        return None
    return min(base_timeout_seconds, remaining)


def configured_ripgrep_timeout_seconds() -> float:
    sidecar_ms = os.environ.get("TG_SIDECAR_TIMEOUT_MS")
    if sidecar_ms is not None:
        try:
            parsed_ms = float(sidecar_ms)
        except (TypeError, ValueError):
            parsed_ms = 0.0
        if parsed_ms > 0:
            return parsed_ms / 1000.0
    # 60s (was 600s): ripgrep does GB/s, so a >60s search means something pathological (scanning
    # an unexcluded huge/index dir). Fail FAST with guidance instead of a 10-minute hang; an agent
    # cannot wait 10 minutes. Env-overridable for the rare legitimately-huge monorepo.
    return _configured_positive_float("TG_RG_TIMEOUT_SECONDS", 60.0)


def run_subprocess(
    args: Sequence[str] | str,
    *,
    timeout_seconds: float | None = None,
    timeout_env_var: str = "TG_SUBPROCESS_TIMEOUT_SECONDS",
    default_timeout_seconds: float = 600.0,
    shell: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    if timeout_seconds is None:
        timeout_seconds = configured_subprocess_timeout_seconds(
            env_var=timeout_env_var,
            default=default_timeout_seconds,
        )
    return subprocess.run(
        args,
        shell=shell,
        timeout=timeout_seconds,
        **kwargs,
    )
