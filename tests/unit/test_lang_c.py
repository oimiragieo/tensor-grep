"""C symbol graph (lang_c.py) tests -- foundational-tier expansion, top-10 language campaign
(Phase 1 of C/C++; C++ is a separate follow-up, not covered here).

Foundational scope (mirrors PATH A Stage 1's lang_go.py / lang_php.py / lang_csharp.py
precedent): C gets its own ``LanguageSpec`` entry + dedicated module providing
``defs``/``source``/``imports``/``agent`` support (function definitions/prototypes,
struct/union/enum definitions, typedefs, plus ``#include`` directive extraction). The
cross-file caller-graph (``references_and_calls``/``file_imports_symbol_from_definition``/
``import_update_target``) is explicitly DEFERRED to a follow-up, exactly like Go/PHP/C#'s own
``import_update_target=None`` gap -- `tg refs`/`tg callers`/`tg blast-radius` on a C symbol fall
through to the generic ``_regex_references_and_calls`` text-heuristic path (never a crash, never
a fabricated AST-verified match).

Covered here:
- ``defs``: function definitions/prototypes resolve with kind "function" (both a prototype and
  its later definition resolve as SEPARATE records, no dedup -- mirrors C#'s own
  interface-method/impl-method shared-name precedent); struct/union/enum definitions resolve
  with kind "class"; typedefs resolve with kind "type"; a body-less forward declaration
  (``struct Foo;``) is NOT emitted; a plain/extern variable declaration is NOT emitted.
- ``source``: full source text for a function definition.
- ``imports``: ``#include`` directive targets (angle/quoted forms), extracted by
  ``c_imports_and_symbols`` and surfaced through ``build_repo_map``.
- Grammar-absent (monkeypatched ``lang_c._c_parser`` -> ``None``): fail-closed, zero fabricated
  rows, an honest ``resolution_gaps`` entry, an honest non-zero/non-crash CLI exit code --
  mirrors Go/PHP/C#'s Stage 1 fail-closed contract exactly (``provenance_when_missing ==
  "grammar-missing"``, never "regex-heuristic").
- The agent capsule reports ``primary_target_language == "c"``.
- A pathologically deep AST does not raise ``RecursionError`` (F26-class regression guard,
  applied preemptively since ``lang_go.py`` already paid for this lesson once).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tensor_grep.cli import agent_capsule, lang_c, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: includes (angle + quoted) + a struct/typedef sharing one name (dual-namespace case)
# + a standalone enum/union/struct + a function with BOTH a prototype and a definition (sharing
# a name) + a definition-only function.
# ---------------------------------------------------------------------------

_WIDGET_C_SOURCE = (
    "#include <stdio.h>\n"
    "#include <stdlib.h>\n"
    '#include "widget_types.h"\n'
    "\n"
    "typedef struct Point {\n"
    "    int x;\n"
    "    int y;\n"
    "} Point;\n"
    "\n"
    "enum WidgetKind {\n"
    "    SMALL,\n"
    "    LARGE,\n"
    "};\n"
    "\n"
    "union WidgetValue {\n"
    "    int i;\n"
    "    float f;\n"
    "};\n"
    "\n"
    "struct WidgetStruct {\n"
    "    int x;\n"
    "};\n"
    "\n"
    "int widget_create(int value);\n"
    "\n"
    "int widget_create(int value) {\n"
    "    return value;\n"
    "}\n"
    "\n"
    "static int widget_get_value(int value) {\n"
    "    return value * 2;\n"
    "}\n"
)


def _write_c_fixture(root: Path) -> Path:
    widget_c = root / "widget.c"
    widget_c.write_text(_WIDGET_C_SOURCE, encoding="utf-8")
    return widget_c


# ---------------------------------------------------------------------------
# Registration + provenance
# ---------------------------------------------------------------------------


def test_c_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["c"]
    assert spec.suffixes == frozenset({".c"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (Stage 1 trap, mirrors Go/PHP/C#): never "regex-heuristic" -- C has no fallback.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None


def test_target_language_for_path_reports_c() -> None:
    assert repo_map._target_language_for_path("widget.c") == "c"
    assert repo_map._language_for_path("widget.c") == "c"
    assert repo_map._provider_language_for_path("widget.c") == "c"


def test_header_suffix_is_claimed_by_cpp_not_c() -> None:
    """`.h` is claimed by lang_cpp.py (tree-sitter-cpp is a strict grammar superset of C), NOT by
    this module -- this module must NOT claim it, or `_target_language_for_path` would disagree
    with `_provider_language_for_path`'s pre-existing "cpp" assignment for header suffixes. Was
    "deliberately unregistered" before lang_cpp.py existed (Phase 1 of the top-10 language
    campaign, #731); now registered under "cpp" (Phase 2, this module's own sibling)."""
    spec = lang_registry.spec_for_path("widget.h")
    assert spec is not None
    assert spec.language_id == "cpp"
    assert repo_map._provider_language_for_path("widget.h") == "cpp"
    assert repo_map._target_language_for_path("widget.h") == "cpp"


# ---------------------------------------------------------------------------
# defs
# ---------------------------------------------------------------------------


def test_defs_finds_enum_union_struct_as_class_kind(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    for name in ("WidgetKind", "WidgetValue", "WidgetStruct"):
        payload = repo_map.build_symbol_defs(name, tmp_path)
        assert not payload.get("no_match"), f"expected a definition for {name}"
        assert payload["definitions"][0]["kind"] == "class", f"{name} should be kind=class"
        assert payload["definitions"][0]["provenance"] == "tree-sitter"


def test_defs_finds_struct_tag_and_typedef_alias_sharing_a_name(tmp_path: Path) -> None:
    """`typedef struct Point {...} Point;` -- the struct TAG and the typedef ALIAS occupy
    different C namespaces even though they share text; both are legitimate, separate defs."""
    _write_c_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Point", tmp_path)

    assert not payload.get("no_match")
    kinds = {d["kind"] for d in payload["definitions"]}
    assert "class" in kinds
    assert "type" in kinds


def test_defs_finds_prototype_and_definition_as_two_function_records(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("widget_create", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 2
    assert all(d["kind"] == "function" for d in payload["definitions"])
    lines = sorted(d["start_line"] for d in payload["definitions"])
    assert lines[0] != lines[1]


def test_defs_finds_definition_only_function(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("widget_get_value", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["kind"] == "function"


def test_defs_excludes_body_less_forward_declaration(tmp_path: Path) -> None:
    root = tmp_path
    (root / "fwd.c").write_text(
        "struct ForwardOnly;\n\nvoid use_it(struct ForwardOnly *p) {\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_defs("ForwardOnly", root)

    assert payload.get("no_match") is True


def test_defs_excludes_plain_and_extern_variable_declarations(tmp_path: Path) -> None:
    root = tmp_path
    (root / "globals.c").write_text(
        "int global_counter = 0;\nextern int shared_flag;\n\nvoid touch(void) {\n}\n",
        encoding="utf-8",
    )

    counter_payload = repo_map.build_symbol_defs("global_counter", root)
    flag_payload = repo_map.build_symbol_defs("shared_flag", root)

    assert counter_payload.get("no_match") is True
    assert flag_payload.get("no_match") is True


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


def test_source_returns_full_function_body(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    payload = repo_map.build_symbol_source("widget_get_value", tmp_path)

    assert not payload.get("no_match")
    assert payload["sources"], "expected at least one source block for widget_get_value"
    source_text = payload["sources"][0]["source"]
    assert "static int widget_get_value(int value)" in source_text
    assert "return value * 2;" in source_text


def test_source_for_prototype_plus_definition_returns_both_blocks(tmp_path: Path) -> None:
    c_file = _write_c_fixture(tmp_path)

    sources = lang_c.c_parser_symbol_sources(c_file, "widget_create")

    assert len(sources) == 2
    assert any(s["source"].strip() == "int widget_create(int value);" for s in sources)
    assert any("return value;" in s["source"] for s in sources)


# ---------------------------------------------------------------------------
# imports: angle / quoted #include forms
# ---------------------------------------------------------------------------


def test_c_imports_and_symbols_extracts_include_targets(tmp_path: Path) -> None:
    source = '#include <stdio.h>\n#include "local.h"\n\nint main(void) {\n    return 0;\n}\n'
    c_file = tmp_path / "main.c"
    c_file.write_text(source, encoding="utf-8")

    imports, symbols = lang_c.c_imports_and_symbols(c_file)

    # Quote/bracket delimiters are stripped -- the recorded module string is the bare target.
    assert imports == sorted({"stdio.h", "local.h"})
    assert any(s["name"] == "main" and s["kind"] == "function" for s in symbols)


def test_build_repo_map_surfaces_c_imports_and_symbols(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(tmp_path)

    file_imports = [
        entry for entry in repo_map_payload["imports"] if entry["file"].endswith("widget.c")
    ]
    assert file_imports, "expected an imports entry for widget.c"
    assert "stdio.h" in file_imports[0]["imports"]
    assert "stdlib.h" in file_imports[0]["imports"]
    assert "widget_types.h" in file_imports[0]["imports"]

    symbol_names = {
        s["name"] for s in repo_map_payload["symbols"] if s["file"].endswith("widget.c")
    }
    assert {
        "Point",
        "WidgetKind",
        "WidgetValue",
        "WidgetStruct",
        "widget_create",
        "widget_get_value",
    }.issubset(symbol_names)


# ---------------------------------------------------------------------------
# #74-follow-up: tg imports (c_imports_with_lines / build_file_imports) -- foundational tier,
# mirrors test_lang_csharp.py's test_file_imports_returns_csharp_using_directives_with_lines.
# ---------------------------------------------------------------------------


def test_c_imports_with_lines_extracts_includes_with_lines(tmp_path: Path) -> None:
    c_file = _write_c_fixture(tmp_path)

    entries = lang_c.c_imports_with_lines(c_file)

    modules = {entry["module"]: entry["line"] for entry in entries}
    assert modules == {
        "stdio.h": 1,
        "stdlib.h": 2,
        "widget_types.h": 3,
    }


def test_c_imports_with_lines_non_c_suffix_returns_empty(tmp_path: Path) -> None:
    not_c = tmp_path / "widget.txt"
    not_c.write_text("#include <stdio.h>\n", encoding="utf-8")

    assert lang_c.c_imports_with_lines(not_c) == []


def test_c_imports_with_lines_grammar_absent_returns_empty(tmp_path: Path, monkeypatch) -> None:
    c_file = _write_c_fixture(tmp_path)
    monkeypatch.setattr(lang_c, "_c_parser", lambda: None)

    assert lang_c.c_imports_with_lines(c_file) == []


def test_file_imports_returns_c_include_directives_with_lines(tmp_path: Path) -> None:
    c_file = _write_c_fixture(tmp_path)

    payload = repo_map.build_file_imports(c_file)

    assert payload["result_incomplete"] is False
    modules = {entry["module"]: entry["line"] for entry in payload["imports"]}
    assert modules == {
        "stdio.h": 1,
        "stdlib.h": 2,
        "widget_types.h": 3,
    }
    # Foundational tier: raw #include directives are real, but resolving them to a specific file
    # is deferred (C has no standardized manifest to resolve against) -- every row must be
    # unresolved and never presumed external, matching the fail-closed contract.
    assert all(entry["resolved"] is None for entry in payload["imports"])
    assert all(entry["external"] is False for entry in payload["imports"])


def test_c_include_target_text_handles_macro_and_call_forms(tmp_path: Path) -> None:
    """`#include MACRO_HEADER` (macro-expanded) and `#include COMBINE(a, b)` (macro-combined)
    both parse as real preproc_include nodes (tree-sitter-c never runs a preprocessor) -- the
    extractor records the raw macro/call text honestly rather than dropping the row."""
    source = "#include MACRO_HEADER\n#include COMBINE(a, b)\n\nvoid noop(void) {\n}\n"
    c_file = tmp_path / "macro_includes.c"
    c_file.write_text(source, encoding="utf-8")

    entries = lang_c.c_imports_with_lines(c_file)

    modules = {entry["module"] for entry in entries}
    assert "MACRO_HEADER" in modules
    assert any("COMBINE" in module for module in modules)


# ---------------------------------------------------------------------------
# Deferred caller-graph, grammar PRESENT: honest resolution_gaps, not a silent proven-zero.
# ---------------------------------------------------------------------------


def test_refs_grammar_present_still_reports_import_resolution_gap(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("widget_create", tmp_path)

    assert not payload.get("no_match")
    gaps = payload["resolution_gaps"]
    c_gaps = [gap for gap in gaps if gap["language"] == "c"]
    assert len(c_gaps) == 1
    # NOT "fail-closed" (that's the grammar-ABSENT case, covered separately below) -- this is
    # the narrower "grammar works fine, but no reverse-import resolver exists yet" gap.
    assert "fail-closed" not in c_gaps[0]["reason"]
    assert "reverse-import" in c_gaps[0]["reason"]
    assert c_gaps[0]["files_affected"] >= 1
    # Honesty floor: the remediation must tell an agent to treat a zero count as UNKNOWN, not
    # proven-zero -- the exact failure mode this test guards against.
    assert "not proven-zero" in c_gaps[0]["remediation"] or "UNKNOWN" in (c_gaps[0]["remediation"])


# ---------------------------------------------------------------------------
# Grammar-absent: fail-closed, resolution_gaps, honest exit code.
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_resolution_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_c_fixture(tmp_path)
    # A python symbol elsewhere in the same repo so refs has something REAL to find -- the
    # resolution_gaps floor is about a C file being an honestly-labeled BYSTANDER in the scan
    # universe, not about the query's own target living in the grammar-missing language.
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(lang_c, "_c_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("widget_create", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    defs_gaps = defs_payload["resolution_gaps"]
    assert any(gap["language"] == "c" for gap in defs_gaps)
    c_gap = next(gap for gap in defs_gaps if gap["language"] == "c")
    assert "fail-closed" in c_gap["reason"]

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    assert not refs_payload.get("no_match")
    gaps = refs_payload["resolution_gaps"]
    assert any(gap["language"] == "c" for gap in gaps)
    c_refs_gap = next(gap for gap in gaps if gap["language"] == "c")
    assert "fail-closed" in c_refs_gap["reason"]
    assert c_refs_gap["files_affected"] >= 1
    assert "fall back to plain literal-text/regex matching" not in c_refs_gap["remediation"]


def test_grammar_absent_cli_exit_code_is_honest_not_found(tmp_path: Path, monkeypatch) -> None:
    """A C-only target with the grammar missing must exit 1 (honest not-found) -- never a
    silent 0 and never a crash."""
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    _write_c_fixture(tmp_path)
    monkeypatch.setattr(lang_c, "_c_parser", lambda: None)

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "widget_create"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Agent capsule
# ---------------------------------------------------------------------------


def test_agent_capsule_reports_c_target_language(tmp_path: Path) -> None:
    _write_c_fixture(tmp_path)

    payload = agent_capsule.build_agent_capsule("widget_create", tmp_path)

    assert payload["context_consistency"]["primary_target_language"] == "c"


# ---------------------------------------------------------------------------
# Deep-AST guard: explicit-stack DFS must not raise RecursionError (lang_go.py F26 precedent).
# ---------------------------------------------------------------------------


def _deep_nested_c_source(depth: int) -> str:
    return "int target(void)\n{\n    return " + ("(" * depth) + "1" + (")" * depth) + ";\n}\n"


def test_c_walkers_survive_pathologically_deep_ast_without_recursion_error(
    tmp_path: Path,
) -> None:
    depth = sys.getrecursionlimit() + 500
    deep_c = tmp_path / "deep.c"
    deep_c.write_text(_deep_nested_c_source(depth), encoding="utf-8")

    imports, symbols = lang_c.c_imports_and_symbols(deep_c)
    assert imports == []
    assert any(s["name"] == "target" and s["kind"] == "function" for s in symbols)

    sources = lang_c.c_parser_symbol_sources(deep_c, "target")
    assert len(sources) == 1
    assert sources[0]["kind"] == "function"


# ---------------------------------------------------------------------------
# Grammar-missing import failure (package not installed) -- distinct from monkeypatched None.
# ---------------------------------------------------------------------------


def test_c_parser_returns_none_when_grammar_module_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "tree_sitter_c":
            raise ImportError("simulated missing grammar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    lang_c._c_parser.cache_clear()
    try:
        assert lang_c._c_parser() is None
    finally:
        lang_c._c_parser.cache_clear()


# ---------------------------------------------------------------------------
# Read/parse-error guards return ([], []) rather than raising.
# ---------------------------------------------------------------------------


def test_c_imports_and_symbols_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "DoesNotExist.c"
    imports, symbols = lang_c.c_imports_and_symbols(missing)
    assert imports == []
    assert symbols == []


def test_c_imports_and_symbols_non_c_suffix_returns_empty(tmp_path: Path) -> None:
    other = tmp_path / "widget.txt"
    other.write_text("not c", encoding="utf-8")
    imports, symbols = lang_c.c_imports_and_symbols(other)
    assert imports == []
    assert symbols == []
