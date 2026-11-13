from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("sub/app.py", "sub/drop.py", "note.txt"):
        (tmp_path / name).write_text("owned fixture\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "globs, options, expected",
    [
        pytest.param(["!*.py", "app.py"], {}, True, id="exclude-then-include"),
        pytest.param(["app.py", "!*.py"], {}, False, id="include-then-exclude"),
        pytest.param(["*.py", "!app.py", "app.py"], {}, True, id="alternating-last-include"),
        pytest.param(["!*.py", "app.py", "!app.py"], {}, False, id="alternating-last-exclude"),
        pytest.param(["!*.py", "note.txt"], {}, False, id="nonmatching-include"),
        pytest.param(["!*.txt"], {}, True, id="negative-only-unmatched"),
        pytest.param(["!*.py"], {}, False, id="negative-only-matched"),
        pytest.param(["*.txt"], {}, False, id="include-only-miss"),
        pytest.param(["app.py"], {}, True, id="include-only-hit"),
        pytest.param(None, {}, True, id="no-glob"),
        pytest.param(["!sub/*.py", "sub/app.py"], {}, True, id="relative-path-override"),
        pytest.param(
            ["!SUB/*.PY", "SUB/APP.PY"],
            {"glob_case_insensitive": True},
            True,
            id="case-insensitive-relative-override",
        ),
        pytest.param(["!*.py", "app.py"], {"file_type": ["py"]}, True, id="type-allows"),
        pytest.param(["!*.py", "app.py"], {"file_type": ["txt"]}, False, id="type-refuses"),
        pytest.param(["!*.py", "app.py"], {"type_not": ["py"]}, False, id="type-not-refuses"),
        pytest.param(["app.py"], {"file_type": ["txt"]}, False, id="positive-type-refuses"),
        pytest.param(["app.py"], {"type_not": ["py"]}, False, id="positive-type-not-refuses"),
    ],
)
def test_last_matching_glob_wins_without_bypassing_types(corpus, globs, options, expected):
    relative = Path("sub/app.py")
    scanner = DirectoryScanner(SearchConfig(glob=globs, **options))
    assert scanner._should_include_file(corpus / relative, relative) is expected


@pytest.mark.parametrize(
    "globs, expected",
    [
        pytest.param(["!*.py", "sub/app.py"], {"sub/app.py"}, id="walk-reinclude"),
        pytest.param(["sub/app.py", "!*.py"], set(), id="walk-exclude"),
    ],
)
def test_walk_applies_ordered_globs(corpus, globs, expected):
    scanner = DirectoryScanner(SearchConfig(glob=globs))
    assert {
        Path(path).relative_to(corpus).as_posix() for path in scanner.walk(str(corpus))
    } == expected
