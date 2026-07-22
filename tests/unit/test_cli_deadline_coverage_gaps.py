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
from typing import Any

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
    # #639 Opus-gate nit 1 (dogfood #1 RESIDUAL): this scenario's shared deadline has ALREADY
    # elapsed by the time the rescue scan even starts (0.5s injected delay > 0.3s budget) -- pre-
    # fix that silently reported exit 0 (the rescue scan itself still succeeded inside its floored
    # 0.1s sub-budget, so nothing individually named in the old fold-in ever flagged it), which was
    # itself an instance of the exact silent lie this PR closes: a `--deadline 0.3` request that
    # actually ran ~0.5s+ must never report success as if it finished in budget. The FINAL
    # wall-clock catch-all now correctly reports exit 2 / partial=True here, even though the rescue
    # scan's OWN substantive result (found the caller) is still present and still useful.
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)  # must not crash/hang -- valid JSON either way
    assert payload["primary_target"]["symbol"] == "helper"
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload.get("partial") is True, result.output
    assert payload.get("partial_reason") == "deadline", result.output
    # The item-3 assertion (unchanged): the shared deadline had already elapsed (0.5s sleep > 0.3s
    # budget), so the rescue scan must receive the FLOORED 0.1s budget, never a negative or zero
    # value -- proven by the substantive "collected" result above despite the overall lateness.
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


# ==================================================================================================
# Item 4 (dogfood #1 RESIDUAL, #639 Opus-gate nit 1): #639 (W1b) bounded the CHECKPOINTED post-map
# stages (build_context_pack_from_map's own pagerank/scoring loop, DAR's outbound-dependency
# collection) and folded each one's own deadline-break flag into the capsule's `result["partial"]`.
# But that fold-in only named the sibling stages it explicitly threaded a flag through -- the
# call-site-evidence rescue scan's OWN partial signal was silently dropped, and NOTHING re-checked
# the shared wall-clock budget one final time before the capsule returns. So a `tg agent --deadline`
# request whose SCAN and RENDER both finish in budget, but whose (uninstrumented) tail work pushes
# elapsed time past the deadline, still silently reported exit 0 / partial-not-True -- the exact
# silent lie dogfood #1 originally flagged, just relocated one stage later. Fixed by: (a) a FINAL
# wall-clock catch-all in build_agent_capsule_from_map that stamps partial=True/partial_reason=
# "deadline" regardless of which stage actually consumed the time; (b) propagating call-site-
# evidence's own radius_payload["partial"] into the evidence dict (agent_capsule.py's existing
# _copy_partial_signal helper, reused verbatim); (c) bounding _precomputed_validation_files_for_
# root's per-entry Path.resolve() loop (repo_map.py, see test_repo_map_deadline.py), the
# pre-existing unbounded cost documented in test_agent_codemap_deadline_scale.py's module docstring.
# ==================================================================================================


def test_agent_tail_overrun_after_checkpointed_pack_stage_still_reports_partial(
    tmp_path: Path, monkeypatch
) -> None:
    """Deterministic (no wall-clock racing, mirrors test_agent_second_scan_deadline_clamps_to_
    floor's proven technique above): force the shared deadline to have ALREADY elapsed by the
    time execution reaches the tail (validation-file discovery + call-site-evidence), while the
    CHECKPOINTED scan + build_context_pack_from_map stage itself finishes well within budget --
    the exact "scan+render fit, tail overruns" shape the existing 0.1s integration tests (which
    cross the deadline in the scan itself) do not cover. On this small a fixture neither the
    validation-file-resolve tail nor the rescue blast-radius scan individually takes measurable
    time, so this specifically isolates the FINAL catch-all (fix item 1), not items 2/3's more
    targeted bounds.
    """
    _write_helper_and_caller(tmp_path)
    original_pack = repo_map.build_context_pack_from_map

    def _slow_pack(rm, query, **kwargs):
        result = original_pack(rm, query, **kwargs)
        time.sleep(0.5)
        return result

    monkeypatch.setattr(repo_map, "build_context_pack_from_map", _slow_pack)

    result = CliRunner().invoke(
        app, ["agent", str(tmp_path), "helper", "--deadline", "0.3", "--json"]
    )

    # RED pre-fix: this was exit_code == 0 / payload.get("partial") in (None, False) -- the silent
    # lie. GREEN post-fix: the final catch-all in build_agent_capsule_from_map re-checks the shared
    # absolute deadline before returning, regardless of which stage actually overran.
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload.get("partial") is True, result.output
    assert payload.get("partial_reason") == "deadline", result.output
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.output


