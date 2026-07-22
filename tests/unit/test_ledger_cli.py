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
    assert "No live claims" in result.stdout
    assert "descendant subtrees" in result.stdout


# ========================================================================================
# PATH-scope footgun fix (CEO v1.92.1 dogfood #1): CLI-level reproduction of the exact
# reported sequence, `unmatched_reason`/`live_claims_elsewhere` release honesty, and the
# `scope` field on claim/list output.
# ========================================================================================


def test_ledger_claim_json_includes_scope_field(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["claim"]["scope"] == "."


def test_ledger_dogfood_repro_claim_subpath_then_bare_list(tmp_path: Path, monkeypatch) -> None:
    """THE literal CLI dogfood repro: `tg ledger claim core/hooks ...` then bare `tg ledger
    list` (default PATH `.`), invoked from the repo root's own cwd exactly as a real agent
    would type it. Pre-fix this returned an EMPTY claims list."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    monkeypatch.chdir(root)

    claimed = runner.invoke(
        app,
        [
            "ledger",
            "claim",
            "core/hooks",
            "--symbol",
            "open_session",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )
    assert claimed.exit_code == 0, claimed.output
    assert json.loads(claimed.stdout)["claim"]["scope"] == "core/hooks"

    listed = runner.invoke(app, ["ledger", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.stdout)
    assert payload["count"] == 1
    assert payload["claims"][0]["scope"] == "core/hooks"

    listed_explicit_dot = runner.invoke(app, ["ledger", "list", ".", "--json"])
    assert json.loads(listed_explicit_dot.stdout)["count"] == 1


def test_ledger_release_right_selector_succeeds_from_different_subpath(
    tmp_path: Path, monkeypatch
) -> None:
    """The release half of the dogfood repro: releasing by `--symbol`/`--agent-id` from a
    DIFFERENT subpath than the claim now succeeds (pre-fix: `released_count: 0`, claim
    silently lived on)."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    monkeypatch.chdir(root)

    runner.invoke(
        app,
        [
            "ledger",
            "claim",
            "core/hooks",
            "--symbol",
            "open_session",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )

    released = runner.invoke(
        app,
        ["ledger", "release", ".", "--symbol", "open_session", "--agent-id", "agent-a", "--json"],
    )
    assert released.exit_code == 0, released.output
    payload = json.loads(released.stdout)
    assert payload["released_count"] == 1
    assert payload["unmatched_reason"] is None
    assert payload["live_claims_elsewhere"] == []


def test_ledger_release_no_match_json_includes_honesty_fields(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )

    result = runner.invoke(
        app,
        [
            "ledger",
            "release",
            str(root),
            "--symbol",
            "nonexistent",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["released_count"] == 0
    assert payload["listed_scope"] == "."
    assert payload["unmatched_reason"] is not None
    assert payload["live_claims_elsewhere_count"] == 1
    assert len(payload["live_claims_elsewhere"]) == 1
    assert payload["live_claims_elsewhere"][0]["symbols"] == ["value"]
    assert payload["live_claims_elsewhere_truncated"] is False


def test_ledger_release_no_match_text_mode_names_live_claims_elsewhere(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    runner.invoke(
        app,
        ["ledger", "claim", str(root), "--symbol", "value", "--agent-id", "agent-a", "--json"],
    )

    result = runner.invoke(
        app, ["ledger", "release", str(root), "--symbol", "nonexistent", "--agent-id", "agent-a"]
    )
    assert result.exit_code == 0, result.output
    assert "No matching live claim found" in result.stdout
    assert "live claim(s) exist elsewhere" in result.stdout
    assert "value" in result.stdout  # the OTHER claim's symbol is actually named


def test_ledger_release_no_match_on_empty_ledger_text_mode(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "release", str(root), "--claim-id", "claim-nope"])
    assert result.exit_code == 0, result.output
    assert "No live claims exist for this repository." in result.stdout


def test_ledger_help_text_states_path_scoping_rule() -> None:
    """Ask 3: help text loudly states the PATH-scoping/rollup rule."""
    group_help = runner.invoke(app, ["ledger", "--help"])
    assert group_help.exit_code == 0, group_help.output
    assert "canonicalize" in group_help.stdout
    assert "rolls up" in group_help.stdout

    claim_help = runner.invoke(app, ["ledger", "claim", "--help"])
    assert claim_help.exit_code == 0, claim_help.output
    assert "scope" in claim_help.stdout.lower()

    list_help = runner.invoke(app, ["ledger", "list", "--help"])
    assert list_help.exit_code == 0, list_help.output
    assert "rolls up" in list_help.stdout.lower() or "rollup" in list_help.stdout.lower()

    release_help = runner.invoke(app, ["ledger", "release", "--help"])
    assert release_help.exit_code == 0, release_help.output
    assert "does not filter" in release_help.stdout.lower() or "never filter" in (
        release_help.stdout.lower()
    )


def test_ledger_top_level_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["ledger", "--help"])
    assert result.exit_code == 0, result.output
    assert "claim" in result.stdout
    assert "release" in result.stdout
    assert "list" in result.stdout


def test_ledger_registered_in_known_commands() -> None:
    from tensor_grep.cli.commands import KNOWN_COMMANDS

    assert "ledger" in KNOWN_COMMANDS


# ========================================================================================
# Slice 2: findings -- tg ledger record / find (exit codes, JSON envelope)
# ========================================================================================


def _git_init(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _write_artifact_json(tmp_path: Path, name: str, payload: dict) -> Path:
    artifact_path = tmp_path / name
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def test_ledger_record_json_envelope_and_exit_zero(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = runner.invoke(
        app,
        [
            "ledger",
            "record",
            str(root),
            "--receipt",
            str(artifact),
            "--symbol",
            "value",
            "--agent-id",
            "agent-a",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    assert payload["routing_backend"] == "Ledger"
    assert payload["routing_reason"] == "ledger-record"
    assert payload["sidecar_used"] is False
    assert payload["advisory"] is True
    assert payload["ledger_schema_version"] == 1
    assert payload["finding"]["agent_id"] == "agent-a"
    assert payload["finding"]["symbol"] == "value"
    assert payload["finding"]["artifact_kind"] == "evidence-receipt"
    assert payload["finding"]["signed"] is False


def test_ledger_record_missing_receipt_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "record", str(root), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "Ledger"
    assert payload["error"]["code"] == "fail_closed"
    assert not (root / ".tensor-grep").exists()


def test_ledger_record_bad_artifact_kind_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = runner.invoke(
        app,
        [
            "ledger",
            "record",
            str(root),
            "--receipt",
            str(artifact),
            "--artifact-kind",
            "not-a-kind",
            "--json",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"


def test_ledger_record_nonexistent_receipt_file_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(tmp_path / "nope.json"), "--json"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"


def test_ledger_record_text_mode_smoke(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = runner.invoke(
        app, ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "value"]
    )
    assert result.exit_code == 0, result.output
    assert "recorded" in result.stdout


def test_ledger_record_dedup_same_artifact_twice(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"same": "content"})
    first = runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "alpha", "--json"],
    )
    second = runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "beta", "--json"],
    )
    assert first.exit_code == 0
    assert second.exit_code == 0
    sha_first = json.loads(first.stdout)["finding"]["receipt_sha256"]
    sha_second = json.loads(second.stdout)["finding"]["receipt_sha256"]
    assert sha_first == sha_second
    blobs_dir = root / ".tensor-grep" / "ledger" / "findings" / "blobs"
    assert len(list(blobs_dir.iterdir())) == 1


def test_ledger_find_exit_zero_on_fresh_hit(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "value", "--json"],
    )

    result = runner.invoke(app, ["ledger", "find", str(root), "--symbol", "value", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "ledger-find"
    assert payload["any_fresh"] is True
    assert payload["count"] == 1
    assert payload["findings"][0]["fresh"] is True


def test_ledger_find_exit_one_on_no_fresh_hit_with_fresh_only(tmp_path: Path) -> None:
    root = _make_project(tmp_path)  # never git-inited -- revision unavailable -> never fresh
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "value", "--json"],
    )

    result = runner.invoke(
        app, ["ledger", "find", str(root), "--symbol", "value", "--fresh-only", "--json"]
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["any_fresh"] is False
    assert payload["count"] == 0


def test_ledger_find_exit_one_on_no_match_at_all(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "find", str(root), "--symbol", "nonexistent", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert payload["any_fresh"] is False


def test_ledger_find_exit_two_on_tampered_blob(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    recorded = runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "value", "--json"],
    )
    sha = json.loads(recorded.stdout)["finding"]["receipt_sha256"]
    blob_path = root / ".tensor-grep" / "ledger" / "findings" / "blobs" / f"{sha}.json"
    blob_path.write_text(json.dumps({"a": "tampered"}), encoding="utf-8")

    result = runner.invoke(app, ["ledger", "find", str(root), "--symbol", "value", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"


def test_ledger_find_missing_symbol_exits_two(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "find", str(root), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "fail_closed"


def test_ledger_find_text_mode_no_findings(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = runner.invoke(app, ["ledger", "find", str(root), "--symbol", "nonexistent"])
    assert result.exit_code == 1, result.output
    assert "No matching findings." in result.stdout


def test_ledger_find_text_mode_lists_findings(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    runner.invoke(
        app,
        ["ledger", "record", str(root), "--receipt", str(artifact), "--symbol", "value", "--json"],
    )

    result = runner.invoke(app, ["ledger", "find", str(root), "--symbol", "value"])
    assert result.exit_code == 0, result.output
    assert "fresh=True" in result.stdout


def test_ledger_top_level_help_lists_record_and_find() -> None:
    result = runner.invoke(app, ["ledger", "--help"])
    assert result.exit_code == 0, result.output
    assert "record" in result.stdout
    assert "find" in result.stdout
