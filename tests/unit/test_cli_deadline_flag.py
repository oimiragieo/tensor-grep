"""Moat P0-6 step 4: the --deadline CLI flag threads deadline_seconds into the symbol builders.

End-to-end (partial:true JSON) is dogfooded against the real binary; this is a fast regression guard
that the flag exists on all 4 commands and forwards the value (or None when absent).
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import tensor_grep.cli.agent_capsule as agent_capsule
import tensor_grep.cli.orient_capsule as orient_capsule
import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import app


def _stub_payload(symbol: str, path: str) -> dict:
    return {
        "symbol": symbol,
        "path": str(path),
        "callers": [],
        "files": [],
        "tests": [],
        "related_paths": [],
        "definitions": [],
        "symbols": [],
        "imports": [],
        "routing_reason": "symbol-callers",
    }


def test_callers_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(symbol, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_payload(symbol, path)

    monkeypatch.setattr(repo_map, "build_symbol_callers", _spy)
    result = CliRunner().invoke(app, ["callers", "foo", str(tmp_path), "--deadline", "5", "--json"])
    assert result.exit_code in (0, 1), result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_callers_without_deadline_passes_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_seconds": "sentinel"}

    def _spy(symbol, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_payload(symbol, path)

    monkeypatch.setattr(repo_map, "build_symbol_callers", _spy)
    result = CliRunner().invoke(app, ["callers", "foo", str(tmp_path), "--json"])
    assert result.exit_code in (0, 1), result.output
    assert recorded.get("deadline_seconds") is None


def test_deadline_flag_accepted_on_all_four_graph_commands() -> None:
    # Robust to --help text wrapping (which is terminal-WIDTH dependent -> CI vs local diverge, the
    # same fragility as the earlier --daemon help test): assert the flag is REGISTERED by passing it
    # with --help. An UNKNOWN option exits 2 before eager --help fires; a KNOWN option is consumed,
    # then --help exits 0. So exit_code == 0 proves --deadline exists without parsing help text.
    runner = CliRunner()
    for command in ("callers", "refs", "impact", "blast-radius"):
        result = runner.invoke(app, [command, "--deadline", "5", "--help"])
        assert result.exit_code == 0, f"{command} rejected --deadline: {result.output}"


def test_deadline_rejects_sub_floor_value(tmp_path: Path) -> None:
    # min=0.1: a sub-floor deadline is a usage error (exit 2), not a silent 0-budget run.
    result = CliRunner().invoke(app, ["callers", "foo", str(tmp_path), "--deadline", "0.001"])
    assert result.exit_code == 2


def _exit_code_for_payload(tmp_path, monkeypatch, payload_extra: dict) -> int:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _spy(symbol, path=".", **_kwargs):
        p = _stub_payload(symbol, str(path))
        p.update(payload_extra)
        return p

    monkeypatch.setattr(repo_map, "build_symbol_callers", _spy)
    return CliRunner().invoke(app, ["callers", "foo", str(tmp_path), "--json"]).exit_code


def test_deadline_partial_empty_exits_2_not_1(tmp_path: Path, monkeypatch) -> None:
    # Exit-code contract (dogfood 1.40.0): a --deadline-truncated result (partial:true) that found
    # nothing is INCOMPLETE (exit 2 = "retry with more budget"), NOT a genuine not-found (exit 1).
    assert _exit_code_for_payload(tmp_path, monkeypatch, {"callers": [], "partial": True}) == 2


def test_result_incomplete_empty_exits_2(tmp_path: Path, monkeypatch) -> None:
    # A max-repo-files-truncated empty result is likewise incomplete -> exit 2 (mirrors tg search).
    assert (
        _exit_code_for_payload(tmp_path, monkeypatch, {"callers": [], "result_incomplete": True})
        == 2
    )


def test_genuine_not_found_still_exits_1(tmp_path: Path, monkeypatch) -> None:
    # A COMPLETE scan that found nothing is a real not-found -> exit 1 (unchanged, rg convention).
    assert _exit_code_for_payload(tmp_path, monkeypatch, {"callers": []}) == 1


def test_complete_found_exits_0(tmp_path: Path, monkeypatch) -> None:
    assert (
        _exit_code_for_payload(tmp_path, monkeypatch, {"callers": [{"file": "m.py", "line": 1}]})
        == 0
    )


def test_blast_radius_partial_exits_2(tmp_path: Path, monkeypatch) -> None:
    # cursor review 1.40.0: blast-radius bypassed _emit_symbol_command_result and exited 0 even on a
    # --deadline partial. It must exit 2 (incomplete) like callers.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _spy(symbol, path=".", **_kwargs):
        return {
            "symbol": symbol,
            "path": str(path),
            "definitions": [{"file": "m.py", "line": 1}],
            "callers": [],
            "files": [],
            "tests": [],
            "partial": True,
            "deadline_limit": {"deadline_exceeded": True},
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _spy)
    result = CliRunner().invoke(app, ["blast-radius", "foo", str(tmp_path), "--json"])
    assert result.exit_code == 2, result.output


def test_impact_propagates_caller_scan_partial_exits_2(tmp_path: Path, monkeypatch) -> None:
    # cursor review 1.40.0: impact's second caller-scan pass can be deadline-truncated; impact must
    # carry that partial signal so it exits 2 like `tg callers`.
    #
    # task #103: impact() now builds ONE shared repo_map and calls the *_from_map variants
    # directly (build_symbol_impact_from_map / build_symbol_callers_from_map) instead of the
    # build_symbol_impact/build_symbol_callers wrappers -- mock at the new seam, since the old
    # wrapper names are no longer imported/called by the CLI handler at all.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _impact_from_map(repo_map_arg, symbol, **_kwargs):
        # impact FOUND files, but its second caller-scan pass is deadline-truncated -> impact must carry
        # that partial signal and exit 2 EVEN THOUGH it found files (council-verified B: truncation trumps
        # found), so an agent never trusts a truncated impact set as exhaustive.
        return {
            "symbol": symbol,
            "path": str(repo_map_arg.get("path", ".")),
            "files": ["m.py"],
            "tests": [],
            "callers": [],
            "no_match": False,
        }

    def _callers_from_map(repo_map_arg, symbol, **_kwargs):
        return {"callers": [], "partial": True, "deadline_limit": {"deadline_exceeded": True}}

    monkeypatch.setattr(repo_map, "build_symbol_impact_from_map", _impact_from_map)
    monkeypatch.setattr(repo_map, "build_symbol_callers_from_map", _callers_from_map)
    result = CliRunner().invoke(app, ["impact", "foo", str(tmp_path), "--json"])
    assert result.exit_code == 2, result.output


def test_found_with_partial_exits_2(tmp_path: Path, monkeypatch) -> None:
    # Council-verified B (2026-07-05): truncation trumps found -- a --deadline/cap-truncated result
    # exits 2 EVEN WITH findings, so an agent never trusts a truncated caller-set as exhaustive. (The
    # found->exit-0 narrowing was tried in #399 and overturned by a unanimous design council.)
    assert (
        _exit_code_for_payload(
            tmp_path, monkeypatch, {"callers": [{"file": "m.py", "line": 1}], "partial": True}
        )
        == 2
    )


def test_found_with_result_incomplete_exits_2(tmp_path: Path, monkeypatch) -> None:
    assert (
        _exit_code_for_payload(
            tmp_path,
            monkeypatch,
            {"callers": [{"file": "m.py", "line": 1}], "result_incomplete": True},
        )
        == 2
    )


def test_blast_radius_found_partial_exits_2(tmp_path: Path, monkeypatch) -> None:
    # Council-verified B: a scan-truncated blast radius exits 2 even when callers were resolved.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _spy(symbol, path=".", **_kwargs):
        return {
            "symbol": symbol,
            "path": str(path),
            "definitions": [{"file": "m.py", "line": 1}],
            "callers": [{"file": "m.py", "line": 1}],
            "files": ["m.py"],
            "tests": [],
            "partial": True,
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _spy)
    result = CliRunner().invoke(app, ["blast-radius", "foo", str(tmp_path), "--json"])
    assert result.exit_code == 2, result.output


# ==================================================================================================
# CEO v1.71.3 dogfood gap (HIGH): --deadline is defined on refs/callers/impact/blast-radius/
# importers/inventory/codemap but was MISSING from the repo-scanning commands agent/edit-plan/
# context/context-render/map/orient (and defs) -- an agent that learned --deadline works on
# `tg callers` and passed it to `tg agent`/`tg orient`/etc got a Click "No such option" exit-2,
# burning the agent loop. Additive-only: default stays None (NOT codemap's 60s), so behavior is
# UNCHANGED unless --deadline is explicitly passed.
# ==================================================================================================

_REPO_SCANNING_DEADLINE_COMMANDS = (
    "agent",
    "edit-plan",
    "context",
    "context-render",
    "map",
    "orient",
    "defs",
)

# Commands that get the codemap-style --deadline/--no-deadline PAIR (default None, unlike codemap's
# 60s -- --no-deadline is accepted as a no-op default-explicit). `defs` deliberately does NOT get
# --no-deadline: it mirrors its true siblings refs/callers/impact/blast-radius, which default
# --deadline to None already and have no --no-deadline companion.
_PAIRED_DEADLINE_COMMANDS = ("agent", "edit-plan", "context", "context-render", "map", "orient")


def _stub_repo_map_payload(path: str) -> dict:
    return {
        "path": str(path),
        "files": [],
        "tests": [],
        "symbols": [],
        "imports": [],
        "related_paths": [],
    }


def _stub_defs_payload(symbol: str, path: str) -> dict:
    return {
        "symbol": symbol,
        "path": str(path),
        "definitions": [],
        "files": [],
        "tests": [],
        "related_paths": [],
        "symbols": [],
        "imports": [],
        "routing_reason": "symbol-defs",
    }


def test_deadline_flag_accepted_on_repo_scanning_commands() -> None:
    # Was exit 2 "No such option" on all 7 of these before this fix -- an UNKNOWN option exits 2
    # before eager --help fires (see test_deadline_flag_accepted_on_all_four_graph_commands above),
    # so exit_code == 0 proves --deadline is now a REGISTERED option.
    runner = CliRunner()
    for command in _REPO_SCANNING_DEADLINE_COMMANDS:
        result = runner.invoke(app, [command, "--deadline", "5", "--help"])
        assert result.exit_code == 0, f"{command} rejected --deadline: {result.output}"


def test_no_deadline_flag_accepted_on_paired_commands() -> None:
    runner = CliRunner()
    for command in _PAIRED_DEADLINE_COMMANDS:
        result = runner.invoke(app, [command, "--no-deadline", "--help"])
        assert result.exit_code == 0, f"{command} rejected --no-deadline: {result.output}"


def test_defs_has_no_no_deadline_companion() -> None:
    # defs mirrors refs/callers/impact/blast-radius (--deadline already defaults to None), not
    # codemap's 60s-default pair -- --no-deadline is genuinely unregistered on defs.
    result = CliRunner().invoke(app, ["defs", "--no-deadline", "--help"])
    assert result.exit_code == 2, result.output


def test_repo_scanning_commands_reject_sub_floor_deadline(tmp_path: Path) -> None:
    # min=0.1 on every one of the 7 commands: a sub-floor deadline is a usage error (exit 2), not a
    # silent 0-budget run. Click validates option constraints during parsing, before the command
    # body (and thus any missing-positional handling) ever runs.
    runner = CliRunner()
    extra_positional = {
        "agent": ["q"],
        "edit-plan": ["q"],
        "context": ["q"],
        "context-render": ["q"],
        "defs": ["foo"],
        "map": [],
        "orient": [],
    }
    for command in _REPO_SCANNING_DEADLINE_COMMANDS:
        result = runner.invoke(
            app, [command, str(tmp_path), *extra_positional[command], "--deadline", "0.001"]
        )
        assert result.exit_code == 2, f"{command}: {result.output}"


def test_map_deadline_flag_threads_to_build_repo_map(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(path, *, max_repo_files=None, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return _stub_repo_map_payload(path)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    result = CliRunner().invoke(app, ["map", str(tmp_path), "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_monotonic") is not None


def test_map_without_deadline_passes_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_monotonic": "sentinel"}

    def _spy(path, *, max_repo_files=None, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return _stub_repo_map_payload(path)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    result = CliRunner().invoke(app, ["map", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_monotonic") is None


def test_map_no_deadline_flag_stays_none(tmp_path: Path, monkeypatch) -> None:
    # --no-deadline is a no-op default-explicit here (map already defaults to unbounded).
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_monotonic": "sentinel"}

    def _spy(path, *, max_repo_files=None, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return _stub_repo_map_payload(path)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    result = CliRunner().invoke(app, ["map", str(tmp_path), "--no-deadline", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_monotonic") is None


def test_orient_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {"path": str(path)}

    monkeypatch.setattr(orient_capsule, "build_orient_capsule", _spy)
    result = CliRunner().invoke(app, ["orient", str(tmp_path), "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_orient_without_deadline_passes_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_seconds": "sentinel"}

    def _spy(path, *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {"path": str(path)}

    monkeypatch.setattr(orient_capsule, "build_orient_capsule", _spy)
    result = CliRunner().invoke(app, ["orient", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") is None


def test_context_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {
            "path": str(path),
            "query": query,
            "files": [],
            "tests": [],
            "symbols": [],
            "imports": [],
        }

    monkeypatch.setattr(repo_map, "build_context_pack", _spy)
    result = CliRunner().invoke(app, ["context", str(tmp_path), "q", "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_context_without_deadline_passes_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_seconds": "sentinel"}

    def _spy(query, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {
            "path": str(path),
            "query": query,
            "files": [],
            "tests": [],
            "symbols": [],
            "imports": [],
        }

    monkeypatch.setattr(repo_map, "build_context_pack", _spy)
    result = CliRunner().invoke(app, ["context", str(tmp_path), "q", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") is None


def test_context_render_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {"path": str(path), "query": query, "render_profile": "llm", "rendered_context": ""}

    monkeypatch.setattr(repo_map, "build_context_render", _spy)
    result = CliRunner().invoke(
        app, ["context-render", str(tmp_path), "q", "--deadline", "5", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_agent_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {"path": str(path), "query": query}

    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _spy)
    result = CliRunner().invoke(app, ["agent", str(tmp_path), "q", "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_edit_plan_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return {"path": str(path), "query": query, "files": [], "tests": [], "symbols": []}

    monkeypatch.setattr(repo_map, "build_context_edit_plan", _spy)
    result = CliRunner().invoke(app, ["edit-plan", str(tmp_path), "q", "--deadline", "5", "--json"])
    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_defs_deadline_flag_threads_seconds(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}

    def _spy(symbol, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_defs_payload(symbol, path)

    monkeypatch.setattr(repo_map, "build_symbol_defs", _spy)
    result = CliRunner().invoke(app, ["defs", "foo", str(tmp_path), "--deadline", "5", "--json"])
    assert result.exit_code in (0, 1), result.output
    assert recorded.get("deadline_seconds") == 5.0


def test_defs_without_deadline_passes_none(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {"deadline_seconds": "sentinel"}

    def _spy(symbol, path=".", *, deadline_seconds=None, **_kwargs):
        recorded["deadline_seconds"] = deadline_seconds
        return _stub_defs_payload(symbol, path)

    monkeypatch.setattr(repo_map, "build_symbol_defs", _spy)
    result = CliRunner().invoke(app, ["defs", "foo", str(tmp_path), "--json"])
    assert result.exit_code in (0, 1), result.output
    assert recorded.get("deadline_seconds") is None


# --- builder-level: the new deadline_seconds param on each top-level wrapper actually reaches
# build_repo_map's deadline_monotonic (mirrors test_step52_build_symbol_impact_forwards_deadline_to_
# from_map in test_repo_map_deadline.py) -------------------------------------------------------


def test_build_context_pack_forwards_deadline_to_build_repo_map(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    repo_map.build_context_pack("f", str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_build_context_edit_plan_forwards_deadline_to_build_repo_map(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    repo_map.build_context_edit_plan("f", str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_build_context_render_forwards_deadline_to_build_repo_map(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    repo_map.build_context_render("f", str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_build_symbol_defs_forwards_deadline_to_build_repo_map(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    repo_map.build_symbol_defs("f", str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_build_orient_capsule_forwards_deadline_to_build_repo_map(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(orient_capsule._repo_map, "build_repo_map", _spy)
    orient_capsule.build_orient_capsule(str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_build_agent_capsule_forwards_deadline_to_build_repo_map(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    recorded: dict = {}
    original = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(path, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_repo_map", _spy)
    agent_capsule.build_agent_capsule("f", str(tmp_path), deadline_seconds=5.0)
    assert recorded.get("deadline_monotonic") is not None


def test_orient_capsule_partial_signal_surfaces_informationally(tmp_path: Path) -> None:
    # tg orient has NO exit-2 contract (docs/CONTRACTS.md:110) -- a --deadline truncation must
    # still surface partial/deadline_limit as INFORMATIONAL fields (never silently dropped), but
    # must NOT flip orient's documented always-exit-0 behavior.
    import time

    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = orient_capsule.build_orient_capsule(str(tmp_path), deadline_seconds=None)
    assert "partial" not in payload  # golden-parity: no deadline -> unchanged shape

    rm = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=time.monotonic() - 1.0)
    truncated_payload = orient_capsule.build_orient_capsule_from_map(rm)
    assert truncated_payload.get("partial") is True
    assert truncated_payload["deadline_limit"]["deadline_exceeded"] is True
