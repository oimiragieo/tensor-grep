"""Cold-path assembly-tail SLA fix (#220): `_detect_vendored_subtrees` /
`_suggested_scope_from_map` honor an already-anchored `deadline_monotonic` budget.

Root cause this closes: `tg agent <root> <query> --deadline N` (and the same-shaped `--deadline`
on the cold path's own F4 60s default) threads `deadline_monotonic` through the COLLECTION stage
(`build_repo_map`'s file walk + parse loop, `repo_map.py`) correctly, but two ASSEMBLY stages ran
unconditionally regardless of whether the shared budget was already exhausted by the time they
started: `_detect_vendored_subtrees` (called from BOTH `agent_capsule.build_agent_capsule_from_map`
AND `repo_map._build_context_pack_from_map`'s own `auto_deweight` pass -- so its cost, profiled at
~1.2s PER CALL on a 25k-file/40-sibling-project synthetic tree, was paid TWICE per `tg agent` call)
and `_suggested_scope_from_map` (the centrality rollup). Real-binary wall-to-exit proof lives in
`tests/integration/test_agent_cold_deadline_tail_sla_220.py`; this file proves the MECHANISM at
the function level: fast, deterministic, no subprocess.

Four properties, mirroring the `_detect_vendored_subtrees`/`_suggested_scope_from_map`
docstrings' own contract:
  1. An ALREADY-EXCEEDED `deadline_monotonic` skips the expensive work and returns the SAME
     "nothing to report" shape the function already used for its other bail-out cases (`{}` /
     `None`) -- no new return shape, no exception.
  2. `deadline_monotonic=None` (omitted) and a `deadline_monotonic` comfortably in the FUTURE
     produce IDENTICAL output to each other -- the no-deadline-pressure path is byte-for-byte
     unaffected by this fix (the explicit regression guard the build task asked for).
  3. `_detect_vendored_subtrees`'s own STRONG-1 manifest-probe loop and outermost-nested-chain
     dedup loop each check the deadline PER ITERATION, not just once at function entry -- an
     entry-only check was proven insufficient during this fix's own calibration: a single
     uninterrupted call measured at ~3.6s even when ~2.3s of budget remained at entry (a
     manifest-dense repo's post-deadline wall-to-exit only dropped from ~9.3s to ~5.5s with an
     entry-only check, and to ~3.4s once the internal loops were also bounded -- see
     `tests/integration/test_agent_cold_deadline_tail_sla_220.py`'s module docstring for the
     real-binary numbers).
  4. An independent Opus gate on the resulting PR (#669) found ONE MORE un-gated span this fix
     initially missed: between the manifest-probe loop's check and the dedup loop's check sits
     the reverse-import-graph re-derivation (`_code_files_and_import_graph`) AND the STRONG-3
     skill-leaf validation loop (`_is_skill_leaf_tree`) that consumes it -- measured on the real
     workspace that motivated this fix at 241 skills-named directories / 2,933 child directories,
     ~0.69s warm / low-seconds cold, un-gated by either neighboring check. A 4th checkpoint
     (right after the manifest loop, before the import-graph call) closes this.
  All internal-loop/mid-function checks above are forced with a monkeypatched `time.monotonic()`
  so they are deterministic (no reliance on a real clock crossing a real deadline mid-loop, which
  would make the test flaky by construction).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli.orient_capsule import _detect_vendored_subtrees, _suggested_scope_from_map

_FAR_FUTURE_DEADLINE_S = 3600.0  # +1h: "ample budget", never crossed by a fast unit test


def _rm_with_vendored_subtree(root: Path) -> dict[str, Any]:
    """A repo map that DOES trigger `_detect_vendored_subtrees` (STRONG-1 manifest + STRONG-2
    import-island, mirroring `tests/unit/test_orient_deweight_vendored.py`'s
    `test_fires_on_manifest_plus_import_island`) -- so a deadline-skip is a genuine, observable
    behavior change versus "there was nothing to detect anyway"."""
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    return {
        "path": str(root),
        "files": [
            str(root / "app.py"),
            str(root / "bundled" / "lib.py"),
            str(root / "bundled" / "helper.py"),
        ],
        "imports": [{"file": str(root / "bundled" / "lib.py"), "imports": ["helper"]}],
        "symbols": [],
    }


def _rm_with_clear_scope_winner() -> dict[str, Any]:
    """Mirrors `test_orient_suggested_scope.py::test_suggested_scope_picks_clear_centrality_winner`
    -- a repo map with an unambiguous centrality winner, so a deadline-skip (-> None) is a genuine
    behavior change versus "there was no clear winner anyway"."""
    root = Path("/repo")
    return {
        "path": str(root),
        "files": [
            str(root / "core" / "hub.py"),
            str(root / "core" / "a.py"),
            str(root / "core" / "b.py"),
            str(root / "misc" / "lonely.py"),
        ],
        "imports": [
            {"file": str(root / "core" / "a.py"), "imports": ["hub"]},
            {"file": str(root / "core" / "b.py"), "imports": ["hub"]},
        ],
        "symbols": [
            {"name": f"Sym{i}", "kind": "function", "file": str(root / "core" / "hub.py")}
            for i in range(6)
        ],
    }


# ---------------------------------------------------------------------------------------------
# _detect_vendored_subtrees
# ---------------------------------------------------------------------------------------------


def test_detect_vendored_subtrees_skips_when_deadline_already_exceeded(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    rm = _rm_with_vendored_subtree(root)
    # Sanity: this fixture DOES fire without a deadline (proves the skip below is a real effect).
    assert _detect_vendored_subtrees(rm) != {}

    already_past = time.monotonic() - 1.0
    result = _detect_vendored_subtrees(rm, deadline_monotonic=already_past)

    # Same "nothing to report" shape the function already returns for e.g. no signal at all --
    # additive de-weight evidence, never hard-exclude, so skipping it changes ranking inputs only,
    # never correctness. No exception, no partial/half-built dict.
    assert result == {}


def test_detect_vendored_subtrees_no_pressure_path_unchanged(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    rm = _rm_with_vendored_subtree(root)

    omitted = _detect_vendored_subtrees(rm)
    far_future = _detect_vendored_subtrees(
        rm, deadline_monotonic=time.monotonic() + _FAR_FUTURE_DEADLINE_S
    )

    # Byte-identical: the new parameter is a true no-op until the budget is actually exhausted.
    assert omitted == far_future
    assert omitted != {}  # (both non-trivial -- see the skip test's sanity check)


# ---------------------------------------------------------------------------------------------
# _suggested_scope_from_map
# ---------------------------------------------------------------------------------------------


def test_suggested_scope_from_map_skips_when_deadline_already_exceeded() -> None:
    rm = _rm_with_clear_scope_winner()
    # Sanity: this fixture DOES produce a clear winner without a deadline.
    assert _suggested_scope_from_map(rm) is not None

    already_past = time.monotonic() - 1.0
    result = _suggested_scope_from_map(rm, deadline_monotonic=already_past)

    # Same "no suggestion" shape the function already returns for a flat/tied/signal-free repo.
    assert result is None


def test_suggested_scope_from_map_no_pressure_path_unchanged() -> None:
    rm = _rm_with_clear_scope_winner()

    omitted = _suggested_scope_from_map(rm)
    far_future = _suggested_scope_from_map(
        rm, deadline_monotonic=time.monotonic() + _FAR_FUTURE_DEADLINE_S
    )

    assert omitted == far_future
    assert omitted is not None


def test_suggested_scope_from_map_deadline_check_precedes_deweighted_trees_lookup() -> None:
    """The deadline check must be the FIRST thing the function does (before the `_file_centrality_
    scores` call it exists to bound) -- pass an intentionally-malformed `deweighted_trees` (a type
    that would raise if ever touched) to prove the already-past-deadline branch returns before
    doing ANY further work, not just before the expensive part."""
    rm = _rm_with_clear_scope_winner()
    already_past = time.monotonic() - 1.0

    class _ExplodingMapping(dict):
        def keys(self) -> Any:  # pragma: no cover - must never be called
            raise AssertionError("deweighted_trees.keys() was reached past an expired deadline")

    result = _suggested_scope_from_map(
        rm,
        deweighted_trees=_ExplodingMapping(),
        deadline_monotonic=already_past,
    )
    assert result is None


# ---------------------------------------------------------------------------------------------
# _detect_vendored_subtrees's internal loops each have their OWN per-iteration deadline check
# (not just the function's entry check) -- deterministic via a monkeypatched clock, never a real
# wall-clock race.
# ---------------------------------------------------------------------------------------------


def _many_manifest_dirs(root: Path, count: int) -> dict[str, Any]:
    """`count` sibling STRONG-0 `_vendored` dirs at distinct paths (`group0/_vendored`,
    `group1/_vendored`, ...) -- STRONG-0 fires ALONE (see the module comment above
    `_DEWEIGHT_FACTOR` in orient_capsule.py: an unambiguous vendor NAME needs no manifest and no
    import-island), so this fixture stresses BOTH internal loops (each `_vendored` dir is a
    `candidate_dir` the manifest probe iterates, and a `strong0_vendor_dirs` member the outermost
    dedup iterates) without needing real cross-file imports to satisfy STRONG-2."""
    files = []
    for i in range(count):
        group = root / f"group{i}"
        vendored = group / "_vendored"
        vendored.mkdir(parents=True)
        (vendored / "lib.py").write_text("x = 1\n", encoding="utf-8")
        files.append(str(vendored / "lib.py"))
    return {"path": str(root), "files": files, "imports": [], "symbols": []}


class _ThenPastDeadlineClock:
    """A fake `time.monotonic()`: the Nth call (1-indexed) onward reports a timestamp already past
    `deadline_monotonic`; every call before that reports 0.0 (always "before"). Lets a test force
    a break at a SPECIFIC call site (e.g. call #2 = right after the function's own entry check, so
    any break can only come from the loop's OWN per-iteration check, not the entry check) without
    depending on real wall-clock timing or exact internal call counts beyond that one boundary."""

    def __init__(self, past_deadline_from_call: int) -> None:
        self.past_deadline_from_call = past_deadline_from_call
        self.call_count = 0

    def __call__(self) -> float:
        self.call_count += 1
        return 1_000_000.0 if self.call_count >= self.past_deadline_from_call else 0.0


def test_detect_vendored_subtrees_manifest_probe_loop_has_own_deadline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    rm = _many_manifest_dirs(root, 20)
    # Sanity: unpatched, this fixture DOES detect all 20 `_vendored` trees.
    assert len(_detect_vendored_subtrees(rm)) == 20

    # Call #1 is this function's OWN entry check (see its docstring) -- report "before the
    # deadline" so it does NOT skip immediately. Call #2 onward (inside the manifest-probe loop,
    # the very next thing the function does) reports "already past" -- so a break can ONLY come
    # from that loop's own per-iteration check.
    clock = _ThenPastDeadlineClock(past_deadline_from_call=2)
    monkeypatch.setattr("tensor_grep.cli.orient_capsule.time.monotonic", clock)
    deadline_hit = _repo_map._DeadlineBreakFlag()

    # Strictly between the clock's "before" (0.0) and "after" (1_000_000.0) sentinels -- 0.0 would
    # make call #1 (`0.0 >= 0.0`) ALSO read as "past deadline", tripping the entry check instead
    # of the loop's own check and making this test pass for the wrong reason.
    result = _detect_vendored_subtrees(rm, deadline_monotonic=500_000.0, deadline_hit=deadline_hit)

    assert deadline_hit.hit is True
    # Broke on/near the first loop iteration -- far fewer than the full 20 (proves it did NOT run
    # to completion), and no exception / malformed partial result.
    assert len(result) < 20
    assert isinstance(result, dict)


def test_detect_vendored_subtrees_outermost_dedup_loop_has_own_deadline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    rm = _many_manifest_dirs(root, 20)
    real_monotonic = time.monotonic
    # `deadline` is captured ONCE and every "already past" sentinel below is `deadline + 1.0` --
    # deliberately NOT a hardcoded absolute timestamp: `time.monotonic()`'s epoch is
    # platform-defined (often system boot on Windows), so a long-uptime shared box could already
    # exceed a hardcoded "large" number, silently breaking the "still before deadline" branch too.
    deadline = real_monotonic() + 3600.0

    # Self-calibrating flip point (not a hardcoded magic number): first count exactly how many
    # `time.monotonic()` calls a FULL, uninterrupted run makes for this fixture (entry check +
    # manifest-probe loop + outermost-dedup loop, in that order -- the dedup loop is always LAST).
    # Flipping at the 75% mark of that total reliably lands inside the dedup loop's own span
    # without hardcoding either loop's exact iteration count, so this test stays meaningful even
    # if a future change alters how many calls either phase makes.
    total_calls = 0

    def counting_clock() -> float:
        nonlocal total_calls
        total_calls += 1
        return real_monotonic()

    monkeypatch.setattr("tensor_grep.cli.orient_capsule.time.monotonic", counting_clock)
    full_result = _detect_vendored_subtrees(rm, deadline_monotonic=deadline)
    assert len(full_result) == 20, "fixture assumption drifted -- recheck _many_manifest_dirs"

    flip_after_calls = (total_calls * 3) // 4
    call_count = 0

    def clock() -> float:
        nonlocal call_count
        call_count += 1
        return deadline + 1.0 if call_count > flip_after_calls else real_monotonic()

    monkeypatch.setattr("tensor_grep.cli.orient_capsule.time.monotonic", clock)
    deadline_hit = _repo_map._DeadlineBreakFlag()

    result = _detect_vendored_subtrees(rm, deadline_monotonic=deadline, deadline_hit=deadline_hit)

    assert deadline_hit.hit is True
    assert isinstance(result, dict)
    # A partial dedup pass can at most equal the full result (never MORE trees than a complete
    # pass would find -- see the function's own docstring on why an incomplete dedup is safe).
    assert len(result) <= 20


def test_detect_vendored_subtrees_post_manifest_loop_gate_skips_import_graph_and_skill_leaf_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#220 Opus-gate follow-up (independent gate on PR #669, real-workspace-measured): between
    the manifest-probe loop's own per-iteration check and the outermost-dedup loop's own
    per-iteration check sat an UN-GATED middle section -- the reverse-import-graph re-derivation
    (`_code_files_and_import_graph`) AND the STRONG-3 skill-leaf validation loop
    (`_is_skill_leaf_tree`, `iterdir()`-heavy per `skills`-named candidate) that consumes it. The
    gate measured this on the real workspace that motivated this fix: 241 skills-named
    directories / 2,933 child directories -> ~0.69s warm, low-seconds cold, entirely un-gated by
    either neighboring check. Proven here with an exploding sentinel on
    `_code_files_and_import_graph` -- unambiguous proof of reachability (not just "the deadline
    check ran," but "the expensive call itself was never invoked") -- combined with the same
    monkeypatched-clock determinism as the sibling loop tests above (never a real clock racing a
    real deadline)."""
    root = tmp_path.resolve()
    # STRONG-0 `_vendored` dirs make `strong0_vendor_dirs` non-empty (so the earlier "nothing
    # found at all" bail-out does not fire for an unrelated reason) AND a `skills/` dir with
    # leaf-shaped children (mirrors test_orient_deweight_vendored.py's "many leaf skills" shape)
    # makes `skill_candidate_dirs` non-empty too -- the skipped section would genuinely do real
    # work absent this fix, not a vacuous no-op.
    files = []
    for i in range(10):
        vendored = root / f"proj{i}" / "_vendored"
        vendored.mkdir(parents=True)
        (vendored / "lib.py").write_text("x = 1\n", encoding="utf-8")
        files.append(str(vendored / "lib.py"))
    for leaf in ("leaf-a", "leaf-b"):
        leaf_dir = root / "skills" / leaf
        leaf_dir.mkdir(parents=True)
        (leaf_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        files.append(str(leaf_dir / "SKILL.md"))
    rm = {"path": str(root), "files": files, "imports": [], "symbols": []}

    real_monotonic = time.monotonic
    deadline = real_monotonic() + 3600.0

    class _ImportGraphReached(Exception):
        pass

    sentinel_calls = 0

    def exploding_import_graph(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal sentinel_calls
        sentinel_calls += 1
        raise _ImportGraphReached

    monkeypatch.setattr(
        "tensor_grep.cli.orient_capsule._code_files_and_import_graph", exploding_import_graph
    )

    # Phase 1: self-calibrate AND sanity-check in one pass. A counting (real-value) clock with an
    # ample deadline -- `_code_files_and_import_graph` MUST be reached (proves this fixture's
    # skipped section is genuinely non-vacuous), and the call count at the moment it fires is
    # exactly "1 entry check + N manifest-loop-iteration checks + 1 post-manifest-loop check" --
    # the flip boundary phase 2 needs, without hardcoding or re-deriving `len(candidate_dirs)`.
    total_calls_before_import_graph = 0

    def counting_clock() -> float:
        nonlocal total_calls_before_import_graph
        total_calls_before_import_graph += 1
        return real_monotonic()

    monkeypatch.setattr("tensor_grep.cli.orient_capsule.time.monotonic", counting_clock)
    with pytest.raises(_ImportGraphReached):
        _detect_vendored_subtrees(rm, deadline_monotonic=deadline)
    assert sentinel_calls == 1, "fixture assumption drifted -- the skipped section is not reached"

    # Phase 2: the actual regression guard. Flip `time.monotonic()` to "already past deadline"
    # starting at the EXACT call phase 1 measured -- the manifest-probe loop's own per-iteration
    # check (point 2 in the function's docstring) never sees "past" here, so it completes ALL its
    # iterations normally (proving this test isolates the POST-loop check, point 3, not a
    # mid-loop break -- that's already covered by
    # test_detect_vendored_subtrees_manifest_probe_loop_has_own_deadline_check above).
    sentinel_calls = 0
    call_count = 0
    flip_at_call = total_calls_before_import_graph

    def clock() -> float:
        nonlocal call_count
        call_count += 1
        return deadline + 1.0 if call_count >= flip_at_call else real_monotonic()

    monkeypatch.setattr("tensor_grep.cli.orient_capsule.time.monotonic", clock)
    deadline_hit = _repo_map._DeadlineBreakFlag()

    result = _detect_vendored_subtrees(rm, deadline_monotonic=deadline, deadline_hit=deadline_hit)

    assert sentinel_calls == 0, (
        "_code_files_and_import_graph ran even though the deadline had already tripped by the "
        "post-manifest-loop check -- the #220 Opus-gate middle-section gate regressed"
    )
    assert deadline_hit.hit is True
    assert result == {}
