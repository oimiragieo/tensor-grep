import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import repo_map
from tensor_grep.cli.main import (
    _candidate_versions_from_pip_index_output,
    _candidate_versions_from_pypi_json,
    _candidate_versions_from_pypi_simple_index,
    _highest_tensor_grep_version,
    app,
)
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_navigation_pack_prefetches_same_directory_related_and_test_reads_into_primary_phase(
    tmp_path,
):

    src_dir = tmp_path / "src" / "tools"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "glob.ts"
    sibling_a = src_dir / "grep.ts"
    sibling_b = src_dir / "read-many-files.ts"
    test_path = src_dir / "glob.test.ts"
    for path, text in (
        (module_path, "export function globTool(): string { return 'glob'; }\n"),
        (sibling_a, "export function grepTool(): string { return 'grep'; }\n"),
        (sibling_b, "export function readManyFiles(): string { return 'read'; }\n"),
        (test_path, "export function testGlobTool(): string { return 'test'; }\n"),
    ):
        path.write_text(text, encoding="utf-8")

    repo_fixture = {
        "symbols": [
            {
                "name": "testGlobTool",
                "file": str(test_path.resolve()),
                "path": str(test_path.resolve()),
                "start_line": 1,
                "end_line": 1,
                "kind": "function",
            }
        ]
    }
    payload = {
        "edit_plan_seed": {
            "primary_file": str(module_path.resolve()),
            "primary_symbol": {"name": "globTool"},
            "primary_span": {"start_line": 1, "end_line": 1},
            "reasons": ["primary-symbol"],
            "confidence": {"overall": 0.95},
            "validation_tests": [str(test_path.resolve())],
            "validation_commands": ["npx vitest run"],
            "edit_ordering": [
                str(module_path.resolve()),
                str(sibling_a.resolve()),
                str(sibling_b.resolve()),
            ],
            "rollback_risk": 0.15,
        },
        "candidate_edit_targets": {
            "spans": [
                {
                    "file": str(module_path.resolve()),
                    "symbol": "globTool",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "primary",
                },
                {
                    "file": str(sibling_a.resolve()),
                    "symbol": "grepTool",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
                {
                    "file": str(sibling_b.resolve()),
                    "symbol": "readManyFiles",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
            ]
        },
    }

    navigation_pack = repo_map._navigation_pack(repo_fixture, payload, max_reads=5)

    groups = navigation_pack["parallel_read_groups"]
    assert len(groups) == 1
    assert groups[0]["label"] == "primary"
    assert sorted(groups[0]["roles"]) == ["primary", "related", "related", "test"]
    assert sorted(groups[0]["files"]) == sorted(
        [
            str(module_path.resolve()),
            str(sibling_a.resolve()),
            str(sibling_b.resolve()),
            str(test_path.resolve()),
        ]
    )


def test_files_with_matches_lists_unique_matched_files(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR here", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_files_with_matches_preserves_discovery_order(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["b.py", "a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "b.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR first", file="b.py")],
                total_files=1,
                total_matches=1,
            ),
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR second", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["b.py", "a.py"]


def test_files_with_matches_should_respect_total_files_without_materialized_matches(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(matches=[], total_files=1, total_matches=3),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches", "-c"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_cli_stats_should_respect_count_only_ripgrep_results(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeRipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "-c"])

    assert result.exit_code == 0
    assert "[stats] scanned_files=2 matched_files=1 total_matches=3" in result.output


def test_files_with_matches_should_use_count_only_ripgrep_file_paths(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeRipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches", "-c"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_files_with_matches_ripgrep_backend_searches_roots_not_expanded_candidates(
    monkeypatch,
):
    seen: dict[str, object] = {}
    global _FAKE_WALK
    _FAKE_WALK = {".": [f"src/file_{index}.py" for index in range(5000)]}

    class RipgrepBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            seen["paths"] = list(file_path)
            seen["pattern"] = pattern
            seen["fixed_strings"] = config.fixed_strings
            seen["null"] = config.null
            return SearchResult(
                matches=[],
                matched_file_paths=["src/file_1.py"],
                total_files=1,
                total_matches=1,
                routing_backend="RipgrepBackend",
                routing_reason="rg_files_with_matches",
            )

    class _RipgrepPipeline:
        def __init__(self, force_cpu=False, config=None):
            self.backend = RipgrepBackend()
            self.selected_backend_name = "RipgrepBackend"
            self.selected_backend_reason = "rg_files_with_matches"
            self.selected_gpu_device_ids = []
            self.selected_gpu_chunk_plan_mb = []

        def get_backend(self):
            return self.backend

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _RipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "--fixed-strings", "ERROR", ".", "--files-with-matches", "-0"]
    )

    assert result.exit_code == 0
    assert seen["paths"] == ["."]
    assert seen["pattern"] == "ERROR"
    assert seen["fixed_strings"] is True
    assert seen["null"] is True
    assert result.stdout == "src/file_1.py\x00"


