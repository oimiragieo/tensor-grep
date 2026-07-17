"""Closes 3 test-coverage gaps flagged by the adversarial Opus gate on PR #581 (the --deadline
CLI-consistency fix that added --deadline/--no-deadline to agent/edit-plan/context/context-render/
map/orient and --deadline to defs, docs/CONTRACTS.md:110). Test-only -- no production logic
changes; see test_cli_deadline_flag.py for the flag-registration/threading regression suite this
file complements.

Item 1 (HIGHEST VALUE): the daemon-skip guard (``if effective_deadline is None: ...`` before each
``_maybe_<cmd>_via_running_daemon`` call) had ZERO regression coverage -- a regression deleting it
would pass every existing test, since a warm daemon session is off by default in CI/tests anyway.

Item 2: cold-path exit-2 coverage under a REAL --deadline-truncated scan (not a mocked payload) for
the newly-wired repo-scanning commands, plus confirmation that `tg orient` is the documented
exception (stays exit 0).

Item 3: `tg agent`'s SECOND (rescue) blast-radius scan -- `_collect_capsule_call_site_evidence`
(agent_capsule.py) converts the shared absolute deadline to a remaining-seconds budget for its own
FS-backed rescue scan; this must never crash/hang, even when the shared budget is already spent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.agent_capsule as agent_capsule
import tensor_grep.cli.main as main
import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import app

# ==================================================================================================
# Item 1: daemon-skip guard regression coverage. Each case monkeypatches the command's own
# daemon-probe function (found via `grep _maybe_.*_via_running_daemon src/tensor_grep/cli/main.py`)
# and proves BOTH directions: --deadline passed -> the probe is never called (the guard's ternary
# short-circuits the call expression itself, not just "discards the result"); no --deadline -> the
# probe IS consulted (proves the guard's else-arm still fires -- not merely "daemon off in tests").
# ==================================================================================================

_DAEMON_SKIP_GUARD_CASES = {
    # command -> (probe function name in tensor_grep.cli.main, extra positional args builder)
    "orient": ("_maybe_orient_via_running_daemon", lambda p: [str(p)]),
    "context-render": ("_maybe_context_render_via_running_daemon", lambda p: [str(p), "q"]),
    "agent": ("_maybe_agent_via_running_daemon", lambda p: [str(p), "q"]),
    "edit-plan": ("_maybe_edit_plan_via_running_daemon", lambda p: [str(p), "q"]),
}


@pytest.mark.parametrize("command", sorted(_DAEMON_SKIP_GUARD_CASES))
def test_daemon_probe_skipped_when_deadline_passed(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    probe_name, args_for = _DAEMON_SKIP_GUARD_CASES[command]
    calls: list = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return None  # a probe "miss" -- cold path runs either way; only the CALL is under test

    monkeypatch.setattr(main, probe_name, _spy)
    result = CliRunner().invoke(app, [command, *args_for(tmp_path), "--deadline", "5", "--json"])
    assert calls == [], f"{command}: daemon probe consulted despite --deadline: {result.output}"


@pytest.mark.parametrize("command", sorted(_DAEMON_SKIP_GUARD_CASES))
def test_daemon_probe_consulted_without_deadline(tmp_path: Path, monkeypatch, command: str) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    probe_name, args_for = _DAEMON_SKIP_GUARD_CASES[command]
    calls: list = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(main, probe_name, _spy)
    result = CliRunner().invoke(app, [command, *args_for(tmp_path), "--json"])
    assert len(calls) == 1, (
        f"{command}: daemon probe NOT consulted without --deadline: {result.output}"
    )


# `defs` shares `_maybe_symbol_command_via_running_daemon` with refs/callers/impact/blast-radius
# (called with command="defs"), unlike the 4 commands above which each own a dedicated probe
# function -- kept separate so the shared-probe call count stays unambiguous (only defs is invoked).


def test_defs_daemon_probe_skipped_when_deadline_passed(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    calls: list = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(main, "_maybe_symbol_command_via_running_daemon", _spy)
    result = CliRunner().invoke(app, ["defs", str(tmp_path), "f", "--deadline", "5", "--json"])
    assert calls == [], f"defs: daemon probe consulted despite --deadline: {result.output}"


def test_defs_daemon_probe_consulted_without_deadline(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    calls: list = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(main, "_maybe_symbol_command_via_running_daemon", _spy)
    result = CliRunner().invoke(app, ["defs", str(tmp_path), "f", "--json"])
    assert len(calls) == 1, f"defs: daemon probe NOT consulted without --deadline: {result.output}"
    assert calls[0].get("command") == "defs"


# ==================================================================================================
# dogfood finding 1 / F4: `tg agent` defaults --deadline to 60s (mirrors codemap's #153) so a
# whole-repo call without an explicit --deadline still terminates in bounded time -- but ONLY on
# the COLD fallback, applied in the command BODY strictly AFTER the warm-daemon gate decides
# whether to try the daemon (`if effective_deadline is None: ...`). Putting the 60.0 default on
# the typer.Option itself (codemap's own placement -- codemap has no daemon gate at all) would
# make `effective_deadline` never None on a default call, silently skipping the daemon probe on
# EVERY invocation and killing the #108 moat. The tests below prove both halves of that contract
# at once: the daemon gate is still consulted by default, AND the cold fallback (once the daemon
# misses/is unavailable) gets exactly 60.0.
# ==================================================================================================


def _agent_cold_spy(recorded: dict):
    def _spy(query, path, **kwargs):
        recorded["deadline_seconds"] = kwargs.get("deadline_seconds")
        return {"path": str(path), "query": query}

    return _spy


def test_agent_default_still_reaches_daemon_gate_before_60s_cold_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """THE moat-preservation proof: a default `tg agent` call (no --deadline/--no-deadline) must
    still ATTEMPT the warm-daemon path, proving the 60s cold-path default cannot have been
    applied before the gate's `effective_deadline is None` check."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    daemon_calls: list = []

    def _daemon_spy(**kwargs):
        daemon_calls.append(kwargs)
        return None  # a daemon "miss" -- falls through to cold; only the CALL matters here

    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", _daemon_spy)
    cold_recorded: dict = {}
    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _agent_cold_spy(cold_recorded))

    result = CliRunner().invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 0, result.output
    assert len(daemon_calls) == 1, "the warm-daemon gate must still be consulted by default"
    assert cold_recorded.get("deadline_seconds") == 60.0, (
        "the cold fallback after a daemon miss must default to 60s"
    )


