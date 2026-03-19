from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
TG_BINARY = REPO_ROOT / "rust_core" / "target" / "release" / ("tg.exe" if os.name == "nt" else "tg")
REWRITE_PATTERN = "def $F($$$ARGS): return $EXPR"
REWRITE_REPLACEMENT = "lambda $$$ARGS: $EXPR"


@pytest.fixture(scope="session")
def native_tg_binary() -> Path:
    candidate = Path(os.environ.get("TG_NATIVE_TG_BINARY") or TG_BINARY)
    if not candidate.exists():
        pytest.skip(f"native tg binary not found: {candidate}")
    return candidate


@pytest.fixture()
def command_env(native_tg_binary: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    env["TG_NATIVE_TG_BINARY"] = str(native_tg_binary)
    env["TG_MCP_TG_BINARY"] = str(native_tg_binary)
    return env


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _assert_success(result: subprocess.CompletedProcess[str], *, context: str) -> None:
    assert result.returncode == 0, (
        f"{context} failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_public_cli_harness_flow_search_rewrite_and_verify(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    log_file = project / "app.log"
    source_file = project / "rewrite_fixture.py"
    log_file.write_text("INFO boot\nERROR timeout while connecting\n", encoding="utf-8")
    source_file.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    search_result = _run(
        [str(native_tg_binary), "search", "--json", "ERROR", str(project)],
        env=command_env,
    )
    _assert_success(search_result, context="search --json")
    search_payload = json.loads(search_result.stdout)
    assert search_payload["version"] == 1
    assert search_payload["routing_backend"]
    assert search_payload["routing_reason"]
    assert search_payload["sidecar_used"] is False
    assert search_payload["total_matches"] >= 1
    assert any(match["file"].endswith("app.log") for match in search_payload["matches"])

    ndjson_result = _run(
        [str(native_tg_binary), "search", "--ndjson", "ERROR", str(project)],
        env=command_env,
    )
    _assert_success(ndjson_result, context="search --ndjson")
    ndjson_rows = [json.loads(line) for line in ndjson_result.stdout.splitlines() if line.strip()]
    assert ndjson_rows
    assert any(row["file"].endswith("app.log") for row in ndjson_rows)
    assert all(row["version"] == 1 for row in ndjson_rows)
    assert all("routing_backend" in row for row in ndjson_rows)

    plan_result = _run(
        [
            str(native_tg_binary),
            "run",
            "--lang",
            "python",
            "--rewrite",
            REWRITE_REPLACEMENT,
            "--json",
            REWRITE_PATTERN,
            str(source_file),
        ],
        env=command_env,
    )
    _assert_success(plan_result, context="rewrite plan")
    plan_payload = json.loads(plan_result.stdout)
    assert plan_payload["version"] == 1
    assert plan_payload["routing_backend"] == "AstBackend"
    assert plan_payload["routing_reason"] == "ast-native"
    assert plan_payload["sidecar_used"] is False
    assert plan_payload["total_edits"] == 1
    assert plan_payload["edits"][0]["replacement_text"] == "lambda x, y: x + y"

    diff_result = _run(
        [
            str(native_tg_binary),
            "run",
            "--lang",
            "python",
            "--rewrite",
            REWRITE_REPLACEMENT,
            "--diff",
            REWRITE_PATTERN,
            str(source_file),
        ],
        env=command_env,
    )
    _assert_success(diff_result, context="rewrite diff")
    assert "--- " in diff_result.stdout
    assert "+++ " in diff_result.stdout
    assert "@@" in diff_result.stdout

    apply_result = _run(
        [
            str(native_tg_binary),
            "run",
            "--lang",
            "python",
            "--rewrite",
            REWRITE_REPLACEMENT,
            "--apply",
            "--verify",
            "--json",
            REWRITE_PATTERN,
            str(source_file),
        ],
        env=command_env,
    )
    _assert_success(apply_result, context="rewrite apply verify")
    apply_payload = json.loads(apply_result.stdout)
    assert apply_payload["version"] == 1
    assert apply_payload["routing_backend"] == "AstBackend"
    assert apply_payload["plan"]["total_edits"] == 1
    assert apply_payload["verification"]["verified"] == 1
    assert apply_payload["verification"]["mismatches"] == []
    assert source_file.read_text(encoding="utf-8") == "lambda x, y: x + y\n"


def test_public_mcp_tools_roundtrip_against_native_binary(
    tmp_path: Path,
    command_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    log_file = project / "app.log"
    source_file = project / "rewrite_fixture.py"
    log_file.write_text("INFO boot\nERROR timeout while connecting\n", encoding="utf-8")
    source_file.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    monkeypatch.setenv("TG_MCP_TG_BINARY", command_env["TG_MCP_TG_BINARY"])
    mcp_server._resolve_native_tg_binary.cache_clear()

    index_payload = json.loads(mcp_server.tg_index_search("ERROR", str(project)))
    assert index_payload["version"] == 1
    assert index_payload["routing_backend"] == "TrigramIndex"
    assert index_payload["routing_reason"] == "index-accelerated"
    assert index_payload["sidecar_used"] is False
    assert index_payload["total_matches"] >= 1

    rewrite_plan_payload = json.loads(
        mcp_server.tg_rewrite_plan(
            pattern=REWRITE_PATTERN,
            replacement=REWRITE_REPLACEMENT,
            lang="python",
            path=str(source_file),
        )
    )
    assert rewrite_plan_payload["version"] == 1
    assert rewrite_plan_payload["routing_backend"] == "AstBackend"
    assert rewrite_plan_payload["routing_reason"] == "ast-native"
    assert rewrite_plan_payload["total_edits"] == 1

    rewrite_diff_payload = json.loads(
        mcp_server.tg_rewrite_diff(
            pattern=REWRITE_PATTERN,
            replacement=REWRITE_REPLACEMENT,
            lang="python",
            path=str(source_file),
        )
    )
    assert rewrite_diff_payload["version"] == 1
    assert rewrite_diff_payload["routing_backend"] == "AstBackend"
    assert isinstance(rewrite_diff_payload["diff"], str)
    assert "--- " in rewrite_diff_payload["diff"]
    assert "+++ " in rewrite_diff_payload["diff"]

    rewrite_apply_payload = json.loads(
        mcp_server.tg_rewrite_apply(
            pattern=REWRITE_PATTERN,
            replacement=REWRITE_REPLACEMENT,
            lang="python",
            path=str(source_file),
            verify=True,
        )
    )
    assert rewrite_apply_payload["version"] == 1
    assert rewrite_apply_payload["routing_backend"] == "AstBackend"
    assert rewrite_apply_payload["verification"]["verified"] == 1
