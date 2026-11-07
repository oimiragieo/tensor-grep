"""TG-056 (Rust arm): the Rust tree walkers must not recurse per AST node.

The Java walkers were converted to an explicit stack; the same finding also named three Rust
walkers. AST depth == call depth there, so a few KB of nested parens aborted `build_repo_map`
for the whole repo.
"""

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map_lang_rust as rust


def _deep_rust_file(tmp_path: Path) -> Path:
    depth = 2000
    body = "(" * depth + "1" + ")" * depth
    path = tmp_path / "deep.rs"
    path.write_text(
        f"fn outer() -> i32 {{ let x = {body}; x }}\n",
        encoding="utf-8",
    )
    return path


def test_rust_parser_symbols_survives_deep_nesting(tmp_path: Path) -> None:
    path = _deep_rust_file(tmp_path)
    symbols = rust._rust_parser_symbols(path)
    assert [item["name"] for item in symbols] == ["outer"]


def test_rust_references_and_calls_survives_deep_nesting(tmp_path: Path) -> None:
    path = _deep_rust_file(tmp_path)
    # Must return normally; the pre-fix behaviour was RecursionError.
    rust._rust_references_and_calls(path, "outer", None)


def test_rust_parser_symbol_sources_survives_deep_nesting(tmp_path: Path) -> None:
    path = _deep_rust_file(tmp_path)
    sources = rust._rust_parser_symbol_sources(path, "outer")
    assert sources, "the outer symbol's source must still be located"


def test_deep_nesting_does_not_raise_recursion_error(tmp_path: Path) -> None:
    """Explicit guard so the failure mode is named rather than inferred from a traceback."""
    path = _deep_rust_file(tmp_path)
    try:
        rust._rust_parser_symbols(path)
    except RecursionError:  # pragma: no cover - only on regression
        pytest.fail("walker still recurses per AST node")


def test_real_rust_grammar_parses_depth_2000_without_errors(tmp_path: Path) -> None:
    parser = rust._self._rust_parser()
    assert parser is not None, "the real Rust tree-sitter grammar must be installed"
    tree = parser.parse(_deep_rust_file(tmp_path).read_bytes())
    assert tree.root_node.type == "source_file"
    assert not tree.root_node.has_error
    assert tree.root_node.named_children[0].type == "function_item"


@pytest.mark.parametrize("depth", [2, 2000])
def test_nested_actual_call_preserves_results(tmp_path: Path, depth: int) -> None:
    body = "(" * depth + "target()" + ")" * depth
    outer = f"fn outer() -> i32 {{ {body} }}\n"
    path = tmp_path / "calls.rs"
    path.write_text("fn target() -> i32 { 1 }\n" + outer, encoding="utf-8")
    parser = rust._self._rust_parser()
    assert parser is not None, "the real Rust tree-sitter grammar must be installed"
    assert not parser.parse(path.read_bytes()).root_node.has_error
    assert [item["name"] for item in rust._rust_parser_symbols(path)] == ["target", "outer"]
    references, calls = rust._rust_references_and_calls(path, "target", None)
    assert references
    assert {item["name"] for item in references} == {"target"}
    assert [item["name"] for item in calls] == ["target"]
    sources = rust._rust_parser_symbol_sources(path, "outer")
    assert [item["source"] for item in sources] == [outer]
