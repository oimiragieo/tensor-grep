"""Focused regression coverage for the disjoint AD3A recovery wave."""

from pathlib import Path

from tensor_grep.io.directory_scanner import DirectoryScanner


def test_tg037_gitignore_directory_does_not_abort_scan(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").mkdir()
    source = tmp_path / "keep.py"
    source.write_text("needle\n", encoding="utf-8")

    scanner = DirectoryScanner()

    assert list(scanner.walk(str(tmp_path))) == [str(source)]