def test_cli_uses_ripgrep_passthrough_for_files_with_matches(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["files_with_matches"] = config.files_with_matches
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--fixed-strings", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert calls == {
        "paths": ["."],
        "pattern": "ERROR",
        "files_with_matches": True,
        "fixed_strings": True,
    }


def test_cli_pcre2_rg_format_is_passthrough_eligible() -> None:
    from tensor_grep.cli.main import _can_passthrough_rg

    config = SearchConfig(pcre2=True, sort_by="path")

    assert _can_passthrough_rg(
        config,
        format_type="rg",
        explicit_rg_format=False,
        json_mode=False,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    )


def test_cli_uses_ripgrep_passthrough_for_explicit_rg_json(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["json_mode"] = config.json_mode
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--format", "rg", "--json", "--fixed-strings", "ERROR", "."],
    )
    result = runner.invoke(
        app,
        ["search", "--format", "rg", "--json", "--fixed-strings", "ERROR", "."],
    )

    assert result.exit_code == 0
    assert calls == {
        "paths": ["."],
        "pattern": "ERROR",
        "json_mode": True,
        "fixed_strings": True,
    }


def test_cli_does_not_treat_default_json_as_rg_json_passthrough() -> None:
    from tensor_grep.cli.main import _can_passthrough_rg

    config = SearchConfig(json_mode=True)

    assert not _can_passthrough_rg(
        config,
        format_type="rg",
        explicit_rg_format=False,
        json_mode=True,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    )


def test_cli_uses_implicit_rg_root_for_no_path_files_with_matches(monkeypatch, tmp_path):
    """Asserts a no-path search forwards an EMPTY `paths` list (the implicit-root contract).

    Runs from an ISOLATED cwd. It used to run from whatever directory pytest was launched in --
    i.e. the real repo root -- which made the assertion depend on ambient filesystem state. A
    permission-denied directory at the repo root (task #268) makes the implicit-root walk report
    INCOMPLETE, so `tg` correctly exits 2 per the three-state contract (0 complete / 1 not-found /
    2 incomplete) and this test false-failed on `assert 2 == 0`. The product was right and the test
    was wrong: nothing here is about the repo's contents, so it must not read them.
    """
    monkeypatch.chdir(tmp_path)
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["files_with_matches"] = config.files_with_matches
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--fixed-strings", "ERROR", "--files-with-matches"])

    assert result.exit_code == 0
    assert calls == {
        "paths": [],
        "pattern": "ERROR",
        "files_with_matches": True,
        "fixed_strings": True,
    }


