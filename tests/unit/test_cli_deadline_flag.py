"""Moat P0-6 step 4: the --deadline CLI flag threads deadline_seconds into the symbol builders.

End-to-end (partial:true JSON) is dogfooded against the real binary; this is a fast regression guard
that the flag exists on all 4 commands and forwards the value (or None when absent).
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

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
