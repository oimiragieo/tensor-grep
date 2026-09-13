from __future__ import annotations

import sys

import pytest

from tensor_grep.cli import bootstrap

# ---------------------------------------------------------------------------
# C1/C2/C3 RED (2026-09-12, dogfooded on the SHIPPED binary tg 1.119.4): the MANAGED NATIVE
# binary's root (option-first) door refuses rg-style search flags the explicit `search` form
# accepts (`tg needle --glob '*.py' src` -> "error: unexpected argument '--glob' found",
# while `tg search needle --glob '*.py' src` works). The native allowlist
# SEARCH_OPTION_FIRST_FLAGS carries the NEGATIVE inverses but not these POSITIVE forms, and
# its matcher misses attached short values (`-tpy`, `-C2`, `-g*.py`).
#
# FRONT-DOOR PARITY: the Python bootstrap door already treats every one of these spellings
# as a SEARCH in root position. These tests pin that contract so the native fix (Rust-side,
# in rust_core/src/main.rs) must agree with it -- and so a future Python-side regression
# cannot silently diverge from the native door either.
#
# DESIGN CONSTRAINT (bug #88 walk-DoS): the native fix must REWRITE
# `tg <pat> --glob X <path>` into the `tg search <pat> --glob X <path>` form -- never
# promote a walk-scope filter onto an unguarded rg/native fast path. The Python door's half
# of that constraint is pinned here (walk-scope subset -> full CLI, where the unbounded-walk
# guard fires) and the native half in main.rs's
# walk_scope_root_flag_rewrite_preserves_implicit_path_walk_guard_trigger.
# ---------------------------------------------------------------------------
_WALK_SCOPE_ROOT_DOOR_FLAGS = [
    # (flag token(s) as they appear in argv after the pattern) -- these narrow WHICH files
    # match but not the WALK, so BOTH doors must keep them on the guarded route.
    "--glob",
    "-g",
    "--files",  # tg-only surface (help advertisement), full CLI at the Python door
    "-l",
    "--files-with-matches",
]

_NATIVE_ROOT_DOOR_RG_PASSTHROUGH_FLAGS = [
    # Not walk-scope filters and not tg-only flags: the Python door rg-passthroughs these
    # (the native door must at minimum RECOGNIZE them -- its rewrite -> search form).
    "-U",
    "--multiline",
    "--hidden",
    "-0",
    "--null",
    "-d",
    "--max-depth",
    "-S",
    "--smart-case",
    "-C2",  # attached short value: the C2 matcher defect
]


def _root_option_first_argv(flag: str) -> list[str]:
    # Value-taking flags get an inline value; everything else is boolean in this set.
    value = {"--glob": "*.log", "-g": "*.log", "-d": "3", "--max-depth": "3"}.get(flag)
    argv = ["tg", "ERROR", flag]
    if value is not None:
        argv.append(value)
    argv.append("src")
    return argv


@pytest.mark.parametrize(
    "flag", _WALK_SCOPE_ROOT_DOOR_FLAGS + _NATIVE_ROOT_DOOR_RG_PASSTHROUGH_FLAGS
)
def test_main_entry_treats_every_native_root_door_flag_as_a_search(monkeypatch, flag):
    """FRONT-DOOR PARITY (C1/C2): every rg-style flag the native root door must accept in
    root (option-first) position is ALREADY a search at the Python bootstrap door -- it must
    route to a search route (rg passthrough or full CLI), never die as an A90
    unknown-command refusal. The walk-scope subset must specifically land on the FULL CLI so
    the #88 unbounded-walk guard can fire (never the unguarded rg passthrough)."""
    argv = _root_option_first_argv(flag)
    fired: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            fired.update({"route": "rg_passthrough", "args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: fired.update({"route": "full_cli"}))
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native tg should not run (none resolved)"),
    )

    # An A90 unknown-command refusal would SystemExit(2) here; the passthrough route raises
    # SystemExit(0) and the (stubbed) full-CLI route returns normally.
    try:
        bootstrap.main_entry()
    except SystemExit as exit_exc:
        assert exit_exc.code == 0, (
            f"root option-first form with {flag} must not be refused (exit {exit_exc.code})"
        )
    assert fired.get("route") in {"rg_passthrough", "full_cli"}, (
        f"root option-first form with {flag} must reach a search route, got: {fired}"
    )
    if flag in _WALK_SCOPE_ROOT_DOOR_FLAGS:
        # The #88 walk-guard half: a walk-scope filter must NOT reach the unguarded
        # rg passthrough -- the full CLI is where the bare-no-PATH walk guard fires.
        assert fired["route"] == "full_cli", (
            f"walk-scope flag {flag} must route to the full CLI (walk-guard route), "
            f"not the unguarded rg passthrough"
        )


@pytest.mark.parametrize(
    "flag", ["--glob", "-g", "-g*.py", "-tpy", "--files", "-l", "--files-with-matches"]
)
def test_python_door_root_walk_scope_and_tg_only_flags_still_demand_full_cli(flag):
    """Predicate-level twin of the routing pin above (A27/A39 class-fix twin rule): the
    walk-scope / tg-only members of the C1 flag set must keep asserting True from
    `_requires_full_cli` -- including the attached short-value forms (`-g*.py`, `-tpy`) the
    native door's matcher must learn to accept. This must not weaken the #88 routing pin:
    these flags were CHOSEN to be in the must-route set."""
    assert bootstrap._requires_full_cli(["ERROR", flag, "src"]), (
        f"{flag} must keep routing to the full CLI"
    )


def test_python_door_accepts_native_root_door_flag_set_without_command_refusal():
    """MUTATION-CONTROL TWIN: the parity pin above is only meaningful if the refusal
    predicate it relies on can distinguish these searches from unknown commands. Pin
    BOTH directions: every C1 flag spelling is NOT an unknown-command refusal, while a
    genuinely unknown command still is (A90)."""
    for flag in (
        _WALK_SCOPE_ROOT_DOOR_FLAGS + _NATIVE_ROOT_DOOR_RG_PASSTHROUGH_FLAGS + ["-g*.py", "-tpy"]
    ):
        argv = _root_option_first_argv(flag)
        assert bootstrap._top_level_command_refusal(argv) is None, (
            f"{argv} must be treated as a root option-first search, not an unknown command"
        )
    # The A90 control arm: a RESERVED/unknown command followed by a flag still refuses.
    assert bootstrap._top_level_command_refusal(["edit-ready", "--flag-x"]) is not None
