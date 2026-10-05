"""Census: smart-case is decided in ONE place (`core.smart_case`), never by `str.islower()`.

`pattern.islower()` is not ripgrep's rule (False for a titlecase letter, False for a pattern with
no cased characters, blind to regex escapes/classes/verbose comments), and it already drifted
across ~17 sites. The check is on the AST: a docstring or comment mentioning `islower` cannot
satisfy or trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

# Unrelated `.islower` uses, keyed by (path relative to src/tensor_grep, enclosing function).
# Each needs a reason. Empty today: there is no legitimate use in the package.
_ALLOWED: dict[tuple[str, str], str] = {}


def _islower_references(source: str) -> list[tuple[int, str]]:
    """(line, enclosing function) of every attribute access named `islower`."""
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_scope = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                else scope
            )
            if isinstance(child, ast.Attribute) and child.attr == "islower":
                found.append((child.lineno, scope))
            visit(child, child_scope)

    visit(tree, "<module>")
    return found


def test_census_detects_islower_on_a_pattern_or_query():
    # Positive control: the census must bite on every spelling of the old rule.
    assert _islower_references("def f(pattern):\n    return pattern.islower()\n") == [(2, "f")]
    assert _islower_references("def g(q):\n    return str.islower(q.query)\n") == [(2, "g")]
    assert _islower_references("def h(xs):\n    return list(map(str.islower, xs))\n") == [(2, "h")]
    # ...and must NOT trip on a docstring/comment mention.
    assert _islower_references('def k():\n    """islower() is wrong"""\n    # x.islower()\n') == []


def test_no_islower_call_on_a_pattern_or_query_anywhere_in_src():
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        for line, scope in _islower_references(path.read_text(encoding="utf-8")):
            if (relative, scope) not in _ALLOWED:
                offenders.append(f"{relative}:{line} in {scope}")
    assert not offenders, (
        "str.islower() is not ripgrep's smart-case rule; use "
        "tensor_grep.core.smart_case.effective_ignore_case / smart_case_ignores_case. "
        f"Offenders: {offenders}"
    )


def test_every_allowlist_entry_still_matches_a_real_use():
    for (relative, scope), reason in _ALLOWED.items():
        assert reason.strip(), f"allowlist entry {relative}:{scope} needs a reason"
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert any(s == scope for _line, s in _islower_references(source)), (
            f"stale allowlist entry {relative}:{scope}"
        )
