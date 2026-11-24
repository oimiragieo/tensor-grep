from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.formatters.json_fmt import JsonFormatter
from tensor_grep.core.config import SearchConfig


def test_ltl_json_accounting_parity_multiple_pairs(tmp_path: Path) -> None:
    log = tmp_path / "ltl_multi.log"
    log.write_text(
        "\n".join([
            "AUTH_FAIL user=1",
            "DB_TIMEOUT db=1",
            "AUTH_FAIL user=2",
            "DB_TIMEOUT db=2",
            "AUTH_FAIL user=3",
            "DB_TIMEOUT db=3",
            "AUTH_FAIL user=4",
            "DB_TIMEOUT db=4",
        ])
        + "\n",
        encoding="utf-8",
    )

    backend = CPUBackend()
    result = backend.search(str(log), r"AUTH_FAIL -> eventually DB_TIMEOUT", SearchConfig(ltl=True))

    assert len(result.matches) == 8
    assert result.total_matches == len(result.matches)
    assert sum(result.match_counts_by_file.values()) == len(result.matches)
    assert result.match_counts_by_file == {str(log): 8}

    payload = json.loads(JsonFormatter().format(result))
    assert payload["total_matches"] == 8
    assert len(payload["matches"]) == 8
    assert sum(payload["match_counts_by_file"].values()) == 8
