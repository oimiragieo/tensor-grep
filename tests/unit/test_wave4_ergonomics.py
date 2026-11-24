"""Wave 4 Track E (P3 Ergonomics & Consistency) regression tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.doctor_payload import _build_doctor_payload, _render_doctor_payload
from tensor_grep.cli.formatters.json_fmt import JsonFormatter, NdjsonFormatter
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.cli.main import app
from tensor_grep.cli.orient_capsule import build_orient_capsule_from_map
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


def test_doctor_payload_resolves_source_version_from_checkout_environment(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.139.1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.139.1")
    monkeypatch.setattr(
        "tensor_grep.cli.runtime_paths._read_project_version_fallback", lambda: "0.0.0"
    )
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda path=None: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda path=None: None
    )

    payload = _build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    assert payload["checkout_environment"]["source_version"] == "1.139.1"
    assert payload["source_version"] == "1.139.1"


def test_doctor_render_managed_launcher_broken_uses_python_module_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.139.1")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda path=None: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda path=None: None
    )
    payload = _build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    payload["installation_health"] = "managed_launcher_broken"
    rendered = _render_doctor_payload(payload)
    assert "python -m tensor_grep repair-launcher" in rendered
    assert "run `tg repair-launcher`" not in rendered


def test_orient_capsule_ranks_public_symbols_before_private_helpers(tmp_path: Path) -> None:
    hub = str(tmp_path / "hub.py")
    leaf = str(tmp_path / "leaf.py")
    (tmp_path / "hub.py").write_text(
        "def _private_top():\n    pass\n\ndef public_low():\n    pass\n\ndef public_hot():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "leaf.py").write_text("import hub\n", encoding="utf-8")

    rm = {
        "path": str(tmp_path),
        "files": [hub, leaf],
        "imports": [{"file": leaf, "imports": ["hub"]}],
        "symbols": [
            {"file": hub, "name": "_private_top", "kind": "function", "line": 1, "in_degree": 10},
            {"file": hub, "name": "public_low", "kind": "function", "line": 4, "in_degree": 1},
            {"file": hub, "name": "public_hot", "kind": "function", "line": 7, "in_degree": 5},
            {"file": leaf, "name": "leaf_fn", "kind": "function", "line": 1},
        ],
    }

    capsule = build_orient_capsule_from_map(rm, max_central_files=2, max_tokens=500)
    hub_central = next(cf for cf in capsule["central_files"] if cf["file"] == hub)
    assert [s["name"] for s in hub_central["symbols"]] == [
        "public_hot",
        "public_low",
        "_private_top",
    ]
    assert [s["name"] for s in capsule["symbol_map"][hub]] == [
        "public_hot",
        "public_low",
        "_private_top",
    ]


def test_formatters_normalize_mixed_windows_paths_without_breaking_separator_override(
    monkeypatch,
) -> None:
    import ntpath

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "path", ntpath)

    mixed_path = "C:\\repo/src\\module.py"
    expected_nt = "C:\\repo\\src\\module.py"
    result = SearchResult(
        matches=[MatchLine(line_number=3, text="hit", file=mixed_path)],
        matched_file_paths=[mixed_path],
        match_counts_by_file={mixed_path: 1},
        total_files=1,
        total_matches=1,
    )

    rg_out = RipgrepFormatter(SearchConfig(with_filename=True)).format(result)
    assert rg_out == f"{expected_nt}:3:hit"

    rg_override = RipgrepFormatter(SearchConfig(with_filename=True, path_separator="/")).format(
        result
    )
    assert rg_override == "C:/repo/src/module.py:3:hit"

    json_out = json.loads(JsonFormatter().format(result))
    assert json_out["matches"][0]["file"] == expected_nt
    assert json_out["matched_file_paths"] == [expected_nt]
    assert json_out["match_counts_by_file"] == {expected_nt: 1}

    ndjson_row = json.loads(NdjsonFormatter().format(result))
    assert ndjson_row["file"] == expected_nt


def test_ledger_claim_accepts_singular_file_alias(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["ledger", "claim", str(tmp_path), "--file", "a.py,b.py", "--json"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["claim"]["files"] == ["a.py", "b.py"]
