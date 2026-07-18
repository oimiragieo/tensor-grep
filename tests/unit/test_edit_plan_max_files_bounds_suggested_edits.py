"""Audit finding B9/A18 (convergently found by two independent audit lenses): `tg edit-plan --json
--max-files N` visibly wired `max_edits=max_files` into `_suggested_edits_from_related_spans`
(repo_map.py) but the callee never read `max_edits` to bound the returned `suggestions` list -- a
flag-lie, the same class of bug as the #200/#203/#205 deadline gaps. `edit_plan_seed.suggested_edits`
could grow unbounded despite the caller (and the user) believing it was capped at `--max-files`.

Fixed by `_capped_suggested_edits` (the enforcement mechanism) plus a new opt-in
`suggested_edits_max` parameter threaded `build_context_edit_plan_from_map` ->
`_attach_edit_plan_metadata` -> `_build_edit_plan_seed` -> `_suggested_edits_from_related_spans`.

VERIFY-FIRST CORRECTION (found while writing this test, not assumed from the audit): the audit
described `_compact_edit_plan_seed` as an "existing cap" for `tg context-render`, but that helper
only runs for `render_profile in {"compact", "llm"}` (`_COMPACT_CONTEXT_RENDER_PROFILES`,
repo_map.py) -- NOT the default `"full"` profile. `tg context-render`'s default profile therefore
carried the IDENTICAL unbounded-suggested_edits bug as edit-plan, as do `tg blast-radius-plan` and
`tg blast-radius-render` (neither calls `_compact_edit_plan_seed` at all). Binding the fix at the
single shared source (`_suggested_edits_from_related_spans`) unconditionally would have silently
changed all four commands' output -- wider than the "ONE correctness fix" for edit-plan this PR
ships and a direct violation of "do not change the context-render output". The fix is therefore
OPT-IN: `suggested_edits_max` defaults to `None` (unbounded, byte-identical to every caller's
pre-fix behavior) everywhere, and only `build_context_edit_plan_from_map` (edit-plan's own top-level
builder -- never shared with context-render or blast-radius-plan/render) passes a real value. The
context-render/blast-radius-plan/blast-radius-render gaps are real and share the same root cause,
but are deliberately left unfixed here as an out-of-scope, separately-reviewable follow-up.

RESOLVED by #212 (a follow-up audit to this one): dogfooding on tensor-grep's own repo confirmed the
gap was real and user-visible (`--max-files 1` on `blast-radius-render` returned `files=[1]` but
`suggested_edits` spanning 40 distinct files -- byte-identical to `--max-files 50`, i.e. zero
bounding effect). `build_context_render_from_map`, `build_symbol_blast_radius_plan_from_map`, and
`build_symbol_blast_radius_render_from_map` now ALL opt in too, each passing its own `--max-files`
value as `suggested_edits_max`. The two tests below that used to pin the unbounded shape as
"must not change" were updated in place to pin the now-bounded shape instead; see
`tests/unit/test_context_render_and_blast_radius_max_files_bounds_suggested_edits.py` for the
RED/GREEN proof (mirroring this file's own `test_edit_plan_max_files_bounds_suggested_edits`
methodology) that the fix does real work on all three sibling commands.
"""

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_many_callers_project(tmp_path: Path, *, caller_count: int) -> Path:
    """A `create_invoice` definition plus `caller_count` distinct files that each import AND call
    it. Every caller file contributes both a `caller-update` suggested edit (the call site) and an
    `import-update` suggested edit (the `from ... import create_invoice` line), so `caller_count`
    files reliably produce roughly `2 * caller_count` raw `suggested_edits` entries -- comfortably
    more than a small `--max-files` cap would allow, which is what makes this fixture able to prove
    the cap is doing real work instead of trivially passing because there was nothing to truncate.
    """
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(src_dir / "payments.py", "def create_invoice(total):\n    return total + 1\n")
    for index in range(caller_count):
        _write(
            src_dir / f"caller_{index}.py",
            "from src.payments import create_invoice\n"
            "\n"
            f"def wrap_{index}(total):\n"
            "    return create_invoice(total)\n",
        )
    return project


