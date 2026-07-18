"""Audit #212, a follow-up to B9/#661: B9 fixed `tg edit-plan --json --max-files N` silently
ignoring `max_edits` when bounding `edit_plan_seed.suggested_edits` (repo_map.py's
`_capped_suggested_edits`), but its own test file
(`test_edit_plan_max_files_bounds_suggested_edits.py`) explicitly documented -- and pinned with a
"must not change" regression test -- that three sibling commands carried the IDENTICAL flag-lie and
were deliberately left unfixed as out of scope: `tg context-render`'s default "full" render profile
(the "compact"/"llm" profiles already had their own separate, pre-existing cap via
`_compact_edit_plan_seed`), `tg blast-radius-plan`, and `tg blast-radius-render` (neither of the
latter two has any downstream compaction step at all, in any profile).

VERIFIED REAL before fixing (not assumed from the audit): dogfooded directly against tensor-grep's
own `src/tensor_grep` tree (80 files, symbol `SearchConfig`) using the real Python builders.
`blast-radius-render --max-files 1` returned `files=[1 file]` (correctly bounded) but
`edit_plan_seed.suggested_edits` had 73 entries spanning 40 DISTINCT files -- byte-identical counts
to `--max-files 50` (55 related_spans / 73 suggested_edits in both cases), i.e. `--max-files` had
literally zero effect on this field. `blast-radius-plan --max-files 1` similarly returned 25
suggested_edits across 8 distinct files. `context-render --render-profile full --max-files 1`
returned 5 suggested_edits across 3 distinct files (`compact`/`llm` profiles were already correctly
bounded to 1 by their own separate cap).

Fixed the same disciplined way B9 did: each builder now opts into the ALREADY-EXISTING
`suggested_edits_max` parameter on `_attach_edit_plan_metadata`/`_build_edit_plan_seed` (which still
defaults to `None` -- unbounded -- for any caller that does not explicitly pass it), by threading its
own `--max-files` value through. No new CLI flags, no change to the low-level opt-in default.

This file proves the fix with the SAME RED/GREEN methodology
`test_edit_plan_max_files_bounds_suggested_edits.py` established for edit-plan itself: a fixture that
provably produces MORE raw suggested_edits than the cap (so the assertion isn't vacuous), a RED check
via monkeypatching `_capped_suggested_edits` to a raw passthrough (reproducing the exact pre-fix
shape), and a GREEN check that the real fix truncates to a stable PREFIX of the uncapped list.
"""

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map, session_daemon
from tests.unit.test_symbol_daemon_autostart import (
    _autostart_env,
    _cli_json,
    _probe_fake_for,
    _real_daemon,
    _serve,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_many_callers_project(tmp_path: Path, *, caller_count: int) -> Path:
    """Same fixture shape as `test_edit_plan_max_files_bounds_suggested_edits.py`'s own helper: a
    `create_invoice` definition plus `caller_count` distinct files that each import AND call it, so
    `caller_count` files reliably produce roughly `2 * caller_count` raw `suggested_edits` entries --
    comfortably more than a small `--max-files` cap, proving the cap does real truncation work."""
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


def test_context_render_full_profile_max_files_bounds_suggested_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED-confirmed then GREEN, for `render_profile="full"` (the default for text output, and
    explicitly selectable for --json): `--max-files 2` must now bound
    `edit_plan_seed.suggested_edits` to <=2 entries, matching edit-plan's own already-fixed
    behavior."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    monkeypatch.setattr(repo_map, "_capped_suggested_edits", lambda entries, max_edits: entries)
    uncapped_payload = repo_map.build_context_render(
        "create invoice", project, max_files=2, render_profile="full"
    )
    uncapped_edits = uncapped_payload["edit_plan_seed"]["suggested_edits"]
    assert len(uncapped_edits) > 2, (
        "fixture must produce MORE than the --max-files cap without enforcement, or this test "
        "cannot prove the cap does real work"
    )
    monkeypatch.undo()

    payload = repo_map.build_context_render(
        "create invoice", project, max_files=2, render_profile="full"
    )
    seed = payload["edit_plan_seed"]
    assert len(seed["suggested_edits"]) <= 2
    assert seed["suggested_edits"] == uncapped_edits[:2]


def test_blast_radius_plan_max_files_bounds_suggested_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED-confirmed then GREEN for `tg blast-radius-plan`, which (unlike context-render) has no
    downstream compaction step at all -- before this fix, suggested_edits was unconditionally
    unbounded regardless of profile."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    monkeypatch.setattr(repo_map, "_capped_suggested_edits", lambda entries, max_edits: entries)
    uncapped_payload = repo_map.build_symbol_blast_radius_plan(
        "create_invoice", project, max_files=2
    )
    uncapped_edits = uncapped_payload["edit_plan_seed"]["suggested_edits"]
    assert len(uncapped_edits) > 2, (
        "fixture must produce MORE than the --max-files cap without enforcement, or this test "
        "cannot prove the cap does real work"
    )
    monkeypatch.undo()

    payload = repo_map.build_symbol_blast_radius_plan("create_invoice", project, max_files=2)
    seed = payload["edit_plan_seed"]
    assert len(seed["suggested_edits"]) <= 2
    assert seed["suggested_edits"] == uncapped_edits[:2]


def test_blast_radius_render_max_files_bounds_suggested_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED-confirmed then GREEN for `tg blast-radius-render`, the starkest case dogfooded against
    tensor-grep's own repo: `--max-files 1` vs `--max-files 50` produced byte-IDENTICAL
    suggested_edits counts pre-fix (zero bounding effect whatsoever)."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    monkeypatch.setattr(repo_map, "_capped_suggested_edits", lambda entries, max_edits: entries)
    uncapped_payload = repo_map.build_symbol_blast_radius_render(
        "create_invoice", project, max_files=2
    )
    uncapped_edits = uncapped_payload["edit_plan_seed"]["suggested_edits"]
    assert len(uncapped_edits) > 2, (
        "fixture must produce MORE than the --max-files cap without enforcement, or this test "
        "cannot prove the cap does real work"
    )
    monkeypatch.undo()

    payload = repo_map.build_symbol_blast_radius_render("create_invoice", project, max_files=2)
    seed = payload["edit_plan_seed"]
    assert len(seed["suggested_edits"]) <= 2
    assert seed["suggested_edits"] == uncapped_edits[:2]


@pytest.mark.parametrize("render_profile", ["compact", "llm"])
def test_context_render_compact_and_llm_profiles_stay_prefix_stable(
    tmp_path: Path,
    render_profile: str,
) -> None:
    """ "compact"/"llm" profiles now apply the bound TWICE (once at the source via
    `suggested_edits_max`, once more downstream via `_compact_edit_plan_seed`'s own pre-existing
    `[:max_files]` truncation) -- both truncations agree on the same prefix, so this must be a
    provable no-op: the doubly-truncated result equals the singly-truncated (source-only) one."""
    project = _build_many_callers_project(tmp_path, caller_count=5)

    doubly_truncated = repo_map.build_context_render(
        "create invoice", project, max_files=2, render_profile=render_profile
    )["edit_plan_seed"]["suggested_edits"]

    full_profile_edits = repo_map.build_context_render(
        "create invoice", project, max_files=2, render_profile="full"
    )["edit_plan_seed"]["suggested_edits"]

    assert len(doubly_truncated) <= 2
    assert doubly_truncated == full_profile_edits


@pytest.mark.parametrize(
    "build",
    [
        lambda project: repo_map.build_context_render(
            "create invoice", project, max_files=50, render_profile="full"
        ),
        lambda project: repo_map.build_symbol_blast_radius_plan(
            "create_invoice", project, max_files=50
        ),
        lambda project: repo_map.build_symbol_blast_radius_render(
            "create_invoice", project, max_files=50
        ),
    ],
    ids=["context-render-full", "blast-radius-plan", "blast-radius-render"],
)
def test_larger_cap_stays_under_the_raw_count(tmp_path: Path, build) -> None:
    """A generous --max-files must never inflate suggested_edits beyond what the (deduplicated)
    related-span analysis actually found -- the cap only ever truncates, never pads. Mirrors
    `test_edit_plan_max_files_bounds_suggested_edits.py`'s own guard of the same name for
    edit-plan."""
    project = _build_many_callers_project(tmp_path, caller_count=2)

    payload = build(project)

    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 50


@pytest.mark.parametrize(
    "build",
    [
        lambda project: repo_map.build_context_render(
            "lonely", project, max_files=2, render_profile="full"
        ),
        lambda project: repo_map.build_symbol_blast_radius_plan("lonely", project, max_files=2),
        lambda project: repo_map.build_symbol_blast_radius_render("lonely", project, max_files=2),
    ],
    ids=["context-render-full", "blast-radius-plan", "blast-radius-render"],
)
def test_zero_related_spans_still_returns_empty_list(tmp_path: Path, build) -> None:
    """Guardrail: a query/symbol with no related spans at all must still return `[]`, not error --
    the cap must not assume a non-empty list. Mirrors
    `test_edit_plan_max_files_bounds_suggested_edits.py`'s own guard of the same name for
    edit-plan."""
    project = tmp_path / "project"
    _write(project / "src" / "solo.py", "def lonely():\n    return 1\n")

    payload = build(project)

    assert payload["edit_plan_seed"]["suggested_edits"] == []


# ---------------------------------------------------------------------------------------------
# Warm-daemon parity (gate nit from the #666 review): #666 threaded `suggested_edits_max=
# max_files` into `build_context_render_from_map` ITSELF (not the cold `build_context_render`
# wrapper), specifically so the cap would apply on both routes -- but every test above only ever
# drives it through the cold wrapper. `tg context-render` is also served by the warm session
# daemon (session_store.py's `context_render` command handler calls `build_context_render_from_map`
# directly against an already-cached repo_map, bypassing `build_context_render`'s own
# `build_repo_map` call entirely), so this proves the cap holds on that real code path too, not
# just when reached cold. Mirrors the real-spawned-daemon harness
# `test_orient_agent_daemon.py` established for orient/agent (itself reusing
# `test_symbol_daemon_autostart.py`'s `_real_daemon`/`_serve`/`_probe_fake_for`/`_autostart_env`/
# `_cli_json` fixtures for the same warm-vs-cold parity contract).
# ---------------------------------------------------------------------------------------------


def test_context_render_warm_daemon_bounds_suggested_edits_same_as_cold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_many_callers_project(tmp_path, caller_count=5)
    render_args = [
        "context-render",
        str(project),
        "create invoice",
        "--max-files",
        "2",
        "--render-profile",
        "full",
        "--json",
    ]

    cold_payload = _cli_json(render_args)
    cold_edits = cold_payload["edit_plan_seed"]["suggested_edits"]
    assert len(cold_edits) <= 2, "cold route must already bound suggested_edits (see #666)"

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(render_args)
    finally:
        server.shutdown()
        server.server_close()

    # Confirms the request actually went through the warm/daemon route, not a silent cold fallback.
    assert warm_payload["routing_reason"] == "session-context-render"
    warm_edits = warm_payload["edit_plan_seed"]["suggested_edits"]
    assert len(warm_edits) <= 2
    assert warm_edits == cold_edits
