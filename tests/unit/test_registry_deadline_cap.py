"""Pin the public registry child timeout to its local five-second ceiling."""

from __future__ import annotations

import subprocess as real_subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import tensor_grep.core.registry_grounding as registry_grounding


@pytest.mark.parametrize(
    ("clock_value", "deadline_monotonic", "expected_timeout"),
    [
        pytest.param(10.0, None, 5.0, id="ordinary-default-budget"),
        pytest.param(65535.99999999999, None, 5.0, id="float-boundary-default-budget"),
        pytest.param(10.0, 12.0, 2.0, id="caller-budget-remains-authoritative"),
    ],
)
def test_fetch_registry_metadata_FloatBoundaryOrCallerDeadline_ChildTimeoutRespectsCap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock_value: float,
    deadline_monotonic: float | None,
    expected_timeout: float,
) -> None:
    calls: list[float] = []
    cache_root = tmp_path / "receipts"
    real_run = real_subprocess.CompletedProcess

    def fetch(
        command: list[str], *, timeout: float, **kwargs: object
    ) -> real_subprocess.CompletedProcess[bytes]:
        calls.append(timeout)
        return real_run(command, 0, b'{"name":"dep","version":"1"}', b"")

    monkeypatch.setattr(
        registry_grounding,
        "time",
        SimpleNamespace(monotonic=lambda: clock_value, time=lambda: 1_800_000_000.0),
    )
    monkeypatch.setattr(registry_grounding, "_cache_root", lambda: cache_root)
    monkeypatch.setattr(
        registry_grounding,
        "subprocess",
        SimpleNamespace(run=fetch, TimeoutExpired=real_subprocess.TimeoutExpired),
    )

    result = registry_grounding.registry_receipts(
        [{"ecosystem": "npm", "name": "dep"}], deadline_monotonic=deadline_monotonic
    )

    assert calls == [expected_timeout]
    assert result["receipts"][0]["status"] == "fetched"