def test_edit_plan_max_files_bounds_suggested_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED-confirmed then GREEN: `--max-files 2` must bound `edit_plan_seed.suggested_edits` to
    <=2 entries, even though the fixture produces far more than 2 raw candidates."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    # RED: reproduce the exact PRE-FIX shape by making the enforcement point a no-op passthrough
    # (before this PR, `_suggested_edits_from_related_spans`'s body never consulted `max_edits` at
    # all, which is behaviorally identical to `_capped_suggested_edits` always returning its input
    # unchanged). This proves two things at once: (a) the fixture genuinely produces more than the
    # cap -- so the assertion below isn't vacuously satisfied -- and (b) the bug, if reintroduced,
    # will fail this same test rather than silently regressing.
    monkeypatch.setattr(repo_map, "_capped_suggested_edits", lambda entries, max_edits: entries)
    uncapped_payload = repo_map.build_context_edit_plan("create invoice", project, max_files=2)
    uncapped_edits = uncapped_payload["edit_plan_seed"]["suggested_edits"]
    assert len(uncapped_edits) > 2, (
        "fixture must produce MORE than the --max-files cap without enforcement, or this test "
        "cannot prove the cap does real work (this would be a fixture bug, not a repo_map bug)"
    )
    monkeypatch.undo()

    # GREEN: with the real fix active, the SAME query/repo/cap is actually bounded, and the capped
    # list is a stable PREFIX of the uncapped one (not some other truncation/reordering).
    payload = repo_map.build_context_edit_plan("create invoice", project, max_files=2)
    seed = payload["edit_plan_seed"]
    assert len(seed["suggested_edits"]) <= 2
    assert seed["suggested_edits"] == uncapped_edits[:2]


def test_edit_plan_max_files_larger_cap_stays_under_the_raw_count(tmp_path: Path) -> None:
    """A generous --max-files must never inflate suggested_edits beyond what the (deduplicated)
    related-span analysis actually found -- the cap only ever truncates, never pads."""
    project = _build_many_callers_project(tmp_path, caller_count=2)

    payload = repo_map.build_context_edit_plan("create invoice", project, max_files=50)

    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 50


def test_edit_plan_max_files_zero_related_spans_still_returns_empty_list(tmp_path: Path) -> None:
    """Guardrail: a query with no related spans at all must still return `[]`, not error -- the cap
    must not assume a non-empty list."""
    project = tmp_path / "project"
    _write(project / "src" / "solo.py", "def lonely():\n    return 1\n")

    payload = repo_map.build_context_edit_plan("lonely", project, max_files=2)

    assert payload["edit_plan_seed"]["suggested_edits"] == []


@pytest.mark.parametrize("render_profile", ["full", "compact", "llm"])
def test_edit_plan_max_files_now_bounds_context_render_suggested_edits_in_every_profile(
    tmp_path: Path,
    render_profile: str,
) -> None:
    """UPDATED by #212 (was `..._does_not_change_context_render_suggested_edits`, which pinned the
    OLD unbounded "full"-profile shape as "must not change"): `build_context_render_from_map` now
    opts into `suggested_edits_max` too (passing its own `--max-files` value), closing the gap for
    "full" -- the default profile, and the one this test previously proved had NO cap at all, pre-
    or post-B9. "compact"/"llm" are unaffected in practice (they already truncated to the same bound
    one step downstream via `_compact_edit_plan_seed`) but are asserted here too so all three
    profiles are pinned to the same invariant going forward. See
    `test_context_render_and_blast_radius_max_files_bounds_suggested_edits.py` for the RED/GREEN
    proof that this bound does real truncation work (not vacuously satisfied)."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    payload = repo_map.build_context_render(
        "create invoice", project, max_files=2, render_profile=render_profile
    )

    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 2, (
        f"context-render (profile={render_profile!r}) suggested_edits must now be bounded by "
        "--max-files in every profile (#212 closed the 'full'-profile gap this test used to pin "
        "as deliberately out of scope)"
    )


def test_edit_plan_max_files_now_bounds_blast_radius_plan_and_render(tmp_path: Path) -> None:
    """UPDATED by #212 (was `..._does_not_change_blast_radius_plan_or_render`, which pinned the OLD
    unbounded shape as "must not change"): `build_symbol_blast_radius_plan_from_map` and
    `build_symbol_blast_radius_render_from_map` now ALSO opt into `suggested_edits_max`, each
    passing its own `--max-files` value -- the same fix `build_context_edit_plan_from_map` (B9/A18)
    and `build_context_render_from_map` (#212, above) already ship. See
    `test_context_render_and_blast_radius_max_files_bounds_suggested_edits.py` for the RED/GREEN
    proof that this bound does real truncation work (not vacuously satisfied)."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    plan_payload = repo_map.build_symbol_blast_radius_plan("create_invoice", project, max_files=2)
    render_payload = repo_map.build_symbol_blast_radius_render(
        "create_invoice", project, max_files=2
    )

    assert len(plan_payload["edit_plan_seed"]["suggested_edits"]) <= 2
    assert len(render_payload["edit_plan_seed"]["suggested_edits"]) <= 2
