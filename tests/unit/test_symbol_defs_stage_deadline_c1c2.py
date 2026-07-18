"""C1/C2 audit: ``build_symbol_defs_from_map`` is called BARE (no ``deadline_monotonic``) as the
stage-1 processing step inside ``build_symbol_defs`` (the cold ``tg defs`` wrapper) and its 5
sibling ``_from_map`` builders (impact/refs/callers/blast-radius/source), so its internal
``_relevant_tests_for_symbol`` scan (repo_map.py:3812, the "related" loop ~3919-3947) runs
unbounded regardless of ``--deadline``. Live repros on origin/main (e2553aa): ``tg defs search
--deadline 40`` -> 113.5s, exit 0, ``partial:null`` (silent 3x overrun); ``tg impact search
--deadline 1`` -> 85.4s; refs 47.4s; callers 20.2s; source 47.0s.

This mirrors ``test_refs_context_pack_deadline_205.py``'s spy pattern: the sibling builders'
OWN sibling-loop deadline checks (refs' bounded_files scan, callers'/impact's existing
fold-ins, blast-radius' nested callers_payload/impact_payload sub-calls) are ALREADY
deadline-aware and would trip ``partial=True`` on an already-expired deadline independent of
this bug -- a naive "expired-deadline -> assert partial" value test on those OUTER functions
would therefore pass VACUOUSLY whether or not this fix is applied (see that file's docstring).
We isolate the fix with spy tests asserting the SPECIFIC bare ``build_symbol_defs_from_map``
call now forwards ``deadline_monotonic``, plus one confound-free end-to-end fold-in assertion
on ``source`` (whose only internal time-sensitive stage IS the defs call) and a dedicated
backstop test for the new C1 defense-in-depth return-time recheck on ``build_symbol_defs``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map


def _project_with_test(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_m.py").write_text(
        "from m import helper\n\n\ndef test_helper():\n    assert helper() == 1\n",
        encoding="utf-8",
    )
    return project.resolve()


def _spy_call_through(monkeypatch: Any, target_name: str) -> list[dict[str, Any]]:
    """Wrap ``repo_map.<target_name>`` with a call-recording spy that still calls through to the
    real implementation (mirrors test_refs_context_pack_deadline_205.py's ``_spy``)."""
    captured_calls: list[dict[str, Any]] = []
    original = getattr(repo_map, target_name)

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        captured_calls.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, target_name, _wrapped)
    return captured_calls


# ---------------------------------------------------------------------------
# Site 1: build_symbol_defs (cold wrapper, repo_map.py:14615-14637)
# ---------------------------------------------------------------------------


def test_defs_cold_wrapper_threads_deadline_into_from_map(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    sentinel = time.monotonic() + 10_000.0
    monkeypatch.setattr(repo_map, "_deadline_monotonic_from_seconds", lambda seconds: sentinel)

    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")
    repo_map.build_symbol_defs("helper", str(project), deadline_seconds=30.0)

    assert captured_calls, "build_symbol_defs_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel


def test_defs_cold_wrapper_backstop_catches_late_return(tmp_path: Path, monkeypatch: Any) -> None:
    """C1 defense-in-depth: mirrors build_context_pack's #642-style return-time recheck
    (repo_map.py:8380-8392) -- even when build_symbol_defs_from_map's OWN return carries no
    partial signal (every stage it ran completed "successfully"), build_symbol_defs must still
    flag the response partial if the shared deadline was already blown by return time, so a slow
    stage this function doesn't specifically instrument can never silently produce exit 0."""
    project = tmp_path / "project"
    project.mkdir()
    fake_repo_map_result: dict[str, Any] = {
        "path": str(project),
        "files": [],
        "symbols": [],
        "imports": [],
        "tests": [],
        "related_paths": [],
    }
    fake_defs_result: dict[str, Any] = {
        "path": str(project),
        "definitions": [],
        "no_match": True,
        "message": "No exact definition found for symbol 'helper'.",
        "files": [],
        "symbols": [],
        "imports": [],
        "tests": [],
        "related_paths": [],
    }
    # Both collaborators are fully mocked (neither ever sets "partial") so the ONLY way the
    # final result can become partial is the NEW return-time backstop in build_symbol_defs.
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *a, **k: dict(fake_repo_map_result))
    monkeypatch.setattr(
        repo_map, "build_symbol_defs_from_map", lambda *a, **k: dict(fake_defs_result)
    )

    result = repo_map.build_symbol_defs("helper", str(project), deadline_seconds=-1000.0)

    assert result.get("partial") is True
    assert isinstance(result.get("deadline_limit"), dict)
    assert result["deadline_limit"].get("deadline_exceeded") is True


# ---------------------------------------------------------------------------
# Site 2: build_symbol_impact_from_map (repo_map.py:14970-14979)
# ---------------------------------------------------------------------------


