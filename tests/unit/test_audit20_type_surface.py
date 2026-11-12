"""The Python scanner honors representative advertised built-in file types."""

from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


@pytest.mark.parametrize(
    "type_name, filename",
    [("go", "app.go"), ("json", "events.jsonl"), ("markdown", "readme.markdown")],
)
def test_advertised_types_select_their_files(tmp_path, type_name, filename):
    (tmp_path / filename).write_text("needle\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("needle\n", encoding="utf-8")
    scanner = DirectoryScanner(SearchConfig(file_type=[type_name]))
    assert [Path(path).name for path in scanner.walk(str(tmp_path))] == [filename]
