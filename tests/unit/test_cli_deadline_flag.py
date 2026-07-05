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
