from __future__ import annotations

import subprocess
import time

from tensor_grep.cli import subprocess_policy


def test_ripgrep_timeout_defaults_to_60s(monkeypatch) -> None:
    # Fail-fast default: ripgrep does GB/s, so a >60s search is pathological; an agent must never
    # hang ~10 minutes (the old 600s default) before getting an actionable error.
    monkeypatch.delenv("TG_RG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 60.0


def test_ripgrep_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TG_RG_TIMEOUT_SECONDS", "120")
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 120.0


def test_run_subprocess_honors_timeout(monkeypatch) -> None:
    monkeypatch.setenv("TG_SUBPROCESS_TIMEOUT_SECONDS", "1")

    try:
        subprocess_policy.run_subprocess(
            ["python", "-c", "import time; time.sleep(5)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return

    raise AssertionError("expected subprocess timeout")


# ---------------------------------------------------------------------------
# deadline_capped_timeout_seconds: tg-codemap 90s-timeout root cause. A subprocess call (e.g.
# git status/rev-parse/ls-files) is atomic -- no per-iteration deadline check is possible
# mid-call -- so the only lever to keep it from blowing a caller's --deadline is capping the
# timeout PASSED IN before the call starts. Deterministic via monkeypatched time.monotonic
# (anti-hang-test-protocol: never a real sleep/wall-clock race).
# ---------------------------------------------------------------------------


def test_deadline_capped_timeout_none_deadline_is_byte_identical_noop() -> None:
    # Every pre-existing caller passes deadline_monotonic=None (the default) -- must return
    # base_timeout_seconds completely unchanged, not merely equal by value coincidence.
    assert (
        subprocess_policy.deadline_capped_timeout_seconds(120.0, deadline_monotonic=None) == 120.0
    )
    assert subprocess_policy.deadline_capped_timeout_seconds(0.5, deadline_monotonic=None) == 0.5


def test_deadline_capped_timeout_caps_to_remaining_budget(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 + 5.0  # 5s of real budget remains

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped == 5.0, "must cap to the remaining budget, not the 120s git-timeout default"


def test_deadline_capped_timeout_never_exceeds_base_when_budget_is_generous(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 + 500.0  # ample budget remains, far more than base_timeout

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped == 120.0, "must not WIDEN the timeout past the configured base"


def test_deadline_capped_timeout_already_expired_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 - 1.0  # already 1s past deadline

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped is None, "an already-expired deadline must signal 'skip the call', not 0/negative"


def test_deadline_capped_timeout_exactly_at_deadline_returns_none(monkeypatch) -> None:
    # remaining == 0 must degrade the same as remaining < 0 (never invoke a subprocess with a
    # non-positive timeout -- subprocess.run rejects timeout<=0 outright).
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)

    capped = subprocess_policy.deadline_capped_timeout_seconds(120.0, deadline_monotonic=1000.0)

    assert capped is None
