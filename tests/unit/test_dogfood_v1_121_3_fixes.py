"""Comprehensive tests for fixes and modernizations from the v1.121.3 dogfood audit.

Covers:
- Task 1: Stream isolation and warning preservation for tg route-test
- Task 2: Editable provenance and package version desync detection in tg doctor / tg repair-env
- Task 6: Symbol disambiguation suggestions for defs and source
- Task 7: Multi-file checkpoint granularity (--paths) with containment & scoped undo safety
- Task 8: Structured AST querying (tg sql) read-only sandbox, limits, deadline & truncation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import runtime_paths
from tensor_grep.cli.checkpoint_store import (
    CheckpointCorruptError,
    create_checkpoint,
    undo_checkpoint,
)
from tensor_grep.cli.doctor_payload import _build_doctor_payload
from tensor_grep.cli.doctor_report import _doctor_rust_binary_remediation
from tensor_grep.cli.main import app

runner = CliRunner()


# ==============================================================================
# Task 1: Stream Isolation & Deprecation Warning for tg route-test
# ==============================================================================


def test_route_test_positional_no_deprecation_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """Positional tg route-test <PATH> <QUERY> produces zero warnings."""
    result = runner.invoke(app, ["route-test", "src/tensor_grep/cli", "search_command", "--json"])
    assert result.exit_code in (0, 2)
    payload = json.loads(result.stdout)
    warnings = payload.get("warnings", [])
    assert not any("--query is deprecated" in w for w in warnings)


def test_route_test_option_query_stream_isolation_json(capsys: pytest.CaptureFixture[str]) -> None:
    """In --json mode, using deprecated --query emits zero bytes to stderr and isolates warning to payload."""
    result = runner.invoke(
        app,
        ["route-test", "src/tensor_grep/cli", "--query", "search_command", "--json"],
    )
    assert result.exit_code in (0, 2)
    # The output is clean valid JSON
    payload = json.loads(result.stdout)
    warnings = payload.get("warnings", [])
    assert any("--query is deprecated for tg route-test" in w for w in warnings)


def test_route_test_option_query_stderr_text_mode() -> None:
    """In text mode, using deprecated --query writes warning to stderr."""
    result = runner.invoke(
        app,
        ["route-test", "src/tensor_grep/cli", "--query", "search_command"],
    )
    assert "--query is deprecated for tg route-test" in result.output


# ==============================================================================
# Task 2: Version Desync Detection & tg repair-env
# ==============================================================================


def test_doctor_detects_stale_editable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When pyproject.toml version > installed version in editable mode, report stale_editable."""
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_installed_version",
        lambda: "1.121.3",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.runtime_paths._read_project_version_fallback",
        lambda: "1.122.0",
    )

    class DummyDist:
        def read_text(self, filename: str) -> str | None:
            if filename == "direct_url.json":
                return json.dumps({
                    "url": runtime_paths._repo_root().resolve().as_uri(),
                    "dir_info": {"editable": True},
                })
            return None

    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda name: DummyDist(),
    )

    payload = _build_doctor_payload(str(tmp_path), with_lsp=False)
    assert payload["python_package_version_status"] == "stale_editable"
    assert payload["source_version"] == "1.122.0"

    remediation = _doctor_rust_binary_remediation(
        rust_binary_version_status="mismatch",
        native_tg_binary_kind="standalone-executable",
        python_package_version_status="stale_editable",
        rust_binary_version="1.122.0",
        source_version="1.122.0",
    )
    assert remediation is not None
    assert "Run 'tg repair-env' or 'uv pip install -e . --no-deps'" in remediation


