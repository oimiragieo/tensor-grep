"""Unit tests for constant-aware diff-impact and directory-ref guard (DF-10)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.diff_impact import build_diff_blast_radius
from tensor_grep.cli.diff_impact_constants import (
    check_directory_ref,
    extract_constant_symbols,
    resolve_constant_blast_radius,
)
from tensor_grep.cli.main import app

runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")


def test_constant_change_tracks_callers_transitive_and_tests(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    config_py = tmp_path / "src" / "config.py"
    billing_py = tmp_path / "src" / "billing.py"
    api_py = tmp_path / "src" / "api.py"
    test_py = tmp_path / "tests" / "test_billing.py"

    config_py.write_text("DEFAULT_TAX_RATE = 0.08\n", encoding="utf-8")
    billing_py.write_text(
        "from src.config import DEFAULT_TAX_RATE\n\n"
        "def calculate_total(amount: float) -> float:\n"
        "    return amount * (1.0 + DEFAULT_TAX_RATE)\n",
        encoding="utf-8",
    )
    api_py.write_text(
        "from src.billing import calculate_total\n\n"
        "def checkout(amount: float) -> float:\n"
        "    return calculate_total(amount)\n",
        encoding="utf-8",
    )
    test_py.write_text(
        "from src.api import checkout\n"
        "from src.config import DEFAULT_TAX_RATE\n\n"
        "def test_checkout_uses_tax_rate() -> None:\n"
        "    assert checkout(100.0) == 100.0 * (1.0 + DEFAULT_TAX_RATE)\n",
        encoding="utf-8",
    )

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    # Modify only the module-level constant
    config_py.write_text("DEFAULT_TAX_RATE = 0.095\n", encoding="utf-8")

    payload = build_diff_blast_radius(root=tmp_path)
    sym_names = [s["name"] for s in payload["changed_symbols"]]
    assert "DEFAULT_TAX_RATE" in sym_names
    assert any(
        s["name"] == "DEFAULT_TAX_RATE" and s.get("kind") == "constant"
        for s in payload["changed_symbols"]
    )

    caller_names = {c.get("caller", c.get("name")) for c in payload["callers"]}
    caller_files = {c["file"] for c in payload["callers"]}
    assert "calculate_total" in caller_names
    assert "test_checkout_uses_tax_rate" in caller_names
    assert "src/billing.py" in caller_files
    assert "src/api.py" in caller_files

    assert "src/config.py" in payload["affected_files"]
    assert "src/billing.py" in payload["affected_files"]
    assert "src/api.py" in payload["affected_files"]
    assert "tests/test_billing.py" in payload["affected_tests"]
    assert payload["blast_radius_score"] >= 0.15
    assert payload["risk_tier"] in {"medium", "high", "critical"}


def test_extract_constant_symbols_multilang() -> None:
    ts_consts = extract_constant_symbols(
        b"export const DEFAULT_TAX_RATE = 0.08;\nconst MAX_RETRIES = 3;\n",
        ".ts",
    )
    assert [c["name"] for c in ts_consts] == ["DEFAULT_TAX_RATE", "MAX_RETRIES"]

    rs_consts = extract_constant_symbols(
        b"pub const DEFAULT_TAX_RATE: f64 = 0.08;\nstatic mut COUNTER: usize = 0;\n",
        ".rs",
    )
    assert [c["name"] for c in rs_consts] == ["DEFAULT_TAX_RATE", "COUNTER"]

    go_consts = extract_constant_symbols(
        b"package cfg\nconst DefaultTaxRate = 0.08\nvar (\n\tMaxRetries = 3\n)\n",
        ".go",
    )
    assert [c["name"] for c in go_consts] == ["DefaultTaxRate", "MaxRetries"]


def test_directory_ref_guard_emits_actionable_hint(tmp_path: Path, monkeypatch: Any) -> None:
    _init_git_repo(tmp_path)
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    payload = build_diff_blast_radius(ref="src", root=tmp_path)
    assert payload["partial"] is True
    assert payload["incomplete_reason"] == "invalid_ref"
    err = str(payload.get("error", ""))
    assert "is a directory path, not a git revision" in err
    assert "--path" in err
    assert "HEAD~1" in err

    monkeypatch.chdir(tmp_path)
    res_json = runner.invoke(app, ["diff-impact", "src", "--json"])
    assert res_json.exit_code == 2
    parsed = json.loads(res_json.stdout)
    assert "is a directory path, not a git revision" in str(parsed.get("error", ""))

    res_text = runner.invoke(app, ["diff-impact", "src"])
    assert res_text.exit_code == 2
    combined_out = res_text.stdout + res_text.stderr
    assert "is a directory path, not a git revision" in combined_out


def test_directory_ref_guard_allows_valid_branch_sharing_dir_name(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "readme.md").write_text("hi\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "branch", "docs")

    assert check_directory_ref("docs", tmp_path) is None


def test_aliased_constant_imports_python_and_js_ts_with_provenance(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()

    config_py = tmp_path / "src" / "config.py"
    aliased_py = tmp_path / "src" / "aliased.py"
    bare_py = tmp_path / "src" / "bare.py"
    config_ts = tmp_path / "src" / "config.ts"
    consumer_ts = tmp_path / "src" / "consumer.ts"

    config_py.write_text("LIMIT = 100\n", encoding="utf-8")
    aliased_py.write_text(
        "from src.config import LIMIT as MAX_ROWS\n\n"
        "def fetch_page() -> int:\n"
        "    return MAX_ROWS\n",
        encoding="utf-8",
    )
    bare_py.write_text(
        "def fallback_page() -> int:\n    return LIMIT\n",
        encoding="utf-8",
    )
    config_ts.write_text("export const TS_LIMIT = 50;\n", encoding="utf-8")
    consumer_ts.write_text(
        "import { TS_LIMIT as MAX_TS_ROWS } from './config';\n"
        "export function pageSize(): number {\n"
        "  return MAX_TS_ROWS;\n"
        "}\n",
        encoding="utf-8",
    )

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    config_py.write_text("LIMIT = 200\n", encoding="utf-8")
    config_ts.write_text("export const TS_LIMIT = 75;\n", encoding="utf-8")

    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["constant_resolution_incomplete"] is False
    assert payload["resolution_gaps_count"] == 0

    callers = payload["callers"]
    aliased_callers = [
        c for c in callers if c["file"] == "src/aliased.py" and c.get("caller") == "fetch_page"
    ]
    assert aliased_callers
    assert aliased_callers[0]["provenance"] == "binding_match"

    bare_callers = [
        c for c in callers if c["file"] == "src/bare.py" and c.get("caller") == "fallback_page"
    ]
    assert bare_callers
    assert bare_callers[0]["provenance"] == "heuristic_name_match"

    ts_callers = [c for c in callers if c["file"] == "src/consumer.ts" and c.get("line") == 3]
    assert ts_callers
    assert ts_callers[0]["provenance"] == "binding_match"


def test_constant_resolution_propagates_uncertainty_on_unreadable_or_unparseable_file(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    config_py = tmp_path / "src" / "config.py"
    broken_py = tmp_path / "src" / "broken.py"

    config_py.write_text("LIMIT = 100\n", encoding="utf-8")
    broken_py.write_text("from src.config import LIMIT\ndef bad(:\n", encoding="utf-8")

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    config_py.write_text("LIMIT = 200\n", encoding="utf-8")
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["constant_resolution_incomplete"] is True
    assert payload["resolution_gaps_count"] >= 1
    assert payload["partial"] is True
    assert "constant_resolution_incomplete" in payload["downgrade_reasons"]


def test_multiline_ts_and_rust_constant_declarations(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    ts_file = tmp_path / "src" / "limits.ts"
    rs_file = tmp_path / "src" / "limits.rs"

    ts_file.write_text(
        "export const TS_LIMIT =\n  200;\nexport const TS_OBJ = {\n  retries: 3,\n};\n",
        encoding="utf-8",
    )
    rs_file.write_text(
        "pub const RS_LIMIT: usize =\n"
        "    200;\n"
        "pub const RS_TUPLE: (usize, usize) = (\n"
        "    1,\n"
        "    2,\n"
        ");\n",
        encoding="utf-8",
    )

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    ts_consts = extract_constant_symbols(ts_file.read_bytes(), ".ts")
    assert [(c["name"], c["start_line"], c["end_line"]) for c in ts_consts] == [
        ("TS_LIMIT", 1, 2),
        ("TS_OBJ", 3, 5),
    ]
    rs_consts = extract_constant_symbols(rs_file.read_bytes(), ".rs")
    assert [(c["name"], c["start_line"], c["end_line"]) for c in rs_consts] == [
        ("RS_LIMIT", 1, 2),
        ("RS_TUPLE", 3, 6),
    ]

    # Edit only the continuation lines (line 2 in TS and line 2 in Rust)
    ts_file.write_text(
        "export const TS_LIMIT =\n  300;\nexport const TS_OBJ = {\n  retries: 3,\n};\n",
        encoding="utf-8",
    )
    rs_file.write_text(
        "pub const RS_LIMIT: usize =\n"
        "    300;\n"
        "pub const RS_TUPLE: (usize, usize) = (\n"
        "    1,\n"
        "    2,\n"
        ");\n",
        encoding="utf-8",
    )

    payload = build_diff_blast_radius(root=tmp_path)
    changed_names = {s["name"] for s in payload["changed_symbols"]}
    assert "TS_LIMIT" in changed_names
    assert "RS_LIMIT" in changed_names


def test_check_directory_ref_refuses_outside_root_and_unc_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _init_git_repo(tmp_path)
    outside_dir = tmp_path / "outside"
    repo_dir = tmp_path / "repo"
    outside_dir.mkdir()
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    (repo_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "initial")

    probed: list[Path] = []
    orig_is_dir = Path.is_dir

    def spy_is_dir(self: Path) -> bool:
        probed.append(self)
        return orig_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", spy_is_dir)

    for bad_ref in (
        "../outside",
        str(outside_dir),
        r"\\server\share\dir",
        "//server/share/dir",
        "C:\\Windows",
        "/etc",
    ):
        assert check_directory_ref(bad_ref, repo_dir) is None

    assert probed == []


def test_check_directory_ref_refuses_in_root_symlink_or_junction_outside_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    outside_dir = tmp_path / "outside"
    repo_dir = tmp_path / "repo"
    outside_dir.mkdir()
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    (repo_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "initial")

    escape_link = repo_dir / "escape_link"
    created_junction = False
    try:
        escape_link.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(escape_link), str(outside_dir)],
                check=True,
                capture_output=True,
            )
            created_junction = True
        else:
            raise

    probed: list[Path] = []
    orig_is_dir = Path.is_dir

    def spy_is_dir(self: Path) -> bool:
        probed.append(self)
        return orig_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", spy_is_dir)
    try:
        assert check_directory_ref("escape_link", repo_dir) is None
        assert probed == []
    finally:
        if created_junction:
            subprocess.run(["cmd", "/c", "rmdir", str(escape_link)], check=False)
        elif escape_link.is_symlink():
            escape_link.unlink()


def test_cross_module_unrelated_import_gets_heuristic_name_match(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pricing").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "js").mkdir()

    tax_py = tmp_path / "pricing" / "tax.py"
    other_tax_py = tmp_path / "other" / "tax.py"
    match_py = tmp_path / "consumer_match.py"
    unrelated_py = tmp_path / "consumer_other.py"

    tax_py.write_text(
        "DEFAULT_TAX_RATE = 0.08\n\ndef local_rate() -> float:\n    return DEFAULT_TAX_RATE\n",
        encoding="utf-8",
    )
    other_tax_py.write_text("DEFAULT_TAX_RATE = 0.05\n", encoding="utf-8")
    match_py.write_text(
        "from pricing.tax import DEFAULT_TAX_RATE\n\n"
        "def apply_pricing() -> float:\n"
        "    return DEFAULT_TAX_RATE\n",
        encoding="utf-8",
    )
    unrelated_py.write_text(
        "from other.tax import DEFAULT_TAX_RATE\n\n"
        "def apply_other() -> float:\n"
        "    return DEFAULT_TAX_RATE\n",
        encoding="utf-8",
    )

    cfg_ts = tmp_path / "js" / "config.ts"
    other_ts = tmp_path / "js" / "other.ts"
    unrelated_ts = tmp_path / "js" / "consumer_other.ts"
    cfg_ts.write_text("export const TS_LIMIT = 10;\n", encoding="utf-8")
    other_ts.write_text("export const TS_LIMIT = 20;\n", encoding="utf-8")
    unrelated_ts.write_text(
        "import { TS_LIMIT } from './other';\nexport function f(): number { return TS_LIMIT; }\n",
        encoding="utf-8",
    )

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    tax_py.write_text(
        "DEFAULT_TAX_RATE = 0.09\n\ndef local_rate() -> float:\n    return DEFAULT_TAX_RATE\n",
        encoding="utf-8",
    )
    cfg_ts.write_text("export const TS_LIMIT = 15;\n", encoding="utf-8")

    payload = build_diff_blast_radius(root=tmp_path)
    callers = payload["callers"]

    local_c = [
        c for c in callers if c["file"] == "pricing/tax.py" and c.get("caller") == "local_rate"
    ]
    assert local_c and local_c[0]["provenance"] == "binding_match"

    match_c = [
        c
        for c in callers
        if c["file"] == "consumer_match.py" and c.get("caller") == "apply_pricing"
    ]
    assert match_c and match_c[0]["provenance"] == "binding_match"

    unrelated_c = [
        c for c in callers if c["file"] == "consumer_other.py" and c.get("caller") == "apply_other"
    ]
    assert unrelated_c and unrelated_c[0]["provenance"] == "heuristic_name_match"

    ts_unrelated_c = [c for c in callers if c["file"] == "js/consumer_other.ts"]
    assert ts_unrelated_c and ts_unrelated_c[0]["provenance"] == "heuristic_name_match"


def test_constant_resolution_handles_prefilter_regex_fallback_and_transitive_gaps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = tmp_path / "config.py"
    consumer = tmp_path / "consumer.py"
    cfg.write_text("LIMIT = 10\n", encoding="utf-8")
    consumer.write_text(
        "from config import LIMIT\n\ndef use_limit() -> int:\n    return LIMIT\n",
        encoding="utf-8",
    )
    base_map: dict[str, Any] = {
        "files": ["config.py", "consumer.py"],
        "tests": [],
        "symbols": [
            {
                "file": "consumer.py",
                "name": "use_limit",
                "kind": "function",
                "line": 3,
                "start_line": 3,
                "end_line": 4,
            }
        ],
    }
    sym = {"name": "LIMIT", "file": "config.py", "line": 1, "kind": "constant"}

    # 1) Prefilter OSError
    monkeypatch.setattr(
        repo_map,
        "_file_may_contain_literal_symbol",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("unreadable")),
    )
    res_prefilter = resolve_constant_blast_radius(base_map, sym, root=tmp_path)
    assert res_prefilter["constant_resolution_incomplete"] is True
    assert res_prefilter["resolution_gaps_count"] >= 1
    assert res_prefilter["partial"] is True

    # 2) JS/TS regex fallback OSError
    monkeypatch.undo()
    ts_cfg = tmp_path / "config.ts"
    ts_consumer = tmp_path / "consumer.ts"
    ts_cfg.write_text("export const LIMIT = 10;\n", encoding="utf-8")
    ts_consumer.write_text(
        "import { LIMIT } from './config';\nconst x = LIMIT;\n", encoding="utf-8"
    )
    ts_map: dict[str, Any] = {"files": ["config.ts", "consumer.ts"], "tests": [], "symbols": []}
    ts_sym = {"name": "LIMIT", "file": "config.ts", "line": 1, "kind": "constant"}
    monkeypatch.setattr(repo_map, "_references_and_calls_for_path", lambda *_a, **_kw: ([], []))
    monkeypatch.setattr(repo_map, "_parsed_source_and_tree", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        repo_map,
        "_regex_references_and_calls",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("regex fallback failed")),
    )
    res_regex = resolve_constant_blast_radius(ts_map, ts_sym, root=tmp_path)
    assert res_regex["constant_resolution_incomplete"] is True
    assert res_regex["resolution_gaps_count"] >= 1
    assert res_regex["partial"] is True

    # 3) Transitive analysis incomplete flags
    monkeypatch.undo()
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_from_map",
        lambda *_a, **_kw: {
            "callers": [],
            "affected_files": [],
            "tests": [],
            "blast_radius_score": 0.0,
            "result_incomplete": True,
        },
    )
    res_trans = resolve_constant_blast_radius(base_map, sym, root=tmp_path)
    assert res_trans["constant_resolution_incomplete"] is True
    assert res_trans["resolution_gaps_count"] >= 1
    assert res_trans["partial"] is True


def test_extract_constant_symbols_newline_after_keyword() -> None:
    ts_consts = extract_constant_symbols(b"export const\n  LIMIT = 100;\n", ".ts")
    assert [(c["name"], c["start_line"], c["end_line"]) for c in ts_consts] == [("LIMIT", 1, 2)]

    rs_consts = extract_constant_symbols(
        b"pub const\n  RS_LIMIT: usize = 100;\nstatic mut\n  COUNTER: usize = 0;\n",
        ".rs",
    )
    assert [(c["name"], c["start_line"], c["end_line"]) for c in rs_consts] == [
        ("RS_LIMIT", 1, 2),
        ("COUNTER", 3, 4),
    ]

    go_consts = extract_constant_symbols(b"const\n  GoLimit = 100\nvar\n  GoVar = 200\n", ".go")
    assert [(c["name"], c["start_line"], c["end_line"]) for c in go_consts] == [
        ("GoLimit", 1, 2),
        ("GoVar", 3, 4),
    ]


def test_constant_resolution_marks_no_match_and_deadline_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cfg = tmp_path / "config.py"
    consumer = tmp_path / "consumer.py"
    cfg.write_text("LIMIT = 10\n", encoding="utf-8")
    consumer.write_text(
        "from config import LIMIT\n\ndef use_limit() -> int:\n    return LIMIT\n",
        encoding="utf-8",
    )
    base_map: dict[str, Any] = {
        "files": ["config.py", "consumer.py"],
        "tests": [],
        "symbols": [
            {
                "file": "consumer.py",
                "name": "use_limit",
                "kind": "function",
                "line": 3,
                "start_line": 3,
                "end_line": 4,
            }
        ],
    }
    sym = {"name": "LIMIT", "file": "config.py", "line": 1, "kind": "constant"}

    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_from_map",
        lambda *_a, **_kw: {
            "callers": [],
            "affected_files": [],
            "tests": [],
            "blast_radius_score": 0.0,
            "no_match": True,
        },
    )
    res_no_match = resolve_constant_blast_radius(base_map, sym, root=tmp_path)
    assert res_no_match["partial"] is True
    assert res_no_match["constant_resolution_incomplete"] is True
    assert res_no_match["resolution_gaps_count"] >= 1

    monkeypatch.undo()
    res_deadline = resolve_constant_blast_radius(
        base_map, sym, root=tmp_path, deadline_monotonic=0.0
    )
    assert res_deadline["partial"] is True
    assert res_deadline["constant_resolution_incomplete"] is True
