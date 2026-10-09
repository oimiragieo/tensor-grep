from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.dogfood_regressions import run_regressions
from tensor_grep.cli.main import app


@pytest.mark.parametrize("suppress_warm_hits", [False, True])
def test_dogfood_cache_probes_require_real_raw_map_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suppress_warm_hits: bool
) -> None:
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "0")
    calls: list[list[str]] = []
    records: dict[str, bool] = {}
    receipts: list[dict[str, Any]] = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str, str]:
        calls.append(args)
        if args[0] != "map":
            # Unrelated regression cases remain outside this focused receipt test.
            return 1, "{}", ""
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        receipts.append(dict(payload["symbol_cache"]))
        if suppress_warm_hits and len(receipts) == 2:
            payload["symbol_cache"]["hits"] = 0
        return result.exit_code, json.dumps(payload), ""

    def record(ok: bool, name: str, *args: Any) -> None:
        records[name] = ok

    run_regressions(tmp_path, run=run, record=record)
    assert calls[0][0] == calls[1][0] == "map"
    assert receipts[0]["misses"] > 0
    assert receipts[1]["hits"] > 0
    assert records["AST cache cold declaration"]
    assert records["AST cache warm declaration"] is (not suppress_warm_hits)
