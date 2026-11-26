from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import mcp_server
from tensor_grep.cli.main import app

pytestmark = pytest.mark.skipif(
    shutil.which("ast-grep") is None and shutil.which("sg") is None,
    reason="ast-grep binary required for real AST CLI/MCP parity tests",
)


def test_mcp_tg_scan_matches_cli_tg_scan_on_mixed_language_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "import subprocess\nsubprocess.run(cmd, shell=True, check=True)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.rs").write_text(
        'fn main() { let _ = std::process::Command::new("sh"); }\n',
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text(
        "child_process.exec(cmd, { cwd: '.' }, cb);\n",
        encoding="utf-8",
    )

    cli_result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "subprocess-safe",
            "--path",
            str(tmp_path),
            "--json",
            "--allow-broad-generated-scan",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.stdout
    cli_payload = json.loads(cli_result.stdout)

    monkeypatch.chdir(tmp_path)
    mcp_raw = mcp_server.tg_scan(
        action="scan",
        ruleset="subprocess-safe",
        path=".",
        allow_broad_generated_scan=True,
    )
    mcp_payload = json.loads(mcp_raw)

    assert "error" not in mcp_payload, mcp_payload
    assert mcp_payload["language"] == cli_payload["language"] == "mixed"
    assert (
        mcp_payload["languages"]
        == cli_payload["languages"]
        == [
            "javascript",
            "python",
            "rust",
        ]
    )
    assert mcp_payload["total_matches"] == cli_payload["total_matches"] == 3
    assert mcp_payload["matched_rules"] == cli_payload["matched_rules"] == 3

    cli_summary = sorted(
        (f["rule_id"], f["language"], f["matches"]) for f in cli_payload["findings"]
    )
    mcp_summary = sorted(
        (f["rule_id"], f["language"], f["matches"]) for f in mcp_payload["findings"]
    )
    assert mcp_summary == cli_summary
