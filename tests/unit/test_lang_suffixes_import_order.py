"""Wave-2a G1.4: ``lang_suffixes`` is import-order independent (no repo_map <-> scope-gap cycle)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")


@pytest.mark.parametrize(
    "statements",
    [
        "import tensor_grep.cli.js_ts_scope_gap; import tensor_grep.cli.repo_map",
        "import tensor_grep.cli.repo_map; import tensor_grep.cli.js_ts_scope_gap",
        "import tensor_grep.cli.lang_suffixes",
    ],
)
def test_import_order_does_not_matter(statements: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {SRC!r}); {statements}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]


def test_lang_suffixes_imports_nothing_from_tensor_grep() -> None:
    import ast

    # The definition lives in the layer-neutral core module and imports nothing; the cli module is
    # a re-export whose ONLY tensor_grep import is that core module (no cycle is possible).
    def tensor_grep_imports(*parts: str) -> list[str]:
        tree = ast.parse(Path(SRC, "tensor_grep", *parts).read_text("utf-8"))
        return [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and "tensor_grep" in ast.unparse(node)
        ]

    assert tensor_grep_imports("core", "lang_suffixes.py") == []
    assert tensor_grep_imports("cli", "lang_suffixes.py") == [
        "from tensor_grep.core.lang_suffixes import JS_TS_SUFFIXES, TS_SUFFIXES"
    ]


def test_test_paths_js_like_suffixes_are_the_shared_set() -> None:
    from tensor_grep.cli import lang_suffixes, repo_map_test_paths

    assert repo_map_test_paths._JS_LIKE_SUFFIXES == lang_suffixes.JS_TS_SUFFIXES


def test_test_paths_first_import_order_exits_zero() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {SRC!r}); "
            "import tensor_grep.cli.repo_map_test_paths; import tensor_grep.cli.repo_map",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