def test_files_without_match_lists_unmatched_files(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR here", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-without-match"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "b.py"


def test_files_without_match_respects_scanned_candidates_for_hidden_relative_root(
    monkeypatch,
):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".fixture-") as temp_dir:
        hidden_root = Path(temp_dir)
        (hidden_root / "large.txt").write_text("NEEDLE\n" * 5, encoding="utf-8")
        (hidden_root / "empty.txt").write_text("other\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "NEEDLE",
                hidden_root.name,
                "--files-without-match",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0
    assert result.stdout.strip() == str(Path(hidden_root.name) / "empty.txt")


def test_files_without_match_skips_gitignored_directories(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=False, capture_output=True, text=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()

    kept = tmp_path / "src" / "empty.txt"
    ignored = tmp_path / "build" / "ignored.txt"
    matched = tmp_path / "src" / "matched.txt"

    kept.write_text("other\n", encoding="utf-8")
    ignored.write_text("other\n", encoding="utf-8")
    matched.write_text("NEEDLE\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "search",
            "NEEDLE",
            str(tmp_path),
            "--files-without-match",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert str(kept) in result.stdout
    assert str(ignored) not in result.stdout


def test_search_rejects_empty_pattern(tmp_path: Path):
    target = tmp_path / "sample.py"
    target.write_text("print('hello')\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "", str(target)])

    assert result.exit_code == 2
    assert "PATTERN must not be empty" in result.output


def test_search_json_reports_empty_pattern_error(tmp_path: Path):
    target = tmp_path / "sample.py"
    target.write_text("print('hello')\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--json", "", str(target)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "empty_pattern"
    assert "PATTERN must not be empty" in payload["detail"]


def test_search_reports_missing_input_paths(tmp_path: Path):
    missing = tmp_path / "missing.py"

    runner = CliRunner()
    result = runner.invoke(app, ["search", "hello", str(missing)])

    assert result.exit_code == 2
    assert str(missing) in result.output
    assert "does not exist" in result.output


def test_search_json_reports_missing_input_path_error(tmp_path: Path):
    missing = tmp_path / "missing.py"

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--json", "hello", str(missing)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "path_not_found"
    assert str(missing) in payload["detail"]
    assert "does not exist" in payload["detail"]


def test_files_with_matches_null_outputs_nul_separator(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "-l", "-0"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.endswith(b"\x00")
    assert b"\r\n" not in result.stdout


def test_files_with_matches_text_outputs_single_platform_newline(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "-l"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.rstrip(b"\r\n") == os.fsencode(str(target))
    assert result.stdout.endswith(b"\n")
    assert not result.stdout.endswith(b"\r\r\n")
    assert result.stdout.count(b"\n") == 1


def test_only_matching_outputs_token_not_whole_line(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="prefix ERROR suffix", file="a.py")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "-o"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ERROR"


def test_tg_run_uses_typer_help():
    from tensor_grep.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "Usage: " in result.stdout
    assert "Options" in result.stdout
    assert "positional arguments:" not in result.stdout


def test_tg_run_help_should_position_ast_as_validated_slice_not_ast_grep_parity():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", help_text)
    assert "validated AST slice" in normalized_help
    assert "PowerShell users should single-quote AST patterns" in normalized_help
    assert "--selector" in help_text
    assert "--strictness" in help_text
    assert "--stdin" in help_text
    assert "--globs" in help_text
    assert "ast-grep parity" not in help_text
    assert "ast-grep replacement" not in help_text


def test_tg_search_help_should_not_claim_tg_run_ast_grep_parity():
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    # Normalize BOTH whitespace and markdown backticks before asserting.
    #
    # The help body is a markdown string and `app.rich_markup_mode == "markdown"`, so when rich is
    # loaded Typer renders the cross-reference as `tg run: Run a validated AST slice` with the
    # backticks consumed as formatting. When rich is NOT in sys.modules Typer falls back to plain
    # Click, which emits the RAW markdown -- backticks intact -- and the identical, correct help
    # text stops matching a backtick-free substring.
    #
    # That is reachable in tests purely through collection/import order: measured, `rich` was in
    # sys.modules for a node-id run (help 30001 chars) and ABSENT when a sibling test module was
    # collected alongside (help 16379 chars), with `rich_markup_mode` identical in both. It is not
    # reachable for users -- `rich` is a CORE dependency, so the shipped CLI always renders rich.
    # Asserting the INVARIANT (the cross-reference is present) rather than one renderer's exact
    # bytes is what makes this test honest about what it protects.
    normalized_help = re.sub(r"\s+", " ", help_text).replace("`", "")
    assert "tg run: Run a validated AST slice" in normalized_help
    assert "ast-grep parity" not in help_text


@pytest.mark.parametrize("command", ["scan", "test", "new"])
def test_ast_workflow_help_should_position_commands_as_bounded_ast_slice(command: str) -> None:
    runner = CliRunner()

    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", help_text)
    assert "bounded AST" in normalized_help
    assert "ast-grep replacement" not in help_text
    assert "full ast-grep" not in help_text


def test_cli_uses_ripgrep_passthrough_fast_path(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", "."])

    assert result.exit_code == 0
    assert calls["pattern"] == "ERROR"
    assert calls["paths"] == ["."]


def test_cli_disables_ripgrep_passthrough_for_ltl_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[
                    MatchLine(line_number=1, text="AUTH_FAIL", file="a.log"),
                    MatchLine(line_number=3, text="DB_TIMEOUT", file="a.log"),
                ],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "AUTH_FAIL -> eventually DB_TIMEOUT", ".", "--ltl"])

    assert result.exit_code == 0
    assert called["passthrough"] is False


def test_cli_uses_ripgrep_passthrough_for_replace_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="REPLACED", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--replace", "REPLACED"])

    assert result.exit_code == 0
    assert called["passthrough"] is True


def test_cli_uses_ripgrep_passthrough_for_short_replace_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="REPLACED", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "-r", "REPLACED"])

    assert result.exit_code == 0
    assert called["passthrough"] is True


def test_cli_replaces_rg_capture_groups_in_output(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="abc123", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "(?P<letters>[a-z]+)(?P<digits>[0-9]+)",
            ".",
            "--replace",
            "$digits-${letters}-$1-$2-$$-$0-${1}a-$1a",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "123-abc-abc-123-$-abc123-abca-"


def test_cli_replaces_rg_capture_groups_for_fixed_strings(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="hello world", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "hello", ".", "-F", "--replace", "$0-${1}a-$1-$$"],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello-a--$ world"


def test_cli_keeps_non_ascii_replacement_tokens_literal(monkeypatch):
    arabic_digit_one = "\u0661"
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="abc123", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "(?P<letters>[a-z]+)(?P<digits>[0-9]+)",
            ".",
            "--replace",
            f"$digits-$ébar-${arabic_digit_one}-$$",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == f"123-$ébar-${arabic_digit_one}-$"


def test_upgrade_uses_uv_when_available(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    versions = iter(["0.31.0", "0.32.0"])

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: next(versions))
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout


def test_upgrade_restarts_preexisting_session_daemon_after_handoff_loss(monkeypatch):
    calls: list[list[str]] = []
    daemon_statuses = iter(
        [
            {
                "running": True,
                "root": r"C:\dev\projects\tensor-grep",
                "host": "127.0.0.1",
                "port": 43123,
                "pid": 9001,
            },
            {
                "running": False,
                "root": r"C:\dev\projects\tensor-grep",
                "stale_metadata": True,
            },
        ]
    )
    restarted: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: next(daemon_statuses),
    )
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.start_session_daemon",
        lambda path: (
            restarted.append(path) or {"running": True, "root": path, "auto_started": True}
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert restarted == [r"C:\dev\projects\tensor-grep"]
    assert "Session daemon restarted after upgrade" in result.stdout


def test_upgrade_does_not_start_session_daemon_when_none_was_running(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: {"running": False, "root": r"C:\dev\projects\tensor-grep"},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.start_session_daemon",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("upgrade should not start a daemon that was not already running")
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert "Session daemon restarted after upgrade" not in result.stdout


def test_upgrade_pins_exact_latest_pypi_version_when_local_metadata_is_stale(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any("tensor-grep==0.33.0" in cmd for cmd in calls)
    assert calls[0][-1] == "tensor-grep==0.33.0"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout


def test_upgrade_latest_version_candidates_skip_yanked_pypi_releases():
    payload = {
        "info": {"version": "0.34.0"},
        "releases": {
            "0.32.0": [{"yanked": False}],
            "0.33.0": [{"yanked": False}],
            "0.34.0": [{"yanked": True}],
        },
    }
    simple_index = """
    <a href="tensor_grep-0.33.0-py3-none-any.whl">tensor_grep-0.33.0-py3-none-any.whl</a>
    <a href="tensor_grep-0.34.0-py3-none-any.whl" data-yanked="bad release">tensor_grep-0.34.0-py3-none-any.whl</a>
    """

    candidates = [
        *_candidate_versions_from_pypi_json(payload),
        *_candidate_versions_from_pypi_simple_index(simple_index),
    ]

    assert _highest_tensor_grep_version(candidates) == "0.33.0"


def test_upgrade_latest_version_candidates_include_pip_index_output():
    pip_output = """
    tensor-grep (0.34.0)
    Available versions: 0.34.0, 0.33.0, 0.32.0
      INSTALLED: 0.32.0
      LATEST:    0.34.0
    """

    assert (
        _highest_tensor_grep_version(_candidate_versions_from_pip_index_output(pip_output))
        == "0.34.0"
    )


def test_latest_pypi_probe_uses_pip_index_when_json_and_simple_are_stale(monkeypatch):
    class _FakeResponse:
        def __init__(self, body: str) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self.body.encode("utf-8")

    stale_json = json.dumps(
        {
            "info": {"version": "0.33.0"},
            "releases": {
                "0.32.0": [{"yanked": False}],
                "0.33.0": [{"yanked": False}],
            },
        }
    )
    stale_simple = """
    <a href="tensor_grep-0.33.0-py3-none-any.whl">tensor_grep-0.33.0-py3-none-any.whl</a>
    """
    calls: list[list[str]] = []

    def _fake_urlopen(request, timeout=None):
        url = request.get_full_url()
        if url.endswith("/json"):
            return _FakeResponse(stale_json)
        if url.endswith("/simple/tensor-grep/"):
            return _FakeResponse(stale_simple)
        raise AssertionError(f"unexpected url: {url}")

    def _fake_run(cmd, **_kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "tensor-grep (0.34.0)\n"
                "Available versions: 0.34.0, 0.33.0, 0.32.0\n"
                "  LATEST:    0.34.0\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)
    # The autouse _doctor_offline fixture sets TG_DOCTOR_OFFLINE=1; this test exercises the REAL
    # probe, so clear it (raising=False: absent in some collection orders).
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)

    assert cli_main._latest_pypi_tensor_grep_version(timeout_seconds=1.0) == "0.34.0"
    assert calls
    assert calls[0][1:5] == ["-m", "pip", "index", "versions"]
    assert "--no-cache-dir" in calls[0]


def test_upgrade_reports_latest_pypi_version_when_verified_version_matches_latest(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any("tensor-grep==0.32.0" in cmd for cmd in calls)
    assert "tensor-grep is already at the latest PyPI version (0.32.0)." in result.stdout


def test_native_frontdoor_asset_candidates_default_to_cpu_even_when_host_has_nvidia(monkeypatch):

    def _fake_run(cmd, capture_output=True, text=True, check=False, timeout=None):
        raise AssertionError(f"default asset selection should not probe hardware: {cmd}")

    monkeypatch.delenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", raising=False)
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        cli_main.shutil, "which", lambda name: name if name == "nvidia-smi" else None
    )
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    candidates = cli_main._native_frontdoor_asset_candidates()

    assert [(candidate.flavor, candidate.asset_name) for candidate in candidates] == [
        ("cpu", "tg-linux-amd64-cpu"),
    ]


def test_native_frontdoor_asset_candidates_prefer_nvidia_only_when_requested(monkeypatch):

    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    candidates = cli_main._native_frontdoor_asset_candidates()

    assert [(candidate.flavor, candidate.asset_name) for candidate in candidates] == [
        ("nvidia", "tg-linux-amd64-nvidia"),
        ("cpu", "tg-linux-amd64-cpu"),
    ]


def test_upgrade_falls_back_to_cpu_native_asset_when_nvidia_asset_is_unavailable(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        if str(url).endswith("tg-windows-amd64-nvidia.exe"):
            raise OSError("404 Not Found")
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-nvidia.exe",
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    metadata = json.loads(
        native_binary.with_name("tg-native-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata == {
        "artifact": "tensor_grep_native_frontdoor_metadata",
        "asset_flavor": "cpu",
        "asset_name": "tg-windows-amd64-cpu.exe",
        "requested_asset_flavor": "nvidia",
        "version": "0.33.0",
    }
    assert "Native tg front door refreshed to 0.33.0." in result.stdout
    assert "Native asset flavor: cpu." in result.stdout
    assert "GPU promotion" not in result.stdout


def test_upgrade_falls_back_to_cpu_native_asset_when_nvidia_asset_smoke_fails(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            # frontdoor-download-held-fd task: the fake urlopen below writes the real bytes to
            # this temp path via the download's own streaming loop, so the reported version can
            # be derived from the actual on-disk content instead of a side-channel dict keyed by
            # a temp path this fake no longer sees (urlopen only receives `url` + `timeout`).
            content = Path(command[0]).read_text(encoding="utf-8")
            version = "0.33.0" if content == "new native" else "0.32.0"
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        if str(url).endswith("tg-windows-amd64-nvidia.exe"):
            return _FakeUrlopenResponse(b"wrong native")
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-nvidia.exe",
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert "Native tg front door refreshed to 0.33.0." in result.stdout
    assert "Native asset flavor: cpu." in result.stdout


def test_upgrade_restores_previous_native_binary_when_install_verification_fails(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0"
                if native_binary.read_text(encoding="utf-8") == "verified native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        return _FakeUrlopenResponse(b"bad installed native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "old native"
    assert "release-native front-door asset install failed" in result.stderr


def test_upgrade_refreshes_managed_native_frontdoor_after_package_upgrade(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    unrelated_native_env = tmp_path / "other" / "bin" / "tg.exe"
    unrelated_native_env.parent.mkdir(parents=True)
    unrelated_native_env.write_text("other native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlopen(url, timeout=None):
        downloads.append(str(url))
        return _FakeUrlopenResponse(b"new native")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(unrelated_native_env))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    _allow_native_frontdoor_checksum(monkeypatch)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe"
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    metadata = json.loads(
        native_binary.with_name("tg-native-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["asset_flavor"] == "cpu"
    assert metadata["requested_asset_flavor"] == "cpu"
    assert metadata["asset_name"] == "tg-windows-amd64-cpu.exe"
    assert metadata["version"] == "0.33.0"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout
    assert "Native tg front door refreshed to 0.33.0." in result.stdout


def test_upgrade_repairs_windows_path_order_for_python_subprocess_tg(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "Python314" / "Scripts"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")
    managed_dir = native_binary.parent
    user_path = {"value": f"{foreign_dir};{managed_dir}"}

    class _FakeKey:
        def __init__(self, root, subkey):
            self.root = root
            self.subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_EXPAND_SZ = 2
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey = lambda root, subkey, *_args: _FakeKey(root, subkey)

    def _query_value_ex(key, name):
        if name != "Path" or key.root is not fake_winreg.HKEY_CURRENT_USER:
            raise OSError("missing registry value")
        return user_path["value"], fake_winreg.REG_EXPAND_SZ

    def _set_value_ex(key, name, _reserved, _value_type, value):
        assert key.root is fake_winreg.HKEY_CURRENT_USER
        assert name == "Path"
        user_path["value"] = value

    fake_winreg.QueryValueEx = _query_value_ex
    fake_winreg.SetValueEx = _set_value_ex

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", f"{foreign_dir};{managed_dir}")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version", _fake_candidate_version
    )

    result = CliRunner().invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert user_path["value"].split(";")[0] == str(managed_dir)
    assert os.environ["PATH"].split(";")[0] == str(managed_dir)
    assert "Windows PATH now prefers managed native tg.exe" in result.stdout


def test_windows_path_repair_reports_machine_path_python_subprocess_blocker(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "MachinePython314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    user_path = {"value": str(native_binary.parent)}
    machine_path = {"value": str(foreign_dir)}

    class _FakeKey:
        def __init__(self, root, subkey):
            self.root = root
            self.subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_EXPAND_SZ = 2
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey = lambda root, subkey, *_args: _FakeKey(root, subkey)

    def _query_value_ex(key, name):
        if name != "Path":
            raise OSError("missing registry value")
        if key.root is fake_winreg.HKEY_CURRENT_USER:
            return user_path["value"], fake_winreg.REG_EXPAND_SZ
        if key.root is fake_winreg.HKEY_LOCAL_MACHINE:
            return machine_path["value"], fake_winreg.REG_EXPAND_SZ
        raise OSError("missing registry value")

    def _set_value_ex(key, name, _reserved, _value_type, value):
        assert key.root is fake_winreg.HKEY_CURRENT_USER
        assert name == "Path"
        user_path["value"] = value

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    fake_winreg.QueryValueEx = _query_value_ex
    fake_winreg.SetValueEx = _set_value_ex

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", f"{foreign_dir};{native_binary.parent}")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    message = cli_main._ensure_windows_managed_native_first_on_path(native_binary)

    assert message is not None
    assert "Python subprocess" in message
    assert "Machine PATH" in message
    assert str(foreign_dir) in message
    assert str(native_binary.parent) in message
    assert "Do not remove unrelated launchers" in message
    assert "repair-launcher --allow-foreign-rename" in message
    assert "Windows PATH now prefers managed native tg.exe" not in message


def test_upgrade_removes_stale_tensor_grep_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    foreign_dir = tmp_path / "ForeignPython" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("foreign launcher", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent};{foreign_dir}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(stale_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message
    assert str(stale_tg) in message
    assert not stale_tg.exists()
    assert foreign_tg.exists()
    assert native_binary.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_removes_shadowing_tensor_grep_python_scripts_launcher_even_when_current(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("shadowing tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.33.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.33.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{native_binary.parent};{stale_dir}")
    monkeypatch.setattr(
        cli_main,
        "_doctor_fresh_shell_path_value",
        lambda: f"{stale_dir};{native_binary.parent}",
    )
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message
    assert not stale_tg.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_removes_broken_tensor_grep_python_scripts_launcher_by_package_owner(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("broken tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return None
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(stale_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert not stale_tg.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_detects_owned_python_scripts_launcher_without_python_named_root(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "miniconda3" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert not stale_tg.exists()