def test_agent_no_overrun_stays_exit_0_partial_absent(tmp_path: Path) -> None:
    """Companion golden-parity case: a real run that finishes on its own, well within a generous
    deadline, must NOT gain a spurious partial=True from the new catch-all (it only fires on an
    ACTUAL wall-clock overrun, never unconditionally)."""
    _write_helper_and_caller(tmp_path)
    result = CliRunner().invoke(
        app, ["agent", str(tmp_path), "helper", "--deadline", "30", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload.get("partial") is not True, result.output
    assert "partial_reason" not in payload, result.output


def test_collect_capsule_call_site_evidence_propagates_inner_partial_signal(
    tmp_path: Path, monkeypatch
) -> None:
    """Fix item 2: _collect_capsule_call_site_evidence's own (possibly deadline-truncated)
    build_symbol_blast_radius rescue scan can come back partial, but the evidence dict built from
    its radius_payload silently dropped that signal -- an agent reading call_site_evidence in
    isolation had no way to know the caller set was truncated, and the capsule-level fold-in the
    sibling test above depends on could never observe it either."""
    partial_radius = {
        "callers": [],
        "output_limit": {},
        "graph_trust_summary": {},
        "resolution_gaps": [],
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True},
    }
    monkeypatch.setattr(
        agent_capsule.repo_map,
        "build_symbol_blast_radius",
        lambda *a, **k: dict(partial_radius),
    )

    related_call_sites, evidence = agent_capsule._collect_capsule_call_site_evidence(
        "helper",
        str(tmp_path),
        {"symbol": "helper", "confidence": 0.9},
        include_blast_radius=True,
        max_files=3,
        max_repo_files=None,
        seed_confidence=0.9,
    )

    assert related_call_sites == []
    assert evidence.get("partial") is True, evidence
    assert evidence.get("deadline_limit", {}).get("deadline_exceeded") is True, evidence


# ==================================================================================================
# Item 5 (#642 gate nit-1 fast-follow): #642 added the SAME final wall-clock catch-all as Item 4
# above, but ONLY to build_agent_capsule_from_map (agent_capsule.py) -- the #642 Opus gate flagged
# that `tg context-render` / `tg edit-plan` / `tg context` reach their own render/pack builders
# (build_context_render_from_map, build_context_edit_plan_from_map, build_context_pack -- all
# repo_map.py) WITHOUT ever routing through the agent capsule, so none of them ever saw a return-time
# recheck: a tail stage overrunning the shared --deadline budget after the checkpointed
# build_context_pack_from_map stage finished in budget could still silently report exit 0 /
# partial-not-True. Separately (found while extending the fix, not in the original #642 gate note):
# build_context_edit_plan_from_map's OWN call into _attach_edit_plan_metadata dropped
# deadline_monotonic entirely, so `tg edit-plan` never threaded a deadline into the edit-plan-seed's
# validation-plan discovery AT ALL, independent of the backstop. Fixed by: (a) the same final
# wall-clock catch-all (partial=True/partial_reason="deadline"/deadline_limit), mirrored verbatim,
# added to all three render/pack builders' single return points; (b) deadline_monotonic/deadline_hit
# threaded through the SECOND validation-plan chain named by the gate
# (_validation_plan_and_alignment_for_tests -> _raw_validation_plan_for_tests ->
# _detect_validation_runners_from_root -> _precomputed_validation_files_for_root, repo_map.py
# ~11987, reached via _build_edit_plan_seed) so that chain is actually BOUNDED, not merely
# backstopped after the fact; (c) build_context_edit_plan_from_map now passes its own
# deadline_monotonic into _attach_edit_plan_metadata.
# ==================================================================================================

_RENDER_FAMILY_DEADLINE_COMMAND_ARGS = {
    # command -> args-builder(tmp_path, deadline_seconds) -> full CliRunner argv
    "context-render": lambda p, d: [
        "context-render",
        str(p),
        "helper",
        "--deadline",
        str(d),
        "--json",
    ],
    "edit-plan": lambda p, d: ["edit-plan", str(p), "helper", "--deadline", str(d), "--json"],
    "context": lambda p, d: ["context", str(p), "helper", "--deadline", str(d), "--json"],
}


@pytest.mark.parametrize("command", sorted(_RENDER_FAMILY_DEADLINE_COMMAND_ARGS))
def test_render_family_tail_overrun_after_checkpointed_pack_stage_still_reports_partial(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    """Deterministic (no wall-clock racing, same proven technique as Item 4's agent test): force the
    shared deadline to have ALREADY elapsed by the time execution reaches each command's tail (the
    edit-plan-seed / validation-plan work that runs AFTER build_context_pack_from_map returns), while
    that checkpointed stage itself finishes well within budget. `context` has no edit-plan-seed tail,
    but shares the same missing return-time recheck in build_context_pack, so it must also flip.

    RED pre-fix: exit_code == 0 / payload.get("partial") in (None, False) for all three commands --
    the exact silent lie #642 closed for `tg agent` only. GREEN post-fix: each builder's own final
    catch-all re-checks the shared absolute deadline before returning, regardless of which stage
    actually overran.
    """
    _write_helper_and_caller(tmp_path)
    original_pack = repo_map.build_context_pack_from_map

    def _slow_pack(rm, query, **kwargs):
        result = original_pack(rm, query, **kwargs)
        time.sleep(0.5)
        return result

    monkeypatch.setattr(repo_map, "build_context_pack_from_map", _slow_pack)

    args = _RENDER_FAMILY_DEADLINE_COMMAND_ARGS[command](tmp_path, 0.3)
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 2, f"{command}: {result.output}"
    payload = json.loads(result.output)
    assert payload.get("partial") is True, f"{command}: {result.output}"
    assert payload.get("partial_reason") == "deadline", f"{command}: {result.output}"
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, (
        f"{command}: {result.output}"
    )


@pytest.mark.parametrize("command", sorted(_RENDER_FAMILY_DEADLINE_COMMAND_ARGS))
def test_render_family_no_overrun_stays_exit_0_partial_absent(tmp_path: Path, command: str) -> None:
    """Companion golden-parity case (mirrors Item 4's agent golden test): a real run that finishes on
    its own, well within a generous deadline, must NOT gain a spurious partial=True/partial_reason
    from the new catch-all -- it only fires on an ACTUAL wall-clock overrun, never unconditionally."""
    _write_helper_and_caller(tmp_path)
    args = _RENDER_FAMILY_DEADLINE_COMMAND_ARGS[command](tmp_path, 30)
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, f"{command}: {result.output}"
    payload = json.loads(result.output)
    assert payload.get("partial") is not True, f"{command}: {result.output}"
    assert "partial_reason" not in payload, f"{command}: {result.output}"


def test_validation_plan_and_alignment_threads_deadline_into_precomputed_file_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    """Proves fix-part (b) directly (thread, not just backstop): _validation_plan_and_alignment_for_
    tests is the SECOND validation-plan chain the #642 gate named (repo_map.py ~11987, reached via
    _build_edit_plan_seed). Spies on the innermost function in the chain
    (_precomputed_validation_files_for_root, already deadline-aware since #642/#639) and asserts it
    actually RECEIVES the caller's deadline_monotonic/deadline_hit -- proving the chain is bounded,
    not merely caught after the fact by the return-time backstop tested above. Deterministic: no
    sleeping, no timing assertions, just kwarg propagation."""
    (tmp_path / "mod.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    test_file = tmp_path / "test_mod.py"
    test_file.write_text(
        "from mod import helper\n\n\ndef test_helper():\n    assert helper() == 1\n",
        encoding="utf-8",
    )
    recorded: dict = {}
    original = repo_map._precomputed_validation_files_for_root

    def _spy(root, file_paths, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        recorded["deadline_hit"] = kwargs.get("deadline_hit")
        return original(root, file_paths, **kwargs)

    monkeypatch.setattr(repo_map, "_precomputed_validation_files_for_root", _spy)

    sentinel_deadline = time.monotonic() + 30.0
    sentinel_flag = repo_map._DeadlineBreakFlag()
    repo_map._validation_plan_and_alignment_for_tests(
        [str(test_file)],
        repo_root=str(tmp_path),
        primary_symbol={"name": "helper", "file": str(tmp_path / "mod.py")},
        primary_file=str(tmp_path / "mod.py"),
        query="helper",
        deadline_monotonic=sentinel_deadline,
        deadline_hit=sentinel_flag,
    )

    assert recorded.get("deadline_monotonic") == sentinel_deadline, recorded
    assert recorded.get("deadline_hit") is sentinel_flag, recorded


def test_build_context_edit_plan_from_map_threads_deadline_into_edit_plan_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression lock for fix-part (c): build_context_edit_plan_from_map's own call into
    _attach_edit_plan_metadata dropped deadline_monotonic entirely (found while extending #642's fix
    to `tg edit-plan`, distinct from the SECOND-validation-plan-chain gap the #642 gate itself
    flagged) -- the edit-plan-seed's internal deadline-checked loops never saw a deadline at all for
    the edit-plan command family. Spy-only; no real overrun needed."""
    _write_helper_and_caller(tmp_path)
    recorded: dict = {}
    original = repo_map._attach_edit_plan_metadata

    def _spy(repo_map_arg, payload_arg, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(repo_map_arg, payload_arg, **kwargs)

    monkeypatch.setattr(repo_map, "_attach_edit_plan_metadata", _spy)
    sentinel_deadline = time.monotonic() + 30.0
    built_repo_map = repo_map.build_repo_map(str(tmp_path))

    repo_map.build_context_edit_plan_from_map(
        built_repo_map,
        "helper",
        deadline_monotonic=sentinel_deadline,
    )

    assert recorded.get("deadline_monotonic") == sentinel_deadline, recorded


# ==================================================================================================
# Item 6 (+10% campaign, ranked-queue #5): `tg agent`'s WARM/DAEMON call-site-evidence collector,
# `_collect_capsule_call_site_evidence_from_map` (agent_capsule.py), had NO `deadline_monotonic`
# parameter at all -- its own `build_symbol_blast_radius_from_map` call (repo_map.py, itself already
# deadline-aware and internally deadline-gated since the #691/#222 BFS-bounding wave) was reached
# with no deadline, making the DEFAULT branch of `build_agent_capsule_from_map`
# (`_rescue_call_site_evidence=False`, taken by every warm/daemon `tg agent`/`tg prepare` call --
# `session_store.py`'s "agent" command handler) run this scan structurally unbounded regardless of
# `--deadline`. The cold sibling (`_collect_capsule_call_site_evidence`, covered by Item 3 above)
# already threads its own deadline correctly; this closes the warm sibling's gap. Purely additive:
# `deadline_monotonic` defaults to `None`, identical in effect to every pre-fix caller (none of
# which could pass it at all, since the parameter did not exist).
# ==================================================================================================


def _write_ambiguous_symbol_fixture(tmp_path: Path, *, padding_files: int) -> None:
    """Two files define the SAME top-level symbol name ("helper") -- the one shape that forces
    `_preferred_definition_files`' own deadline-checked loop (repo_map.py) to actually iterate; with
    a single definition file it early-returns before ever consulting the deadline. `padding_files`
    extra tiny modules pad out the repo-map file universe that loop iterates, so an injected
    per-file delay (see the budget test below) accumulates into a measurable, controllable total."""
    (tmp_path / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "helper_dup.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    for i in range(padding_files):
        (tmp_path / f"pad_{i}.py").write_text(f"PAD_{i} = {i}\n", encoding="utf-8")


def test_warm_call_site_evidence_respects_deadline_budget(tmp_path: Path, monkeypatch) -> None:
    """Part (a): with the fix, a tight deadline bounds the ACTUAL wall clock of
    `_collect_capsule_call_site_evidence_from_map`, not just the flags it returns. Deterministic --
    no wall-clock racing against real scan cost: an artificial per-file delay is injected into
    `_file_imports_symbol_from_definition` (the innermost call inside `_preferred_definition_files`'
    own deadline-checked loop), so the fully-unbounded total (40 padding files x up to 2 definition
    candidates x 0.01s <= 0.8s) is far larger than the budget, and the bounded total must stay close
    to it instead. RED pre-fix: this call would raise TypeError (no such parameter existed) --
    proving the seam itself, not just its timing, was previously absent."""
    _write_ambiguous_symbol_fixture(tmp_path, padding_files=40)
    original = repo_map._file_imports_symbol_from_definition

    def _slow_check(*args, **kwargs):
        time.sleep(0.01)
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_file_imports_symbol_from_definition", _slow_check)

    rm = repo_map.build_repo_map(str(tmp_path))
    target = {"symbol": "helper", "confidence": 0.9}
    deadline = time.monotonic() + 0.15

    start = time.monotonic()
    agent_capsule._collect_capsule_call_site_evidence_from_map(
        "helper",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
        deadline_monotonic=deadline,
    )
    elapsed = time.monotonic() - start

    # Budget (0.15s) + generous slack for scheduler jitter -- comfortably below the ~0.8s the
    # fully-unbounded loop would take if the deadline were silently dropped (the pre-fix bug).
    assert elapsed < 0.6, f"elapsed={elapsed:.3f}s -- deadline was not threaded into the warm scan"


def test_warm_call_site_evidence_reports_partial_honestly_on_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    """Part (b): an ALREADY-EXPIRED shared deadline must make the collected evidence dict report
    partial=True / deadline_limit.deadline_exceeded=True honestly (not silently succeed), mirroring
    Item 4's `test_collect_capsule_call_site_evidence_propagates_inner_partial_signal` for the cold
    sibling. Deterministic: the deadline is already in the past when the collector is entered, so
    `_preferred_definition_files`' very first loop iteration trips the deadline check -- no
    sleeping, no timing assertions."""
    _write_ambiguous_symbol_fixture(tmp_path, padding_files=5)
    rm = repo_map.build_repo_map(str(tmp_path))
    target = {"symbol": "helper", "confidence": 0.9}
    already_expired = time.monotonic() - 5.0

    _related, evidence, _unreliable = agent_capsule._collect_capsule_call_site_evidence_from_map(
        "helper",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
        deadline_monotonic=already_expired,
    )

    assert evidence.get("partial") is True, evidence
    assert evidence.get("deadline_limit", {}).get("deadline_exceeded") is True, evidence


def test_warm_call_site_evidence_deadline_none_is_byte_identical_noop(
    tmp_path: Path, monkeypatch
) -> None:
    """Part (c): the new `deadline_monotonic` parameter defaults to `None`, and passing `None`
    explicitly must be indistinguishable from every pre-fix caller (which could not pass it at all,
    since the parameter did not exist -- the callee's OWN default was already `None` either way).
    Pins BOTH the exact kwarg `build_symbol_blast_radius_from_map` receives AND the full returned
    payload, proving the two call shapes (omit the kwarg vs pass `deadline_monotonic=None`) are
    byte-identical."""
    _write_helper_and_caller(tmp_path)
    rm = repo_map.build_repo_map(str(tmp_path))
    target = {"symbol": "helper", "confidence": 0.9}
    recorded: list[Any] = []
    original = repo_map.build_symbol_blast_radius_from_map

    def _spy(*args, **kwargs):
        recorded.append(kwargs.get("deadline_monotonic", "<absent>"))
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_symbol_blast_radius_from_map", _spy)

    omitted = agent_capsule._collect_capsule_call_site_evidence_from_map(
        "helper",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
    )
    explicit_none = agent_capsule._collect_capsule_call_site_evidence_from_map(
        "helper",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
        deadline_monotonic=None,
    )

    assert recorded == [None, None], recorded
    assert omitted == explicit_none, (omitted, explicit_none)
    related, evidence, unreliable = omitted
    assert evidence["status"] == "collected"
    assert evidence["symbol"] == "helper"
    assert unreliable is False
    assert related and Path(related[0]["file"]).name == "caller.py", related