def test_agent_no_deadline_flag_also_reaches_daemon_gate_and_cold_stays_unbounded(
    tmp_path: Path, monkeypatch
) -> None:
    """--no-deadline is a real opt-out: the daemon gate is STILL consulted (unchanged from
    today -- effective_deadline is None either way), but the cold fallback must stay unbounded
    (None), never silently downgraded to the new 60s default."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    daemon_calls: list = []

    def _daemon_spy(**kwargs):
        daemon_calls.append(kwargs)
        return None

    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", _daemon_spy)
    cold_recorded: dict = {}
    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _agent_cold_spy(cold_recorded))

    result = CliRunner().invoke(app, ["agent", str(tmp_path), "f", "--no-deadline", "--json"])

    assert result.exit_code == 0, result.output
    assert len(daemon_calls) == 1
    assert cold_recorded.get("deadline_seconds") is None


def test_agent_explicit_deadline_overrides_the_60s_default(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", lambda **kwargs: None)
    cold_recorded: dict = {}
    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _agent_cold_spy(cold_recorded))

    result = CliRunner().invoke(app, ["agent", str(tmp_path), "f", "--deadline", "30", "--json"])

    assert result.exit_code == 0, result.output
    assert cold_recorded.get("deadline_seconds") == 30.0


def test_agent_default_cli_deadline_constant_is_60_seconds() -> None:
    """Documents/locks the constant agent's body-level default reads. Unlike codemap's own guard
    test (which pins a literal-duplicated typer.Option default against codemap.DEFAULT_CLI_
    DEADLINE_SECONDS -- required there because codemap's default sits ON the typer.Option,
    evaluated at CLI-decoration/module-import time, before codemap.py's heavy import runs),
    agent's 60s default is applied in the command BODY -- after `agent_capsule` is already
    lazily imported -- so main.py imports agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS
    DIRECTLY rather than literal-duplicating it. There is no drift to pin; this just locks the
    value itself."""
    assert agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS == 60.0


# ==================================================================================================
# Item 2: cold-path exit-2 coverage under a REAL --deadline-truncated scan (not a mocked payload).
# Target `src/tensor_grep` itself (~80 real files) rather than a tiny tmp_path fixture -- a 0.1s
# deadline needs genuine scan work to truncate against; a 2-file fixture can complete a full repo
# scan in well under 100ms, which would make the test assert nothing. Only grows more reliable as
# this source tree grows, never less.
# ==================================================================================================

_REAL_REPO_DIR = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

_COLD_PATH_REAL_DEADLINE_CASES = {
    "map": ["map", str(_REAL_REPO_DIR), "--deadline", "0.1", "--json"],
    "context": ["context", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    "context-render": ["context-render", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    "agent": ["agent", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    "edit-plan": ["edit-plan", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    "defs": ["defs", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    # CEO v1.72.1 dogfood M1: source/blast-radius-plan both go through the same build_repo_map
    # AST-parse loop as defs above (proven reliable at 0.1s against this ~80-file real tree), so
    # they reuse the identical real-deadline-truncation pattern.
    "source": ["source", str(_REAL_REPO_DIR), "q", "--deadline", "0.1", "--json"],
    "blast-radius-plan": [
        "blast-radius-plan",
        str(_REAL_REPO_DIR),
        "q",
        "--deadline",
        "0.1",
        "--json",
    ],
}


@pytest.mark.parametrize("command", sorted(_COLD_PATH_REAL_DEADLINE_CASES))
def test_real_deadline_truncation_exits_2_with_partial(command: str) -> None:
    result = CliRunner().invoke(app, _COLD_PATH_REAL_DEADLINE_CASES[command])
    assert result.exit_code == 2, f"{command}: {result.output}"
    payload = json.loads(result.output)
    assert payload.get("partial") is True, f"{command}: {result.output}"


def test_orient_real_deadline_truncation_stays_exit_0() -> None:
    # docs/CONTRACTS.md:110 -- tg orient is the documented exception: it never gates on
    # _scan_incomplete, so a truncated scan still exits 0, surfacing partial/deadline_limit only
    # as informational fields (never a retry signal).
    result = CliRunner().invoke(app, ["orient", str(_REAL_REPO_DIR), "--deadline", "0.1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload.get("partial") is True, result.output


# ==================================================================================================
# Item 3: `tg agent`'s SECOND (rescue) blast-radius scan. _collect_capsule_call_site_evidence
# (agent_capsule.py ~543-560) converts the shared ABSOLUTE deadline_monotonic to a RELATIVE
# remaining-seconds budget for its own FS-backed build_symbol_blast_radius call, floored at 0.1s
# (max(0.1, deadline_monotonic - time.monotonic())) so an already-exhausted shared budget never
# passes a negative/zero deadline downstream. Both tests are deterministic (no wall-clock racing):
# an explicit, generously-bounded time.sleep() forces the shared deadline to have already elapsed
# by the time execution reaches the rescue scan, rather than gambling on real scan timing.
# ==================================================================================================


def _write_helper_and_caller(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text(
        "from mod import helper\n\n\ndef main():\n    return helper()\n", encoding="utf-8"
    )


def test_agent_second_scan_deadline_clamps_to_floor(tmp_path: Path, monkeypatch) -> None:
    # Delay AFTER the real render/ranking pass resolves "helper" as the primary target (so the
    # rescue collector's early gates -- no-symbol / not-requested / low-confidence -- all pass and
    # execution actually reaches the remaining-seconds clamp), but BEFORE the rescue scan reads the
    # clock -- so the shared deadline is provably expired only once we get there.
    _write_helper_and_caller(tmp_path)
    original_render = repo_map.build_context_render_from_map

    def _slow_render(rm, query, **kwargs):
        result = original_render(rm, query, **kwargs)
        time.sleep(0.5)
        return result

    recorded: dict = {}
    original_blast_radius = repo_map.build_symbol_blast_radius

    def _spy_blast_radius(symbol, path, **kwargs):
        recorded["deadline_seconds"] = kwargs.get("deadline_seconds")
        return original_blast_radius(symbol, path, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render_from_map", _slow_render)
    monkeypatch.setattr(agent_capsule.repo_map, "build_symbol_blast_radius", _spy_blast_radius)

    result = CliRunner().invoke(
        app, ["agent", str(tmp_path), "helper", "--deadline", "0.3", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)  # must not crash/hang -- valid JSON either way
    assert payload["primary_target"]["symbol"] == "helper"
    assert payload["call_site_evidence"]["status"] == "collected"
    # The item-3 assertion: the shared deadline had already elapsed (0.5s sleep > 0.3s budget), so
    # the rescue scan must receive the FLOORED 0.1s budget, never a negative or zero value.
    assert recorded.get("deadline_seconds") == 0.1


def test_agent_second_scan_skips_gracefully_on_full_deadline_exhaustion(
    tmp_path: Path, monkeypatch
) -> None:
    # The more extreme case: the shared deadline is already exhausted before the FIRST scan even
    # starts (0.3s sleep > 0.1s budget), so no primary target is resolved and the rescue collector's
    # OWN early-exit ("primary target has no symbol") fires before it ever reaches the
    # remaining-seconds clamp -- still must not crash/hang, and must still emit valid partial JSON.
    _write_helper_and_caller(tmp_path)
    original_build_repo_map = repo_map.build_repo_map

    def _slow_build_repo_map(path, **kwargs):
        time.sleep(0.3)
        return original_build_repo_map(path, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_repo_map", _slow_build_repo_map)

    result = CliRunner().invoke(
        app, ["agent", str(tmp_path), "helper", "--deadline", "0.1", "--json"]
    )
    assert result.exit_code == 2, result.output  # partial:true -> incomplete, exit-2 contract
    payload = json.loads(result.output)  # must not crash/hang -- valid JSON on full exhaustion too
    assert payload.get("partial") is True
    assert payload["deadline_limit"]["deadline_exceeded"] is True
    assert payload["call_site_evidence"]["status"] == "skipped"
