from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def test_find_why_ranked_and_install_state_in_json(tmp_path: Path) -> None:
    f1 = tmp_path / "refusal.py"
    f1.write_text("def _emit_broad_scan_refusal():\n    pass\n", encoding="utf-8")

    res = runner.invoke(
        app, ["find", "broad_scan_refusal", str(tmp_path), "--json", "--why-ranked"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert "install_state" in payload
    assert "dense_ready" in payload["install_state"] or "bm25_only" in payload["install_state"]
    assert len(payload["matches"]) > 0
    first_match = payload["matches"][0]
    assert "why_ranked" in first_match
    assert isinstance(first_match["why_ranked"], list)
