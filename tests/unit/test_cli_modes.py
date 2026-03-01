from dataclasses import dataclass

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult


@dataclass
class _FakeBackend:
    results_by_file: dict[str, SearchResult]

    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        return self.results_by_file.get(
            file_path, SearchResult(matches=[], total_files=0, total_matches=0)
        )


@dataclass
class _FakePipeline:
    backend: _FakeBackend

    def __init__(self, force_cpu=False, config=None):
        self.backend = _FAKE_BACKEND

    def get_backend(self):
        return self.backend


class _FakeScanner:
    def __init__(self, config=None):
        pass

    def walk(self, path):
        yield from _FAKE_WALK.get(path, [])


@dataclass
class _FakeRipgrepBackend:
    called: bool = False
    seen_paths: list[str] | None = None
    seen_pattern: str | None = None

    def search_passthrough(self, paths, pattern, config=None):
        self.called = True
        self.seen_paths = list(paths)
        self.seen_pattern = pattern
        return 0


_FAKE_BACKEND = _FakeBackend(results_by_file={})
_FAKE_WALK: dict[str, list[str]] = {}


def _patch_cli_dependencies(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)


def test_files_mode_lists_candidates(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "x", ".", "--files"])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]


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
    result = runner.invoke(app, ["search", "ERROR", ".", "--only-matching"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py:1:ERROR"


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