def test_repair_env_fails_when_not_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    """repair-env refuses non-editable or foreign installs fail-closed."""

    class NonEditableDist:
        version = "1.121.3"

        def read_text(self, filename: str) -> str | None:
            return None  # No direct_url.json -> wheel install

    monkeypatch.setattr(
        "importlib.metadata.Distribution.from_name",
        lambda name: NonEditableDist(),
    )
    result = runner.invoke(app, ["repair-env", "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "failed"
    assert "not installed in editable mode" in data["error"]


# ==============================================================================
# Task 6: Symbol Disambiguation Suggestions for defs and source
# ==============================================================================


def test_symbol_disambiguation_suggestions(tmp_path: Path) -> None:
    """When a symbol is not found, close matches are suggested while preserving exit code 1."""
    source_file = tmp_path / "calc.py"
    source_file.write_text(
        "def calculate_total_amount(x, y):\n    return x + y\n\n"
        "def calculate_tax_rate(amount):\n    return amount * 0.1\n",
        encoding="utf-8",
    )

    # Misspelled symbol: calculate_totl_amount
    result = runner.invoke(app, ["defs", str(source_file), "calculate_totl_amount", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload.get("not_found") is True
    suggestions = payload.get("suggestions", [])
    assert "calculate_total_amount" in suggestions

    # Text mode outputs 'Did you mean: ...?'
    text_result = runner.invoke(app, ["defs", str(source_file), "calculate_totl_amount"])
    assert text_result.exit_code == 1
    assert "Did you mean: calculate_total_amount" in text_result.output

    # Directory target also collects candidate symbols
    dir_result = runner.invoke(app, ["defs", str(tmp_path), "calculate_totl_amount", "--json"])
    assert dir_result.exit_code == 1
    dir_payload = json.loads(dir_result.stdout)
    assert dir_payload.get("not_found") is True
    assert "calculate_total_amount" in dir_payload.get("suggestions", [])


# ==============================================================================
# Task 7: Multi-File Checkpoint Granularity (--paths) & Scoped Undo
# ==============================================================================


def test_checkpoint_paths_confinement_and_scoped_undo(tmp_path: Path) -> None:
    """--paths creates scoped checkpoints; undo preserves files outside the scoped paths."""
    root = tmp_path / "repo"
    root.mkdir()
    sub1 = root / "sub1"
    sub1.mkdir()
    sub2 = root / "sub2"
    sub2.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    file_a = sub1 / "a.txt"
    file_a.write_text("version 1 in sub1", encoding="utf-8")
    file_b = sub2 / "b.txt"
    file_b.write_text("version 1 in sub2", encoding="utf-8")

    # 1. Out-of-root path is rejected fail-closed
    with pytest.raises(ValueError, match="outside checkpoint root"):
        create_checkpoint(path=str(root), paths=["../outside"])

    # 2. Scoped checkpoint only snapshots files within scoped paths
    res = create_checkpoint(path=str(root), paths=["sub1"])
    assert res.file_count == 1
    assert res.scoped_paths == ["sub1"]

    # 3. Add modifications and additions both inside and outside scope
    file_a.write_text("version 2 in sub1", encoding="utf-8")
    file_b.write_text("version 2 in sub2 (should NOT be reverted)", encoding="utf-8")
    unselected_new = sub2 / "new_outside.txt"
    unselected_new.write_text("fresh file outside scope", encoding="utf-8")
    selected_new = sub1 / "new_inside.txt"
    selected_new.write_text("fresh file inside scope", encoding="utf-8")

    # 4. Undo scoped checkpoint
    undo_res = undo_checkpoint(res.checkpoint_id, path=str(root))
    assert undo_res.restored_files == 1

    # In-scope file reverted
    assert file_a.read_text(encoding="utf-8") == "version 1 in sub1"
    # In-scope new file cleaned up
    assert not selected_new.exists()

    # OUT-OF-SCOPE file preserved completely!
    assert file_b.read_text(encoding="utf-8") == "version 2 in sub2 (should NOT be reverted)"
    assert unselected_new.exists()
    assert unselected_new.read_text(encoding="utf-8") == "fresh file outside scope"


def test_checkpoint_paths_single_file_scope_conflict(tmp_path: Path) -> None:
    """Cannot combine single-file path with --paths."""
    single_file = tmp_path / "single.py"
    single_file.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot specify both a single-file path and --paths"):
        create_checkpoint(path=str(single_file), paths=["single.py"])


# ==============================================================================
# Task 8: Structured AST Querying (tg sql) Sandbox & Resource Bounds
# ==============================================================================


@pytest.fixture
def sql_test_env(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    mod1 = proj / "mod1.py"
    mod1.write_text(
        "class UserService:\n"
        "    def get_user(self, user_id: int):\n"
        "        pass\n"
        "    def delete_user(self, user_id: int):\n"
        "        pass\n",
        encoding="utf-8",
    )
    mod2 = proj / "mod2.py"
    mod2.write_text(
        "def helper_func():\n    return 42\n",
        encoding="utf-8",
    )
    return proj


def test_sql_basic_queries(sql_test_env: Path) -> None:
    """Verify basic SELECT queries and safe SQL functions."""
    result = runner.invoke(
        app,
        ["sql", str(sql_test_env), "SELECT symbol, kind FROM symbols WHERE kind='class'", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["rows"][0]["symbol"] == "UserService"
    assert payload["rows"][0]["kind"] == "class"
    assert payload["truncated"] is False
    assert payload["result_incomplete"] is False


def test_sql_safe_function_and_aggregation(sql_test_env: Path) -> None:
    """Verify aggregation and safe string functions (count, upper, length)."""
    result = runner.invoke(
        app,
        [
            "sql",
            str(sql_test_env),
            "SELECT count(*), upper(kind) FROM symbols GROUP BY kind",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] >= 1
    assert payload["result_incomplete"] is False


def test_sql_authorizer_blocks_dangerous_functions(sql_test_env: Path) -> None:
    """Security authorizer blocks memory-bomb functions like randomblob and zeroblob."""
    result = runner.invoke(
        app,
        ["sql", str(sql_test_env), "SELECT randomblob(100) FROM symbols", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "error" in payload
    assert "not authorized" in payload["error"]


def test_sql_authorizer_blocks_writes(sql_test_env: Path) -> None:
    """Security authorizer blocks INSERT, ATTACH, and DDL statements."""
    res1 = runner.invoke(
        app, ["sql", str(sql_test_env), "ATTACH DATABASE ':memory:' AS evil", "--json"]
    )
    assert res1.exit_code == 1

    res2 = runner.invoke(
        app,
        [
            "sql",
            str(sql_test_env),
            "INSERT INTO symbols VALUES ('a','b','c',1,2,'py',null)",
            "--json",
        ],
    )
    assert res2.exit_code == 1


def test_sql_rejects_duplicate_column_aliases(sql_test_env: Path) -> None:
    """Query projecting duplicate column names fails closed with exit 1."""
    result = runner.invoke(
        app,
        ["sql", str(sql_test_env), "SELECT symbol AS x, kind AS x FROM symbols", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "Query contains duplicate column name(s): 'x'" in payload["error"]


def test_sql_rejects_multiple_statements(sql_test_env: Path) -> None:
    """Semicolon statement chaining is rejected fail-closed."""
    result = runner.invoke(
        app,
        ["sql", str(sql_test_env), "SELECT 1; SELECT 2", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "Multiple statements are not permitted" in payload["error"]


def test_sql_lookahead_truncation_exact_vs_exceeded(sql_test_env: Path) -> None:
    """Lookahead distinguishes exact limit match (exit 0) from limit+1 truncation (exit 2)."""
    # Total symbols in sql_test_env is 4 (UserService, get_user, delete_user, helper_func)
    # Case 1: limit > total -> exit 0, truncated=False
    res_more = runner.invoke(
        app, ["sql", str(sql_test_env), "SELECT symbol FROM symbols", "--limit", "10", "--json"]
    )
    assert res_more.exit_code == 0
    p_more = json.loads(res_more.stdout)
    assert p_more["truncated"] is False
    assert p_more["result_incomplete"] is False
    assert p_more["count"] == 4

    # Case 2: limit == total (exactly 4) -> exit 0, truncated=False
    res_exact = runner.invoke(
        app, ["sql", str(sql_test_env), "SELECT symbol FROM symbols", "--limit", "4", "--json"]
    )
    assert res_exact.exit_code == 0
    p_exact = json.loads(res_exact.stdout)
    assert p_exact["truncated"] is False
    assert p_exact["result_incomplete"] is False
    assert p_exact["count"] == 4

    # Case 3: limit < total (limit=2) -> exit 2, truncated=True, result_incomplete=True
    res_trunc = runner.invoke(
        app, ["sql", str(sql_test_env), "SELECT symbol FROM symbols", "--limit", "2", "--json"]
    )
    assert res_trunc.exit_code == 2
    p_trunc = json.loads(res_trunc.stdout)
    assert p_trunc["truncated"] is True
    assert p_trunc["result_incomplete"] is True
    assert p_trunc["count"] == 2


def test_sql_cooperative_deadline_interruption(sql_test_env: Path) -> None:
    """When query exceeds deadline, progress handler triggers exit 2 with all deadline diagnostics."""
    recursive_query = (
        "WITH RECURSIVE r(i) AS (VALUES(0) UNION ALL SELECT i+1 FROM r WHERE i < 1000000) "
        "SELECT count(*) FROM r"
    )
    res_timeout = runner.invoke(
        app,
        ["sql", str(sql_test_env), recursive_query, "--deadline", "0.1", "--json"],
    )
    assert res_timeout.exit_code == 2
    payload = json.loads(res_timeout.stdout)
    assert payload["error"] == "query_deadline_exceeded"
    assert payload["deadline_exceeded"] is True
    assert payload["result_incomplete"] is True
    assert payload["partial"] is True


def test_checkpoint_scoped_undo_rejects_symlink_escape(tmp_path: Path) -> None:
    """Pre-flight check in scoped undo rejects in-scope symlink pointing to an unselected file."""
    root = tmp_path / "repo"
    root.mkdir()
    sub1 = root / "sub1"
    sub1.mkdir()
    sub2 = root / "sub2"
    sub2.mkdir()

    file_a = sub1 / "a.txt"
    file_a.write_text("v1 in sub1", encoding="utf-8")
    file_b = sub2 / "b.txt"
    file_b.write_text("v1 in sub2 (unselected)", encoding="utf-8")

    res = create_checkpoint(path=str(root), paths=["sub1"])
    assert res.scoped_paths == ["sub1"]

    # Replace in-scope file_a with a symlink pointing outside scope to sub2/b.txt (without tampering with metadata.json)
    file_a.unlink()
    try:
        file_a.symlink_to(file_b)
    except (OSError, NotImplementedError):
        # On environments where unprivileged symlink creation is denied, test resolution check via mock
        from unittest.mock import patch

        with patch("tensor_grep.cli.checkpoint_store._matches_scoped_paths", return_value=False):
            with pytest.raises(CheckpointCorruptError, match="escapes the scoped paths"):
                undo_checkpoint(res.checkpoint_id, path=str(root))
        return

    # Normal undo_checkpoint must fail-closed with CheckpointCorruptError during pre-flight resolution
    with pytest.raises(CheckpointCorruptError, match="escapes the scoped paths"):
        undo_checkpoint(res.checkpoint_id, path=str(root))


def test_sql_text_output_byte_bound(sql_test_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SQL text mode output strictly enforces payload byte limits and marks output truncated with exit 2."""
    import tensor_grep.cli.sql_query as sql_query

    # The query returns 4 rows. We test standard text mode output formatting
    res_normal = runner.invoke(app, ["sql", str(sql_test_env), "SELECT symbol, kind FROM symbols"])
    assert res_normal.exit_code == 0
    assert "symbol" in res_normal.output
    assert "(4 rows)" in res_normal.output

    # Test that setting limit=2 causes text output to show [truncated] and exit 2
    res_limit = runner.invoke(
        app, ["sql", str(sql_test_env), "SELECT symbol, kind FROM symbols", "--limit", "2"]
    )
    assert res_limit.exit_code == 2
    assert "(2 rows [truncated])" in res_limit.output

    # Test forced byte-budget truncation by monkeypatching MAX_SQL_PAYLOAD_BYTES to a tiny bound (200 bytes)
    monkeypatch.setattr(sql_query, "MAX_SQL_PAYLOAD_BYTES", 200)
    res_trunc = runner.invoke(app, ["sql", str(sql_test_env), "SELECT symbol, kind FROM symbols"])
    assert res_trunc.exit_code == 2
    assert "truncated" in res_trunc.output
    assert len(res_trunc.output.encode("utf-8")) <= 200


def test_sql_semicolon_in_string_literal_and_comments(sql_test_env: Path) -> None:
    """Semicolons inside SQL string literals and comments are not rejected as multiple statements."""
    result = runner.invoke(
        app,
        [
            "sql",
            str(sql_test_env),
            "SELECT symbol FROM symbols WHERE signature LIKE '%;' OR symbol = 'a;b'",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 0
    assert payload["truncated"] is False

    # Leading comment with path in second argument
    res_comment = runner.invoke(
        app,
        ["sql", "/* query comment */ SELECT symbol FROM symbols", str(sql_test_env), "--json"],
    )
    assert res_comment.exit_code == 0
    p_comment = json.loads(res_comment.stdout)
    assert p_comment["count"] == 4


def test_sql_extended_safe_functions(sql_test_env: Path) -> None:
    """Safe pure SQLite functions (group_concat, replace, ifnull, printf, iif) are permitted."""
    query = (
        "SELECT group_concat(symbol), replace(file, 'a', 'b'), ifnull(signature, 'none'), "
        "printf('%s:%d', symbol, line), iif(kind='class', 1, 0) FROM symbols GROUP BY kind"
    )
    result = runner.invoke(app, ["sql", str(sql_test_env), query, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] >= 1
    assert payload["result_incomplete"] is False


def test_checkpoint_paths_dot_scope(tmp_path: Path) -> None:
    """--paths with '.' or '' correctly matches all files under root."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "file1.txt").write_text("hello", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "file2.txt").write_text("world", encoding="utf-8")

    res = create_checkpoint(path=str(root), paths=["."])
    assert res.file_count == 2
    assert res.scoped_paths == ["."]

    undo_res = undo_checkpoint(res.checkpoint_id, path=str(root))
    assert undo_res.restored_files == 2


def test_defs_does_not_leak_candidate_symbols(tmp_path: Path) -> None:
    """Missing symbol responses contain suggestions but never leak internal candidate_symbols array."""
    f = tmp_path / "mod.py"
    f.write_text("def my_special_function(): pass\n", encoding="utf-8")
    result = runner.invoke(app, ["defs", str(f), "my_special_functin", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload.get("not_found") is True
    assert "candidate_symbols" not in payload
    assert "my_special_function" in payload.get("suggestions", [])


def test_scoped_undo_preserves_out_of_scope_empty_dir(tmp_path: Path) -> None:
    """Scoped undo must not prune empty directories located outside scoped_paths."""
    root = tmp_path / "repo"
    root.mkdir()
    sub1 = root / "sub1"
    sub1.mkdir()
    (sub1 / "a.txt").write_text("content", encoding="utf-8")

    sub2 = root / "sub2"
    sub2.mkdir()
    out_of_scope_empty = sub2 / "empty_dir"
    out_of_scope_empty.mkdir()

    res = create_checkpoint(path=str(root), paths=["sub1"])
    assert res.scoped_paths == ["sub1"]

    undo_checkpoint(res.checkpoint_id, path=str(root))
    assert out_of_scope_empty.exists(), (
        "Out-of-scope empty directory was mistakenly pruned by scoped undo!"
    )


def test_symbol_disambiguation_caps_candidate_length_at_64(tmp_path: Path) -> None:
    """Candidates longer than 64 characters are excluded from suggestions to prevent payload bloat."""
    f = tmp_path / "mod.py"
    long_symbol = "a" * 70
    valid_symbol = "a" * 60
    f.write_text(f"def {long_symbol}(): pass\ndef {valid_symbol}(): pass\n", encoding="utf-8")

    result = runner.invoke(app, ["defs", str(f), "a" * 59, "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    suggestions = payload.get("suggestions", [])
    assert long_symbol not in suggestions, "Symbol > 64 chars should be excluded"
    assert valid_symbol in suggestions, "Symbol <= 64 chars should be suggested"


def test_repair_env_fails_when_metadata_mismatches_expected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repair-env fails closed when installed distribution metadata version mismatches source pyproject version."""
    import importlib.metadata
    from unittest.mock import MagicMock

    monkeypatch.setattr("tensor_grep.cli.runtime_paths._repo_root", lambda: tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "tensor-grep"\nversion = "1.121.3"\n', encoding="utf-8")

    mock_dist = MagicMock()
    mock_dist.version = "1.119.16"  # remains stale even after install
    mock_dist.read_text.return_value = json.dumps({
        "dir_info": {"editable": True},
        "url": f"file:///{tmp_path.as_posix()}",
    })
    monkeypatch.setattr(importlib.metadata.Distribution, "from_name", lambda name: mock_dist)
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="", stderr="")
    )

    result = runner.invoke(app, ["repair-env", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "Verification failed" in payload["error"]


def test_defs_payload_candidate_symbols_are_bounded_near_misses(tmp_path: Path) -> None:
    """build_symbol_defs_from_map feeds MCP/LSP/context consumers that never pop
    candidate_symbols, so it must carry only bounded near-misses, not the repo inventory."""
    from tensor_grep.cli.repo_map import build_repo_map, build_symbol_defs_from_map

    lines = [f"def unrelated_helper_{i}():\n    pass\n" for i in range(40)]
    lines.append("def my_special_function():\n    pass\n")
    (tmp_path / "mod.py").write_text("\n".join(lines), encoding="utf-8")
    payload = build_symbol_defs_from_map(build_repo_map(tmp_path), "my_special_functin")
    candidates = payload.get("candidate_symbols", [])
    assert "my_special_function" in candidates
    assert len(candidates) <= 20
    assert not any(c.startswith("unrelated_helper_") for c in candidates)


def test_sql_scan_limit_truncation_is_incomplete_and_disclosed(sql_test_env: Path) -> None:
    """A --max-repo-files cap surfaces as scan_limit.possibly_truncated (not `partial`); tg sql must
    still report it incomplete (exit 2) and say so on STDOUT in text mode, not only on stderr."""
    query = "SELECT symbol FROM symbols"
    json_result = runner.invoke(
        app, ["sql", str(sql_test_env), query, "--max-repo-files", "1", "--json"]
    )
    assert json_result.exit_code == 2, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["result_incomplete"] is True
    assert payload.get("scan_incomplete") is True

    text_result = runner.invoke(app, ["sql", str(sql_test_env), query, "--max-repo-files", "1"])
    assert text_result.exit_code == 2
    assert "INCOMPLETE" in text_result.stdout


def test_sql_scan_limit_default_mirrors_main() -> None:
    from tensor_grep.cli import main as cli_main
    from tensor_grep.cli import sql_query

    assert sql_query._DEFAULT_AGENT_REPO_SCAN_LIMIT == cli_main._DEFAULT_AGENT_REPO_SCAN_LIMIT


def test_repair_env_on_a_wheel_install_explains_instead_of_blaming_a_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dogfooded on the published 1.122.0 wheel: `_repo_root()` resolves under site-packages,
    which has no pyproject.toml. That is the NORMAL install, so the refusal must say repair-env
    does not apply (and point at `tg upgrade`), not read like a broken environment."""
    monkeypatch.setattr("tensor_grep.cli.runtime_paths._repo_root", lambda: tmp_path)
    result = runner.invoke(app, ["repair-env", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_applicable"
    assert payload["reason"] == "no_source_checkout"
    assert "Nothing to repair" in payload["message"]
    assert "tg upgrade" in payload["message"]


def test_repair_env_on_a_wheel_install_text_mode_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Text-mode mirrors the JSON contract: a wheel install exits 0, not 1."""
    monkeypatch.setattr("tensor_grep.cli.runtime_paths._repo_root", lambda: tmp_path)
    result = runner.invoke(app, ["repair-env"])
    assert result.exit_code == 0, result.stdout
    assert "Nothing to repair" in result.stdout
    assert "tg upgrade" in result.stdout


def test_repair_env_non_editable_install_in_a_checkout_still_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real source checkout (pyproject.toml present) whose install is not editable, or points
    elsewhere, stays a hard failure (exit 1, status failed) -- only the no-source-checkout branch
    changes."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='tensor-grep'\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.runtime_paths._repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.cli.repair_env.editable_install_points_at",
        lambda repo_root: (False, "1.0.0"),
    )
    result = runner.invoke(app, ["repair-env", "--json"])
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "not installed in editable mode" in payload["error"]
