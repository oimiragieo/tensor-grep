"""Unit tests for Wave 3 Track L (DF-12) CLI & MCP flag/UX consistency sweep."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import ast_workflows, mcp_server
from tensor_grep.cli.main import app
from tensor_grep.cli.prepare_format import PREPARE_COMPACT_MAX_BYTES, format_prepare_compact_json

runner = CliRunner()


def _sample_prepare_payload(*, bloat: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "prepare",
        "prepare_version": 1,
        "path": "src/billing.py",
        "query": "compute_tax",
        "primary_target": {
            "file": "src/billing.py",
            "line": 12,
            "end_line": 20,
            "symbol": "compute_tax",
            "kind": "function",
        },
        "alternative_targets": (
            [{"file": f"src/alt_{i}.py", "line": i, "symbol": f"alt_{i}"} for i in range(60)]
            if bloat
            else []
        ),
        "confidence": {"overall": 0.92, "reasons": ["exact symbol match"]},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "validation_plan": [{"command": "uv run pytest -q"}] * (40 if bloat else 1),
        "validation_commands": ["uv run pytest -q"] * (40 if bloat else 1),
        "context_consistency": {"status": "fresh", "mode": "cold", "stale": False},
        "rollback": {"supported": True, "command": "tg checkpoint create"},
        "blast_radius_floor": {
            "callers_count": 3,
            "source": "repo_map",
            "truncated": False,
            "callers": [
                {"file": f"src/caller_{i}.py", "line": i} for i in range(80 if bloat else 2)
            ],
        },
        "coordination": {
            "claim": {"submitted": False, "argv": ["tg", "ledger", "claim"]},
            "evidence": {"note": "save capsule" * (200 if bloat else 1)},
        },
        "next_action": {
            "action": {
                "type": "edit_file",
                "path": "src/billing.py",
                "span": {"start_line": 12, "end_line": 20},
                "instruction": "Edit compute_tax" + (" detail" * (300 if bloat else 1)),
            },
            "on_success": {"type": "run", "argv": ["uv", "run", "pytest", "-q"]},
            "on_failure": {"type": "narrow_scope", "suggested_path": "src"},
        },
    }
    return payload


def test_prepare_compact_json_bounded_and_preserves_required_fields() -> None:
    compact_str = format_prepare_compact_json(_sample_prepare_payload(bloat=True))
    assert len(compact_str.encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    parsed = json.loads(compact_str)
    for key in (
        "version",
        "schema_version",
        "routing_backend",
        "routing_reason",
        "prepare_version",
        "path",
        "query",
        "primary_target",
        "next_action",
        "blast_radius_floor",
        "context_consistency",
        "ask_user_before_editing",
        "confidence",
        "rollback",
        "validation_commands",
    ):
        assert key in parsed, f"missing mandatory key {key}"
    assert parsed["compact_truncated"] is True


def test_prepare_compact_json_enforces_cap_on_oversized_query_path_and_top_callers() -> None:
    payload = _sample_prepare_payload(bloat=True)
    payload["path"] = "a/" * 3000
    payload["query"] = "find_symbol " * 500
    payload["blast_radius_floor"] = {
        "callers_count": 50,
        "source": "supplementary_blast_radius",
        "possibly_incomplete": True,
        "top_callers": [
            {"file": f"src/caller_{i}.py", "line": i, "symbol": f"fn_{i}"} for i in range(50)
        ],
    }
    compact_str = format_prepare_compact_json(payload)
    assert len(compact_str.encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    parsed = json.loads(compact_str)
    assert parsed["compact_truncated"] is True
    assert parsed["blast_radius_floor"]["possibly_incomplete"] is True
    assert "top_callers_omitted" in parsed["blast_radius_floor"]
    for key in (
        "ask_user_before_editing",
        "confidence",
        "rollback",
        "validation_commands",
        "next_action",
    ):
        assert key in parsed


def test_prepare_cli_text_default_and_json_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "mod.py"
    target.write_text("def compute_tax(x):\n    return x * 1.1\n", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.cli.main._build_prepare_payload",
        lambda **_kwargs: _sample_prepare_payload(bloat=True),
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.prepare_service._build_prepare_payload",
        lambda **_kwargs: _sample_prepare_payload(bloat=True),
    )

    text_res = runner.invoke(app, ["prepare", str(tmp_path), "compute_tax"])
    assert text_res.exit_code == 0, text_res.output
    assert "primary=" in text_res.output
    assert not text_res.output.lstrip().startswith("{")

    json_res = runner.invoke(app, ["prepare", str(tmp_path), "compute_tax", "--json"])
    assert json_res.exit_code == 0, json_res.output
    full_parsed = json.loads(json_res.output)
    assert full_parsed["primary_target"]["symbol"] == "compute_tax"

    compact_res = runner.invoke(
        app, ["prepare", str(tmp_path), "compute_tax", "--json", "--compact"]
    )
    assert compact_res.exit_code == 0, compact_res.output
    assert len(compact_res.output.strip().encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    compact_parsed = json.loads(compact_res.output)
    assert compact_parsed["primary_target"]["symbol"] == "compute_tax"

    bad_compact = runner.invoke(app, ["prepare", str(tmp_path), "compute_tax", "--compact"])
    assert bad_compact.exit_code != 0


def test_prepare_compact_json_never_truncates_or_synthesizes_actionable_fields() -> None:
    payload = _sample_prepare_payload(bloat=True)
    long_cmd = "tg checkpoint create --label " + ("x" * 200)
    long_ckpt = "ckpt-" + ("a1b2c3d4" * 25)
    long_val = "uv run pytest -q tests/unit/" + ("long_name_" * 20) + ".py"
    long_file = "src/deep/" + ("pkg/" * 50) + "billing.py"
    long_sym = "compute_tax_" + ("variant_" * 25)
    long_instr = "Edit compute_tax " + ("carefully_" * 300)
    payload["rollback"] = {"supported": True, "command": long_cmd, "checkpoint_id": long_ckpt}
    payload["validation_commands"] = [long_val, "uv run pytest -q"]
    payload["primary_target"]["file"] = long_file
    payload["primary_target"]["symbol"] = long_sym
    payload["next_action"]["action"]["path"] = long_file
    payload["next_action"]["action"]["instruction"] = long_instr
    payload["blast_radius_floor"] = {
        "callers_count": 3,
        "source": "repo_map",
        "truncated": False,
        "top_callers": [
            {"file": long_file, "line": 10, "symbol": "caller_a"},
            {"file": "src/caller_b.py", "line": 20, "symbol": long_sym},
            {"file": "src/caller_c.py", "line": 30, "symbol": "caller_c"},
        ],
    }

    compact_str = format_prepare_compact_json(payload)
    assert len(compact_str.encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    parsed = json.loads(compact_str)
    assert parsed["compact_truncated"] is True
    assert "..." not in parsed["rollback"].get("command", "")
    assert "..." not in parsed["rollback"].get("checkpoint_id", "")
    assert "..." not in parsed["primary_target"].get("file", "")
    assert "..." not in parsed["primary_target"].get("symbol", "")
    assert all("..." not in cmd for cmd in parsed.get("validation_commands", []))
    assert "..." not in parsed.get("next_action", {}).get("action", {}).get("path", "")
    assert "..." not in parsed.get("next_action", {}).get("action", {}).get("instruction", "")
    for caller in parsed.get("blast_radius_floor", {}).get("top_callers", []):
        assert "..." not in caller.get("file", "")
        assert "..." not in caller.get("symbol", "")
    assert parsed["blast_radius_floor"]["top_callers"] == [
        {"file": "src/caller_c.py", "line": 30, "symbol": "caller_c"}
    ]
    assert parsed["blast_radius_floor"]["top_callers_omitted"] == 2
    assert parsed["validation_commands"] == ["uv run pytest -q"]
    omitted = parsed["omissions"]["compact_omitted"]
    for omitted_key in (
        "rollback.command",
        "rollback.checkpoint_id",
        "validation_commands",
        "primary_target.file",
        "primary_target.symbol",
        "next_action.action.path",
        "next_action.action.instruction",
        "blast_radius_floor.top_callers",
    ):
        assert omitted_key in omitted

    # Extreme fallback must omit next_action cleanly rather than synthesizing edit_file.
    extreme_payload = _sample_prepare_payload(bloat=True)
    extreme_payload["next_action"] = {
        "action": {"type": "run_command", "instruction": "z" * 6000, "path": "src/a.py"}
    }
    extreme_payload["rollback"] = {"supported": True, "command": "git status"}
    extreme_str = format_prepare_compact_json(extreme_payload, max_bytes=350)
    assert len(extreme_str.encode("utf-8")) <= 350
    extreme_parsed = json.loads(extreme_str)
    assert extreme_parsed["compact_truncated"] is True
    if "next_action" in extreme_parsed:
        assert extreme_parsed["next_action"]["action"]["type"] == "run_command"


def test_prepare_compact_json_omitted_section_count_and_final_fallback_omissions() -> None:
    payload = _sample_prepare_payload(bloat=True)
    payload["omissions"] = {"omitted_section_count": 2}
    payload["validation_commands"] = ["uv run pytest -q"] * 10
    payload["primary_target"]["symbol"] = "sym_" * 50
    compact_str = format_prepare_compact_json(payload)
    assert len(compact_str.encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    parsed = json.loads(compact_str)
    compact_omitted = parsed["omissions"]["compact_omitted"]
    assert parsed["omissions"]["omitted_section_count"] == 2 + len(compact_omitted)
    assert parsed["omissions"]["omitted_count"] == sum(compact_omitted.values())

    fallback_payload = _sample_prepare_payload(bloat=True)
    fallback_payload["ask_user_before_editing"] = {"required": True, "reasons": ["r" * 400]}
    fallback_payload["confidence"] = {"overall": 0.4, "level": "low", "band": "low"}
    fallback_payload["context_consistency"] = {
        "status": "stale",
        "mode": "cold",
        "stale": True,
        "reason": "x" * 400,
    }
    fallback_str = format_prepare_compact_json(fallback_payload, max_bytes=420)
    assert len(fallback_str.encode("utf-8")) <= 420
    fallback_parsed = json.loads(fallback_str)
    assert fallback_parsed["compact_truncated"] is True
    if "omissions" in fallback_parsed:
        fb_omitted = fallback_parsed["omissions"]["compact_omitted"]
        for dropped_key in ("ask_user_before_editing", "confidence", "context_consistency"):
            if dropped_key not in fallback_parsed:
                assert dropped_key in fb_omitted


def test_prepare_compact_json_enforces_cap_on_oversized_version_and_schema_version() -> None:
    payload = _sample_prepare_payload(bloat=True)
    payload["version"] = "v" * 5000
    payload["schema_version"] = {"nested": "s" * 5000}
    payload["possibly_incomplete"] = True

    compact_str = format_prepare_compact_json(payload)
    assert len(compact_str.encode("utf-8")) <= PREPARE_COMPACT_MAX_BYTES
    parsed = json.loads(compact_str)
    assert parsed["compact_truncated"] is True
    assert parsed["possibly_incomplete"] is True


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["prepare", "--help"],
        ["codemap", "--help"],
        ["evidence", "emit", "--help"],
        ["evidence", "keygen", "--help"],
        ["review-bundle", "create", "--help"],
        ["dogfood", "--help"],
    ],
)
def test_out_and_output_aliases_exposed(cmd_args: list[str]) -> None:
    res = runner.invoke(app, cmd_args)
    assert res.exit_code == 0, res.output
    assert "--out" in res.output
    assert "--output" in res.output


def test_scan_format_sarif_alias(tmp_path: Path) -> None:
    py_file = tmp_path / "sample.py"
    py_file.write_text("print('hello')\n", encoding="utf-8")
    rule_file = tmp_path / "rule.yml"
    rule_file.write_text(
        "id: no-print\nlanguage: python\nseverity: warning\nmessage: avoid print\npattern: print($X)\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        app,
        ["scan", str(tmp_path), "--rule", str(rule_file), "--format", "sarif"],
    )
    assert res.exit_code == 0, res.output
    sarif = json.loads(res.output)
    assert sarif["version"] == "2.1.0"
    assert "runs" in sarif


def test_test_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        m.setattr(
            ast_workflows,
            "test_command",
            lambda config, json_output=False: (
                print(
                    json.dumps({"version": 1, "passed": True, "total_cases": 2, "failed_cases": 0})
                )
                or 0
                if json_output
                else 0
            ),
        )
        res = runner.invoke(app, ["test", "--json"])
        assert res.exit_code == 0, res.output
        parsed = json.loads(res.output)
        assert parsed["passed"] is True
        assert parsed["total_cases"] == 2

    # Real error path with missing sgconfig.yml
    res_err = runner.invoke(app, ["test", "--config", str(tmp_path / "missing.yml"), "--json"])
    assert res_err.exit_code == 1
    parsed_err = json.loads(res_err.output)
    assert parsed_err["passed"] is False
    assert parsed_err["error"]["code"] == "invalid_input"


def test_mcp_classify_logs_text_and_exclusivity() -> None:
    neither = json.loads(mcp_server.tg_classify_logs())
    assert neither["error"]["code"] == "invalid_input"

    both = json.loads(mcp_server.tg_classify_logs(file_path="app.log", text="ERROR boom"))
    assert both["error"]["code"] == "invalid_input"

    ok = json.loads(mcp_server.tg_classify_logs(text="INFO ok\nERROR database connection failed\n"))
    assert ok["source"] == "text"
    assert ok["anomaly_count"] >= 1
    assert "error" not in ok


def test_checkpoint_create_json_invalid_label_emits_structured_error(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    res = runner.invoke(app, ["checkpoint", "create", str(tmp_path), "--label", "   ", "--json"])
    assert res.exit_code == 2
    parsed = json.loads(res.output)
    assert parsed["error"]["code"] == "invalid_input"
    assert parsed["error"]["message"]


def test_readme_clarifies_search_rank_vs_find() -> None:
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert "--rank" in readme
    assert "tg find" in readme


def test_checkpoint_undo_does_not_rewrite_positional_double_dash_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    seen: list[tuple[str, str]] = []

    def _fake_undo(checkpoint_id: str, path: str) -> Any:
        seen.append((checkpoint_id, path))
        return SimpleNamespace(
            checkpoint_id=checkpoint_id,
            mode="git",
            root=path,
            restored_files=0,
            removed_paths=0,
            diverged_paths=[],
            divergence_unchecked_paths=[],
        )

    monkeypatch.setattr("tensor_grep.cli.checkpoint_store.undo_checkpoint", _fake_undo)
    res = runner.invoke(app, ["checkpoint", "undo", "ckpt-1", "--", "--json"])
    assert res.exit_code == 0, res.output
    assert seen == [("ckpt-1", "--json")]


def test_checkpoint_create_permission_error_is_not_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _deny(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("access denied")

    monkeypatch.setattr("tensor_grep.cli.checkpoint_store.create_checkpoint", _deny)
    res = runner.invoke(app, ["checkpoint", "create", str(tmp_path), "--json"])
    assert res.exit_code == 1
    parsed = json.loads(res.output)
    assert parsed["error"]["code"] == "create_failed"


def test_mcp_classify_logs_caps_oversized_text_and_long_anomaly_lines() -> None:
    oversized = "ERROR boom\n" * 200_000
    rejected = json.loads(mcp_server.tg_classify_logs(text=oversized))
    assert rejected["error"]["code"] == "invalid_input"

    long_line = "ERROR " + ("x" * 1000) + "\n"
    ok = json.loads(mcp_server.tg_classify_logs(text=long_line))
    assert ok["anomaly_count"] == 1
    assert len(ok["anomalies"][0]["text"]) <= 512


def test_scan_invalid_format_mentions_sarif_and_json() -> None:
    res = runner.invoke(app, ["scan", "--format", "xml"])
    assert res.exit_code != 0
    assert "sarif" in res.output and "json" in res.output
