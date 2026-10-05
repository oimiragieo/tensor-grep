"""Wave-2a G1.4: one source of truth for the TypeScript / JS-TS suffix sets.

An AST walk over ``src/tensor_grep`` finds every set / list / tuple / dict-key literal that holds
BOTH ``".ts"`` and ``".tsx"``. After the fix the only such literals are ``lang_suffixes.py``'s two
definitions plus two allowlisted suffix->label dicts (identified by assignment name), so a new
literal copy of the suffix set (the drift that left ``.mts``/``.cts`` unregistered) fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

# (module path relative to src/tensor_grep, assignment name) -> required extra keys
ALLOWLISTED_DICTS = {
    ("cli/inventory.py", "_LANGUAGE_BY_SUFFIX"),
    ("cli/sql_query.py", "lang_map"),
}
SHARED_MODULE = "core/lang_suffixes.py"


def _literal_strings(node: ast.AST) -> set[str] | None:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elements = node.elts
    elif isinstance(node, ast.Dict):
        elements = [key for key in node.keys if key is not None]
    else:
        return None
    return {
        element.value
        for element in elements
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _assigned_name(parents: list[ast.AST]) -> str | None:
    for parent in reversed(parents):
        if isinstance(parent, ast.Assign) and isinstance(parent.targets[0], ast.Name):
            return parent.targets[0].id
        if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            return parent.target.id
    return None


def census(root: Path) -> list[tuple[str, int, str | None, set[str]]]:
    """Every ts+tsx literal under ``root`` as (relative path, line, assignment name, strings)."""
    found: list[tuple[str, int, str | None, set[str]]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        rel = path.relative_to(root).as_posix()

        def visit(node: ast.AST, parents: list[ast.AST], rel: str = rel) -> None:
            strings = _literal_strings(node)
            if strings is not None and {".ts", ".tsx"} <= strings:
                found.append((rel, getattr(node, "lineno", 0), _assigned_name(parents), strings))
            for child in ast.iter_child_nodes(node):
                visit(child, [*parents, node])

        visit(tree, [])
    return found


def _violations(found: list[tuple[str, int, str | None, set[str]]]) -> list[str]:
    problems: list[str] = []
    for rel, line, name, strings in found:
        if rel == SHARED_MODULE:
            continue
        if (rel, name) in ALLOWLISTED_DICTS:
            if not {".mts", ".cts"} <= strings:
                problems.append(f"{rel}:{line} {name} lacks .mts/.cts")
            continue
        problems.append(f"{rel}:{line} {name} is a literal copy of the TS suffix set")
    return problems


def test_ts_suffix_literals_live_only_in_the_shared_module_and_allowlisted_dicts() -> None:
    assert _violations(census(SRC)) == []


def test_shared_module_defines_the_two_sets_with_mts_cts() -> None:
    from tensor_grep.cli import lang_suffixes

    assert {".mts", ".cts"} <= lang_suffixes.TS_SUFFIXES
    assert lang_suffixes.TS_SUFFIXES <= lang_suffixes.JS_TS_SUFFIXES
    assert {".mjs", ".cjs", ".js", ".jsx"} <= lang_suffixes.JS_TS_SUFFIXES


def test_census_negative_control_flags_a_new_literal_copy(tmp_path: Path) -> None:
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "rogue.py").write_text('X = {".ts", ".tsx"}\n', encoding="utf-8")
    assert _violations(census(tmp_path)) != []


def test_census_negative_control_flags_an_allowlisted_dict_missing_mts(tmp_path: Path) -> None:
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "inventory.py").write_text(
        '_LANGUAGE_BY_SUFFIX = {".ts": "typescript", ".tsx": "typescript"}\n', encoding="utf-8"
    )
    assert any("lacks .mts/.cts" in problem for problem in _violations(census(tmp_path)))
