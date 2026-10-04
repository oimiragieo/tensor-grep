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

    tree = ast.parse(Path(SRC, "tensor_grep", "cli", "lang_suffixes.py").read_text("utf-8"))
    imported = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and "tensor_grep" in ast.unparse(node)
    ]
    assert imported == []
