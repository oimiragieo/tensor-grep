"""Unknown source evidence must remain tentative, while complete nonedges stay rejected."""

import ast
import errno
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map as rm


def _write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("failure", ["unreadable", "over-cap", "malformed", "dynamic"])
def test_supported_unknown_source_keeps_heuristic_consumer_without_confirmed_distance(
    tmp_path, monkeypatch, failure
):
    target = _write(tmp_path / "pkg/a.py", "def unique_target():\n    return 1\n")
    candidate = _write(
        tmp_path / "candidate.py",
        "from pkg.a import unique_target\n\ndef candidate():\n    return 0\n",
    )
    before = rm._imports_with_lines_for_path(Path(candidate))
    assert before, "supported import parser positive control failed"
    assert rm._confirm_import_edges(Path(candidate), target, tmp_path), (
        "concrete resolver positive control failed"
    )
    repo = rm.build_repo_map(tmp_path)
    files = [str(path) for path in repo["files"]]
    imports = {entry["file"]: entry["imports"] for entry in repo["imports"]}

    if failure == "unreadable":
        original = Path.read_text
        original_bytes = Path.read_bytes
        denied = []

        def read_text(path, *args, **kwargs):
            if str(path) == candidate:
                denied.append(str(path))
                raise PermissionError(errno.EACCES, "controlled owned leaf read", candidate)
            return original(path, *args, **kwargs)

        def read_bytes(path):
            if str(path) == candidate:
                raise PermissionError(errno.EACCES, "controlled owned leaf read", candidate)
            return original_bytes(path)

        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        rm._read_source_cached_bounded.cache_clear()
        rm._read_source_text_cached_bounded.cache_clear()
        assert rm._imports_with_lines_for_path(Path(candidate)) == []
        assert denied, "owned leaf read failure was not exercised"
    elif failure == "over-cap":
        Path(candidate).write_text(
            "from pkg.a import unique_target\n#" + "x" * 100, encoding="utf-8"
        )
        monkeypatch.setattr(rm, "_max_parse_bytes", lambda: 64)
        assert Path(candidate).stat().st_size > rm._max_parse_bytes()
        assert rm._imports_with_lines_for_path(Path(candidate)) == []
    elif failure == "malformed":
        Path(candidate).write_text(
            "from pkg.a import unique_target\ndef broken(:\n", encoding="utf-8"
        )
        with pytest.raises(SyntaxError):
            ast.parse(Path(candidate).read_text(encoding="utf-8"))
        assert rm._imports_with_lines_for_path(Path(candidate)) == []
    else:
        Path(candidate).write_text("name = 'pkg.a'\n__import__(name)\n", encoding="utf-8")
        raw = rm._imports_with_lines_for_path(Path(candidate))
        assert any(entry.get("dynamic_unresolved") is True for entry in raw)

    assert rm._confirm_import_edges(Path(candidate), target, tmp_path) == []
    distances = rm._reverse_import_distances([target], files, imports)
    payload = rm.build_context_pack_from_map(repo, "unique_target")
    row = next(row for row in payload["file_matches"] if row["path"] == candidate)
    assert candidate not in distances, "unknown cached import evidence became confirmed distance"
    assert "heuristic-import" in row["reasons"]
    assert "heuristic" in row["provenance"]
    assert payload["coverage_summary"]["graph_completeness"] == "partial"


@pytest.mark.requires_grammar
@pytest.mark.parametrize("language", ["java", "go"])
def test_confirmer_unsupported_language_retains_only_heuristic_consumer(tmp_path, language):
    if language == "java":
        target_source = (
            "package pkg; public class a { public static int unique_target() { return 1; } }"
        )
        candidate_source = "import pkg.a; class Candidate {}"
        symbol = "unique_target"
    else:
        target_source = "package pkg\nfunc UniqueTarget() int { return 1 }\n"
        candidate_source = 'package consumer\nimport "pkg/a"\n'
        symbol = "UniqueTarget"
    target = _write(tmp_path / f"pkg/a.{language}", target_source)
    candidate = _write(tmp_path / f"candidate.{language}", candidate_source)
    raw = rm._imports_with_lines_for_path(Path(candidate))
    assert raw, "actual unsupported-language extractor positive control failed"
    assert rm._confirm_import_edges(Path(candidate), target, tmp_path) == []
    repo = rm.build_repo_map(tmp_path)
    files = [str(path) for path in repo["files"]]
    imports = {entry["file"]: entry["imports"] for entry in repo["imports"]}
    distances = rm._reverse_import_distances([target], files, imports)
    payload = rm.build_context_pack_from_map(repo, symbol)
    row = next(row for row in payload["file_matches"] if row["path"] == candidate)
    assert candidate not in distances, "unsupported confirmer evidence became confirmed distance"
    assert "heuristic-import" in row["reasons"]
    assert "heuristic" in row["provenance"]
    assert payload["coverage_summary"]["graph_completeness"] == "partial"


def test_complete_supported_parsed_nonmatch_does_not_get_heuristic_fallback(tmp_path):
    target = _write(tmp_path / "pkg/cli.py", "def unique_target():\n    return 1\n")
    candidate = _write(tmp_path / "candidate.py", "import click\n")
    raw = rm._imports_with_lines_for_path(Path(candidate))
    assert [entry["module"] for entry in raw] == ["click"]
    assert rm._confirm_import_edges(Path(candidate), target, tmp_path) == []
    repo = rm.build_repo_map(tmp_path)
    imports = {entry["file"]: entry["imports"] for entry in repo["imports"]}
    distances = rm._reverse_import_distances(
        [target], [str(path) for path in repo["files"]], imports
    )
    assert candidate not in distances, "complete supported nonedge got a substring fallback"
