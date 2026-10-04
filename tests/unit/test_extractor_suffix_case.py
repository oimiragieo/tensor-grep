"""Extractors must agree with the language registry, which lowercases suffixes.

`lang_registry.spec_for_path` maps `app.PY` to the Python spec, so the extractor it dispatches to
must not then refuse the same file because its own guard compared the suffix case-sensitively
(that returned an empty, "successful" extraction for a valid file full of definitions).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tensor_grep.cli.repo_map  # noqa: F401  (importing registers every language spec)
from tensor_grep.cli import lang_registry

SAMPLES: list[tuple[str, str]] = [
    (".py", "def f():\n    return 1\n"),
    (".go", "package main\n\nfunc F() int {\n\treturn 1\n}\n"),
    (".php", "<?php\nfunction f() { return 1; }\n"),
    (".java", "class A {\n  void f() {}\n}\n"),
    (".cs", "class A { void F() {} }\n"),
    (".c", "int f(void) { return 1; }\n"),
    (".cpp", "int f() { return 1; }\n"),
    (".rs", "fn f() -> i32 { 1 }\n"),
    (".js", "function f() { return 1; }\n"),
    (".ts", "function f(): number { return 1; }\n"),
    (".tsx", "function f(): number { return 1; }\n"),
]


def _names(path: Path) -> list[str]:
    spec = lang_registry.spec_for_path(path)
    assert spec is not None, path
    assert spec.extract_imports_and_symbols is not None, path
    _, symbols = spec.extract_imports_and_symbols(path)
    return sorted(str(s["name"]) for s in symbols)


@pytest.mark.parametrize(("suffix", "source"), SAMPLES, ids=[s for s, _ in SAMPLES])
@pytest.mark.parametrize("case", ["upper", "title"])
def test_extractor_agrees_with_registry_on_suffix_case(
    tmp_path: Path, suffix: str, source: str, case: str
) -> None:
    lower = tmp_path / f"sample{suffix}"
    lower.write_text(source, encoding="utf-8")
    expected = _names(lower)
    if not expected:
        pytest.skip(f"no parser/symbols available for {suffix} in this environment")
    cased_suffix = suffix.upper() if case == "upper" else suffix.title()
    cased = tmp_path / f"cased{cased_suffix}"
    cased.write_text(source, encoding="utf-8")
    assert lang_registry.spec_for_path(cased) is lang_registry.spec_for_path(lower)
    assert _names(cased) == expected, f"{cased.name} lost its symbols vs {lower.name}"
