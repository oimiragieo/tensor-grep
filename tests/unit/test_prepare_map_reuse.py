from __future__ import annotations

import json
from pathlib import Path

import tensor_grep.cli.repo_map as repo_map_module
from tensor_grep.cli.session_resume_service import session_prepare
from tensor_grep.cli.session_store import _session_payload_path, open_session

# P9 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 06): "Extract a
# prepare-from-map service. Keep the existing cold wrapper building a map once. Pass only a
# map whose identity/freshness has been checked by the owning session service." Accept
# criterion: "a spy control: unchanged warm prepare makes zero full build_repo_map calls."
#
# The spy patches `build_repo_map` at its ORIGIN module (`tensor_grep.cli.repo_map`) -- per
# this repo's bare-call-ratchet discipline, never at a bare name re-imported into
# `prepare_service.py` / `session_resume_service.py`, which is exactly the trap two other PRs
# hit earlier the same day this task was written.


class _BuildRepoMapSpy:
    def __init__(self, original: object) -> None:
        self._original = original
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._original(*args, **kwargs)  # type: ignore[operator]

    def reset(self) -> None:
        self.calls = 0


def _install_spy(monkeypatch: object) -> _BuildRepoMapSpy:
    original = repo_map_module.build_repo_map
    spy = _BuildRepoMapSpy(original)
    monkeypatch.setattr(repo_map_module, "build_repo_map", spy)  # type: ignore[attr-defined]
    return spy


def test_warm_prepare_makes_zero_full_build_repo_map_calls(tmp_path: Path, monkeypatch) -> None:
    """DEVIATION from the literal step-by-step in the task brief, noted here rather than
    silently: the brief's step (a) expected the FIRST `session_prepare` call after `open_session`
    to still fire `build_repo_map` (treating only the second, repeated call as "warm"). Measured
    reality is stronger than that: `open_session` (session_store.py) stamps
    `current_generation = _snapshot_generation(snapshot)` against the SAME snapshot it just
    captured, so the freshness check this task adds already holds immediately after open --
    there is no "cold" `session_prepare` call to observe once a session exists. `open_session`'s
    own internal `build_repo_map` call is also not observable through this spy: `session_store.py`
    binds `build_repo_map` as a bare module-level import (an existing, out-of-scope pattern), so
    patching `tensor_grep.cli.repo_map.build_repo_map` cannot intercept it -- only
    `prepare_service.py`'s per-call local import (this task's own new code) is reachable this
    way, which is the correct half to spy on for this accept criterion.
    """
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    spy = _install_spy(monkeypatch)

    open_result = open_session(str(tmp_path))
    session_id = open_result.session_id

    # (a) First prepare on a freshly-opened session is ALREADY warm (see docstring above):
    # zero full build_repo_map calls from prepare_service's own path.
    first = session_prepare(session_id, "add numbers", str(tmp_path))
    assert first["session_id"] == session_id
    assert spy.calls == 0

    # (b) Prepare AGAIN on the SAME session with no file changes in between. Still zero calls:
    # this is the accept criterion itself, "unchanged warm prepare makes zero full
    # build_repo_map calls."
    spy.reset()
    second = session_prepare(session_id, "add numbers", str(tmp_path))
    assert second["session_id"] == session_id
    assert spy.calls == 0, (
        "warm session_prepare must reuse the session's own repo_map, not rebuild it "
        f"(spy.calls={spy.calls})"
    )


def test_prepare_never_serves_a_map_whose_freshness_stamp_has_drifted(
    tmp_path: Path, monkeypatch
) -> None:
    """The other half of the accept criterion: never silently serve a stale map. Simulate a
    payload whose `current_generation` stamp no longer matches its own `snapshot` field (the
    exact identity AGT-02's `_snapshot_generation` guards) by corrupting the persisted session
    payload directly -- a real on-disk file modification to a TRACKED path is already caught
    earlier, by the pre-existing unconditional `_ensure_session_not_stale` check inside
    `_load_session_payload` (session_store.py), which raises `SessionStaleError` before this
    task's new freshness-reuse logic ever runs. That guard is out of scope here; this test
    isolates the new comparison this task adds."""
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    spy = _install_spy(monkeypatch)

    open_result = open_session(str(tmp_path))
    session_id = open_result.session_id
    session_path = _session_payload_path(tmp_path.resolve(), session_id)

    first = session_prepare(session_id, "add numbers", str(tmp_path))
    assert first["session_id"] == session_id

    # Corrupt the stamped generation so it no longer matches a fresh recomputation of
    # `_snapshot_generation(payload["snapshot"])` -- the exact drift this task's freshness
    # check must catch and refuse to trust.
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["current_generation"] = "0" * 32
    session_path.write_text(json.dumps(payload), encoding="utf-8")

    spy.reset()
    second = session_prepare(session_id, "add numbers", str(tmp_path))
    assert second["session_id"] == session_id
    assert spy.calls >= 1, (
        "a drifted current_generation stamp must fall back to a cold rebuild, never serve the "
        "stale-relative-to-its-own-stamp repo_map"
    )
