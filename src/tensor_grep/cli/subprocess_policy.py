from __future__ import annotations

import os
import subprocess
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


def configured_ripgrep_timeout_seconds() -> float:
    sidecar_ms = os.environ.get("TG_SIDECAR_TIMEOUT_MS")
    if sidecar_ms is not None:
        try:
            parsed_ms = float(sidecar_ms)
        except (TypeError, ValueError):
            parsed_ms = 0.0
        if parsed_ms > 0:
            return parsed_ms / 1000.0
    return _configured_positive_float("TG_RG_TIMEOUT_SECONDS", 600.0)


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
