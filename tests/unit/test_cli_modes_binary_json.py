import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes_shared import _FakeBackend, _patch_cli_dependencies

_FAKE_WALK: dict[str, list[str]] = {}
_FAKE_BACKEND = _FakeBackend(results_by_file={})


def _binary_results() -> _FakeBackend:
    return _FakeBackend(
        results_by_file={
            "a.bin": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text='binary file matches (found "\\0" byte around offset 6)',
                        file="a.bin",
                        meta_variables={"binary_notice": True},
                    )
                ],
                total_files=1,
                total_matches=1,
                binary_match_files=1,
            ),
            "b.bin": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text='binary file matches (found "\\0" byte around offset 6)',
                        file="b.bin",
                        meta_variables={"binary_notice": True},
                    )
                ],
                total_files=1,
                total_matches=1,
                binary_match_files=1,
            ),
        }
    )


def _binary_notice(path: str) -> SearchResult:
    return SearchResult(
        matches=[
            MatchLine(
                line_number=1,
                text='binary file matches (found "\\0" byte around offset 6)',
                file=path,
                meta_variables={"binary_notice": True},
            )
        ],
        total_files=1,
        total_matches=1,
        binary_match_files=1,
    )


def test_search_merges_binary_match_file_counts_into_json_output(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.bin", "b.bin"]}
    _FAKE_BACKEND = _binary_results()
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "needle", ".", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["binary_match_files"] == 2


def test_search_merges_binary_match_file_counts_into_ndjson_output(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.bin", "b.bin"]}
    _FAKE_BACKEND = _binary_results()
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "needle", ".", "--ndjson"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows[0]["binary_match_files"] == 2


@pytest.mark.parametrize("format_option", ["--json", "--ndjson"])
def test_search_counts_relative_and_absolute_binary_paths_once(
    monkeypatch, tmp_path, format_option
):
    global _FAKE_WALK, _FAKE_BACKEND
    monkeypatch.chdir(tmp_path)
    binary_path = tmp_path / "binary.bin"
    binary_path.write_bytes(b"needle\0payload")
    aliases = [binary_path.name, str(binary_path.resolve())]
    _FAKE_WALK = {".": aliases}
    _FAKE_BACKEND = _FakeBackend(results_by_file={path: _binary_notice(path) for path in aliases})
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "needle", ".", format_option])

    assert result.exit_code == 0
    if format_option == "--json":
        assert json.loads(result.stdout)["binary_match_files"] == 1
    else:
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert {row["binary_match_files"] for row in rows} == {1}


@pytest.mark.parametrize("format_option", ["--json", "--ndjson"])
def test_search_counts_symlink_binary_alias_once(monkeypatch, tmp_path, format_option):
    global _FAKE_WALK, _FAKE_BACKEND
    monkeypatch.chdir(tmp_path)
    binary_path = tmp_path / "binary.bin"
    alias_path = tmp_path / "binary-alias.bin"
    binary_path.write_bytes(b"needle\0payload")
    try:
        alias_path.symlink_to(binary_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a file symlink on this host: {exc}")

    aliases = [str(binary_path), str(alias_path)]
    _FAKE_WALK = {".": aliases}
    _FAKE_BACKEND = _FakeBackend(results_by_file={path: _binary_notice(path) for path in aliases})
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "needle", ".", format_option])

    assert result.exit_code == 0
    if format_option == "--json":
        assert json.loads(result.stdout)["binary_match_files"] == 1
    else:
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert {row["binary_match_files"] for row in rows} == {1}


def test_search_uses_absolute_path_fallback_when_resolution_fails(monkeypatch, tmp_path):
    global _FAKE_WALK, _FAKE_BACKEND
    monkeypatch.chdir(tmp_path)
    binary_path = tmp_path / "binary.bin"
    binary_path.write_bytes(b"needle\0payload")
    aliases = [binary_path.name, str(binary_path.resolve())]
    _FAKE_WALK = {".": aliases}
    _FAKE_BACKEND = _FakeBackend(results_by_file={path: _binary_notice(path) for path in aliases})
    original_resolve = Path.resolve

    def fail_binary_resolution(path, strict=False):
        if path.name == binary_path.name:
            raise OSError("simulated canonicalization failure")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_binary_resolution)
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "needle", ".", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["binary_match_files"] == 1
