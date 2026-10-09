"""Wave-2a G1.2: every direct consumer of the per-file extractors has a recorded disposition.

``_imports_and_symbols_for_path`` / ``_imports_with_lines_for_path`` / a registry spec's
``extract_imports_and_symbols`` return ``[]`` for a file they could not parse, which is
indistinguishable from "this file has nothing". A consumer that presents that as a complete answer
silently drops the file, so each call site must either consult the coverage-gap builders or be
recorded here with the reason it cannot drop a file. A NEW call site fails this test until it is
added with a disposition (the same AST walk the repo's other census tests use).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

EXTRACTORS = {
    "_imports_and_symbols_for_path",
    "_imports_with_lines_for_path",
    "extract_imports_and_symbols",
}

# (relative path, enclosing function) -> disposition
DISPOSITIONS: dict[tuple[str, str], str] = {
    # build_repo_map and friends: the universe gaps are computed from the same files
    # (_language_coverage_gaps_for_universe -> source_coverage_gaps).
    ("cli/repo_map.py", "_imports_and_symbols_for_path"): "wired: universe gaps (same files)",
    ("cli/repo_map.py", "build_repo_map"): "wired: universe gaps (same files)",
    ("cli/repo_map.py", "build_file_imports"): "wired: attach_target_gaps",
    ("cli/repo_map.py", "_confirm_import_edges"): "wired: importer universe gaps",
    ("cli/diff_impact.py", "_extract_symbols"): "wired: target_file_gaps -> unparsed_changed_files",
    ("cli/agent_capsule.py", "_collect_outbound_dependencies"): "opt-in DAR only; primary file "
    "gap is attached in agent_capsule_builder (attach_target_gaps)",
    ("cli/ast_enrichment.py", "enrich_match_with_container"): "wired: enclosing_symbol_status",
    # tg sql imports passes the extractor as a keyword argument (not a call here); its
    # _run_imports_pass is wired to python_syntax_error_gap (test_sql_imports_table.py).
    ("cli/symbol_suggestions.py", "suggestions_for_payload"): "EXEMPT: proposes names after a miss "
    "that already carries the coverage disclosure",
}


def _enclosing_function(parents: list[ast.AST]) -> str:
    for parent in reversed(parents):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
    return "<module>"


def census(root: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        rel = path.relative_to(root).as_posix()

        def visit(node: ast.AST, parents: list[ast.AST], rel: str = rel) -> None:
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name == "partial" and node.args:
                    callback = node.args[0]
                    name = (
                        callback.id
                        if isinstance(callback, ast.Name)
                        else callback.attr
                        if isinstance(callback, ast.Attribute)
                        else None
                    )
                if name in EXTRACTORS:
                    found.add((rel, _enclosing_function(parents)))
            for child in ast.iter_child_nodes(node):
                visit(child, [*parents, node])

        visit(tree, [])
    return found


def test_every_extractor_consumer_has_a_recorded_disposition() -> None:
    found = census(SRC)
    # per-language extractor modules call their own helpers; only cross-module consumers matter
    consumers = {
        key
        for key in found
        if not key[0].startswith(("cli/lang_", "cli/repo_map_lang_", "cli/repo_map_regex"))
    }
    assert consumers - set(DISPOSITIONS) == set(), (
        "NEW direct extractor consumer(s): wire to repo_map_coverage_gaps or record a disposition"
    )
    assert set(DISPOSITIONS) - consumers == set(), "stale disposition: the call site is gone"


def test_census_negative_control_flags_a_new_consumer(tmp_path: Path) -> None:
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "rogue.py").write_text(
        "def f(p):\n    return _imports_and_symbols_for_path(p)\n", encoding="utf-8"
    )
    assert census(tmp_path) == {("cli/rogue.py", "f")}


def test_census_negative_control_flags_a_partial_extractor_callback(tmp_path: Path) -> None:
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "rogue.py").write_text(
        "def f(p):\n    return generation.product(p, partial(registry.extract_imports_and_symbols, p))\n",
        encoding="utf-8",
    )
    assert census(tmp_path) == {("cli/rogue.py", "f")}