def test_impact_threads_deadline_into_defs_from_map(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")

    sentinel = time.monotonic() + 10_000.0
    repo_map.build_symbol_impact_from_map(rmap, "helper", deadline_monotonic=sentinel)

    assert captured_calls, "build_symbol_defs_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel


# ---------------------------------------------------------------------------
# Site 3: build_symbol_refs_from_map (repo_map.py:15311-15319)
# ---------------------------------------------------------------------------


def test_refs_threads_deadline_into_defs_from_map(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")

    sentinel = time.monotonic() + 10_000.0
    repo_map.build_symbol_refs_from_map(rmap, "helper", deadline_monotonic=sentinel)

    assert captured_calls, "build_symbol_defs_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel


# ---------------------------------------------------------------------------
# Site 4: build_symbol_callers_from_map (repo_map.py:16104-16113)
# ---------------------------------------------------------------------------


def test_callers_threads_deadline_into_defs_from_map(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")

    sentinel = time.monotonic() + 10_000.0
    repo_map.build_symbol_callers_from_map(rmap, "helper", deadline_monotonic=sentinel)

    assert captured_calls, "build_symbol_defs_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel


# ---------------------------------------------------------------------------
# Site 5: build_symbol_blast_radius_from_map (repo_map.py:16802-16811)
# ---------------------------------------------------------------------------


def test_blast_radius_threads_deadline_into_defs_from_map(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")

    sentinel = time.monotonic() + 10_000.0
    repo_map.build_symbol_blast_radius_from_map(rmap, "helper", deadline_monotonic=sentinel)

    # blast-radius calls build_symbol_defs_from_map directly AND transitively (via its own
    # callers_payload/impact_payload sub-calls) -- every one of those calls must carry the SAME
    # shared deadline, never a dropped/None one.
    assert captured_calls, "build_symbol_defs_from_map was never called"
    for captured in captured_calls:
        assert captured.get("deadline_monotonic") == sentinel


def test_blast_radius_folds_own_defs_partial_signal(tmp_path: Path, monkeypatch: Any) -> None:
    """Additive fold-in (site 5): blast-radius' own DIRECT defs_payload call (the first of the 3
    build_symbol_defs_from_map calls this function triggers) must be OR'd into blast-radius' own
    partial stamp, not just callers_payload/impact_payload's (pre-existing) partial signals."""
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    original = repo_map.build_symbol_defs_from_map
    call_count = {"n": 0}

    def _only_first_call_partial(rm: Any, symbol: str, **kwargs: Any) -> Any:
        call_count["n"] += 1
        result = original(rm, symbol, **kwargs)
        if call_count["n"] == 1:
            # Simulate ONLY blast-radius's own direct stage-1 defs call (repo_map.py:16811)
            # overrunning; leave callers_payload's/impact_payload's OWN nested defs calls
            # (2nd/3rd, called later in this function) clean -- isolates the NEW
            # `defs_payload.get("partial")` fold-in this fix adds from the PRE-EXISTING
            # callers_payload/impact_payload partial propagation.
            result["partial"] = True
            result.setdefault("deadline_limit", {"deadline_exceeded": True})
        return result

    monkeypatch.setattr(repo_map, "build_symbol_defs_from_map", _only_first_call_partial)

    result = repo_map.build_symbol_blast_radius_from_map(rmap, "helper")

    assert call_count["n"] == 3, "expected exactly 3 nested build_symbol_defs_from_map calls"
    assert result.get("partial") is True
    assert isinstance(result.get("deadline_limit"), dict)


# ---------------------------------------------------------------------------
# Sites 6 & 7: build_symbol_source_from_map gains deadline_monotonic (repo_map.py:14858-14865)
# and build_symbol_source threads its own deadline into it (repo_map.py:14829-14855)
# ---------------------------------------------------------------------------


def test_source_from_map_accepts_and_threads_deadline(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    captured_calls = _spy_call_through(monkeypatch, "build_symbol_defs_from_map")

    sentinel = time.monotonic() + 10_000.0
    # Pre-fix this raises TypeError: build_symbol_source_from_map() got an unexpected keyword
    # argument 'deadline_monotonic' -- the missing parameter itself IS the site-6 gap.
    repo_map.build_symbol_source_from_map(rmap, "helper", deadline_monotonic=sentinel)

    assert captured_calls, "build_symbol_defs_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel


def test_source_from_map_folds_defs_partial_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    """Confound-free end-to-end assertion (task requirement): unlike impact/refs/callers/
    blast-radius, build_symbol_source_from_map has NO sibling deadline-aware loop of its own --
    its entire time-sensitivity is inherited from the defs stage. An already-expired deadline can
    therefore only produce partial:true here if the defs-stage threading fix actually works,
    never vacuously from a pre-existing unrelated loop."""
    project = _project_with_test(tmp_path)
    rmap = repo_map.build_repo_map(str(project))  # built WITHOUT a deadline: guaranteed non-partial
    assert not rmap.get("partial"), "fixture repo_map must start non-partial for this to be valid"

    already_expired = time.monotonic() - 1.0
    result = repo_map.build_symbol_source_from_map(
        rmap, "helper", deadline_monotonic=already_expired
    )

    assert result.get("partial") is True
    assert isinstance(result.get("deadline_limit"), dict)


def test_source_cold_wrapper_threads_deadline_into_from_map(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _project_with_test(tmp_path)
    sentinel = time.monotonic() + 10_000.0
    monkeypatch.setattr(repo_map, "_deadline_monotonic_from_seconds", lambda seconds: sentinel)

    captured_calls = _spy_call_through(monkeypatch, "build_symbol_source_from_map")
    repo_map.build_symbol_source("helper", str(project), deadline_seconds=30.0)

    assert captured_calls, "build_symbol_source_from_map was never called"
    assert captured_calls[0].get("deadline_monotonic") == sentinel
