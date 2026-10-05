"""Guard: a test must compare a cache format to the CONSTANT, never to a bare integer.

K1.5 bumped `_RESULT_CACHE_FORMAT` 2 -> 3 and an unrelated test pinned `payload["format"] == 2`, so
three CI lanes went red for a reason nobody had grepped for. The check is an AST walk over `tests/`:

* a comparison whose one side names a format (`format`, `*_FORMAT*`, `format_version`, a
  `["format"]` / `.get("format")` read) and whose other side is a bare int literal;
* a subscript write (`data["format"] = 2`) or a dict-literal entry (`{"format": 2}`) with an int
  literal.

A module-level or local NAME bound to an int is fine -- that is how a deliberate "older version"
fixture is spelled (`pre_fix_format = 2`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_FORMAT_NAME = re.compile(r"(^|_)format(_version)?$", re.IGNORECASE)
_TESTS = Path(__file__).resolve().parent
# (file name, line-insensitive reason) -- none today.
_ALLOWED: dict[str, str] = {}


def _mentions_format(node: ast.AST) -> bool:
    called = {id(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)}
    for sub in ast.walk(node):
        if id(sub) in called:  # `formatter.format(x)` is a method call, not a format field
            continue
        if isinstance(sub, ast.Name) and _FORMAT_NAME.search(sub.id):
            return True
        if isinstance(sub, ast.Attribute) and _FORMAT_NAME.search(sub.attr):
            return True
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value.lower() in {"format", "format_version"}
        ):
            return True
    return False


def _is_int_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _is_format_key(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower() in {"format", "format_version"}
    )


def hardcoded_format_literals(source: str) -> list[int]:
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(_is_int_literal(s) for s in sides) and any(
                _mentions_format(s) for s in sides if not _is_int_literal(s)
            ):
                found.append(node.lineno)
        elif isinstance(node, ast.Assign) and _is_int_literal(node.value):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_format_key(target.slice):
                    found.append(node.lineno)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if _is_format_key(key) and _is_int_literal(value):
                    found.append(key.lineno if key is not None else node.lineno)
    return found


def test_guard_bites_on_every_spelling_and_spares_named_constants():
    # positive controls
    assert hardcoded_format_literals('assert payload.get("format") == 2\n') == [1]
    assert hardcoded_format_literals('assert payload["format"] == 3\n') == [1]
    assert hardcoded_format_literals("assert _RESULT_CACHE_FORMAT == 3\n") == [1]
    assert hardcoded_format_literals("assert 2 == rewritten['format_version']\n") == [1]
    assert hardcoded_format_literals('data["format"] = 2\n') == [1]
    assert hardcoded_format_literals('x = {"format": 2}\n') == [1]
    # negative controls: the constant, a named older version, and unrelated integers
    assert hardcoded_format_literals('assert payload.get("format") == _RESULT_CACHE_FORMAT\n') == []
    assert hardcoded_format_literals("old = 2\nassert _RESULT_CACHE_FORMAT != old\n") == []
    assert hardcoded_format_literals('data["format"] = old\n') == []
    assert hardcoded_format_literals("assert len(rows) == 2\n") == []


def test_no_test_compares_a_cache_format_to_a_bare_integer():
    offenders: list[str] = []
    for path in sorted(_TESTS.rglob("test_*.py")):
        if path.name in _ALLOWED or path.name == Path(__file__).name:
            continue
        for line in hardcoded_format_literals(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "compare cache formats to the constant (e.g. _RESULT_CACHE_FORMAT), not a bare integer; "
        f"offenders: {offenders}"
    )
