"""Built-in type extensions retain their declared case sensitivity."""

from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


@pytest.mark.parametrize("field, expected", [("file_type", "lower.py"), ("type_not", "upper.PY")])
def test_python_type_keeps_extension_case(tmp_path, field, expected):
    for filename in ("lower.py", "upper.PY"):
        (tmp_path / filename).write_text("needle\n", encoding="utf-8")
    scanner = DirectoryScanner(SearchConfig(**{field: ["python"]}))
    assert [Path(path).name for path in scanner.walk(str(tmp_path))] == [expected]
