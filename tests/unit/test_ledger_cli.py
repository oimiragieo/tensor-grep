"""CLI-level tests for `tg ledger claim / release / list` (exit codes, JSON envelope).

Uses Typer's CliRunner against the real `app` object -- appropriate here because these tests
exercise the Typer COMMAND BODIES (argument parsing, envelope shape, exit-code mapping), not
the bootstrap front-door ROUTING decision. Routing (does `tg ledger ...` reach the Typer app
at all, both in-process and through the native binary) is a SEPARATE concern covered by
`commands.KNOWN_COMMANDS` + `tests/e2e/test_routing_parity.py::PUBLIC_TOP_LEVEL_COMMANDS` and
must be verified against the real installed/built binary (dogfood), never CliRunner alone --
see AGENTS.md "Dogfood the Real Binary, Not CliRunner".
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _make_project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def test_ledger_claim_json_envelope_and_exit_zero(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    assert payload["routing_backend"] == "Ledger"
    assert payload["routing_reason"] == "ledger-claim"
    assert payload["sidecar_used"] is False
    assert payload["advisory"] is True
    assert payload["ledger_schema_version"] == 1
    assert payload["claim"]["agent_id"] == "agent-a"
    assert payload["claim"]["symbols"] == ["value"]
    assert payload["overlaps"] == []


def test_ledger_claim_repeatable_symbol_and_comma_files(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    (root / "other.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "ledger",
            "claim",
            str(root),
            "--symbol",
            "alpha",
            "--symbol",
            "beta",
            "--files",
            "mod.py,other.py",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["claim"]["symbols"] == ["alpha", "beta"]
    assert payload["claim"]["files"] == ["mod.py", "other.py"]


def test_ledger_claim_overlap_reported_but_exit_zero(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    first = runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )
    assert first.exit_code == 0

    second = runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-b", "--json"],
    )
    assert second.exit_code == 0, second.output  # ADVISORY: overlap never blocks
    payload = json.loads(second.stdout)
    assert len(payload["overlaps"]) == 1
    assert payload["overlaps"][0]["agent_id"] == "agent-a"


def test_ledger_claim_missing_target_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "claim", str(root), "--agent-id", "agent-a", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "Ledger"
    assert payload["error"]["code"] == "fail_closed"
    assert not (root / ".tensor-grep").exists()


def test_ledger_claim_traversal_exits_two_and_writes_nothing(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "ledger",
            "claim",
            str(root),
            "--files",
            "../escape.py",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"
    assert not (root / ".tensor-grep").exists()


def test_ledger_claim_text_mode_smoke(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app, ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a"]
    )
    assert result.exit_code == 0, result.output
    assert "recorded for agent=agent-a" in result.stdout
    assert "overlaps: none" in result.stdout


def test_ledger_release_by_claim_id_json(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    claimed = runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )
    claim_id = json.loads(claimed.stdout)["claim"]["claim_id"]

    released = runner.invoke(
        app, ["ledger", "release", str(root), "--claim-id", claim_id, "--json"]
    )
    assert released.exit_code == 0, released.output
    payload = json.loads(released.stdout)
    assert payload["routing_reason"] == "ledger-release"
    assert payload["released_count"] == 1
    assert payload["released"][0]["claim_id"] == claim_id


def test_ledger_release_by_symbol_json(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )

    released = runner.invoke(
        app,
        ["ledger", "release", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )
    assert released.exit_code == 0, released.output
    payload = json.loads(released.stdout)
    assert payload["released_count"] == 1


def test_ledger_release_no_match_still_exits_zero(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app, ["ledger", "release", str(root), "--claim-id", "claim-nonexistent", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["released_count"] == 0


def test_ledger_release_missing_selector_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "release", str(root), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"


def test_ledger_list_json_envelope(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "alpha", "--agent-id", "agent-a", "--json"],
    )
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "beta", "--agent-id", "agent-b", "--json"],
    )

    result = runner.invoke(app, ["ledger", "list", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "ledger-list"
    assert payload["count"] == 2
    assert {c["agent_id"] for c in payload["claims"]} == {"agent-a", "agent-b"}


def test_ledger_list_empty_exits_zero(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "list", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["claims"] == []
    assert payload["count"] == 0


def test_ledger_list_filters_by_symbol_and_agent(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "alpha", "--agent-id", "agent-a", "--json"],
    )
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "beta", "--agent-id", "agent-b", "--json"],
    )

    by_symbol = runner.invoke(app, ["ledger", "list", str(root), "--symbol", "alpha", "--json"])
    assert json.loads(by_symbol.stdout)["count"] == 1

    by_agent = runner.invoke(app, ["ledger", "list", str(root), "--agent-id", "agent-b", "--json"])
    assert json.loads(by_agent.stdout)["count"] == 1
    assert json.loads(by_agent.stdout)["claims"][0]["agent_id"] == "agent-b"


def test_ledger_list_text_mode_no_claims(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "list", str(root)])
    assert result.exit_code == 0, result.output
    assert "No live claims." in result.stdout


def test_ledger_top_level_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["ledger", "--help"])
    assert result.exit_code == 0, result.output
    assert "claim" in result.stdout
    assert "release" in result.stdout
    assert "list" in result.stdout


def test_ledger_registered_in_known_commands() -> None:
    from tensor_grep.cli.commands import KNOWN_COMMANDS

    assert "ledger" in KNOWN_COMMANDS
