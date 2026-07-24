"""C# symbol graph (lang_csharp.py) tests -- deep symbol-intelligence tier expansion.

Foundational scope (mirrors PATH A Stage 1's lang_go.py precedent, not the pre-registry
Rust/JS/TS inline pattern): C# gets its own ``LanguageSpec`` entry + dedicated module providing
``defs``/``source``/``imports``/``agent`` support (classes/interfaces/structs/enums/records +
methods/constructors, plus ``using`` directive extraction). The cross-file caller-graph
(``references_and_calls``/``file_imports_symbol_from_definition``/``import_update_target``) is
explicitly DEFERRED to a follow-up, exactly like Go's own ``import_update_target=None`` gap --
`tg refs`/`tg callers`/`tg blast-radius` on a C# symbol fall through to the generic
``_regex_references_and_calls`` text-heuristic path (never a crash, never a fabricated
AST-verified match).

Covered here:
- ``defs``: class/interface/struct/enum/record declarations resolve with kind "class"; method/
  constructor declarations resolve with kind "function"; both report provenance "tree-sitter".
- ``source``: full source text for a method definition.
- ``imports``: dotted namespace names from plain/multi-segment/aliased/``static``/``global``
  ``using`` directives, extracted by ``csharp_imports_and_symbols`` and surfaced through
  ``build_repo_map``.
- Grammar-absent (monkeypatched ``lang_csharp._csharp_parser`` -> ``None``): fail-closed, zero
  fabricated rows, an honest ``resolution_gaps`` entry, an honest non-zero/non-crash CLI exit
  code -- mirrors Go's Stage 1 fail-closed contract exactly (``provenance_when_missing ==
  "grammar-missing"``, never "regex-heuristic").
- The agent capsule reports ``primary_target_language == "csharp"``.
- A pathologically deep AST does not raise ``RecursionError`` (F26-class regression guard,
  applied preemptively since ``lang_go.py`` already paid for this lesson once).
"""

from __future__ import annotations

import sys
from pathlib import Path

from tensor_grep.cli import agent_capsule, lang_csharp, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: namespace + using directives (plain/dotted/aliased) + interface + enum + record +
# struct + a class implementing the interface, with a constructor and two methods (one of which
# shares its name with the interface's abstract method -- both must resolve as separate defs).
# ---------------------------------------------------------------------------

_WIDGET_CS_SOURCE = (
    "using System;\n"
    "using System.Collections.Generic;\n"
    "using MyAlias = System.Text.StringBuilder;\n"
    "\n"
    "namespace Widgets.Core\n"
    "{\n"
    "    public interface IWidget\n"
    "    {\n"
    "        int GetValue();\n"
    "    }\n"
    "\n"
    "    public enum WidgetKind\n"
    "    {\n"
    "        Small,\n"
    "        Large,\n"
    "    }\n"
    "\n"
    "    public record WidgetRecord(string Name, int Value);\n"
    "\n"
    "    public struct WidgetStruct\n"
    "    {\n"
    "        public int X;\n"
    "    }\n"
    "\n"
    "    public class Widget : IWidget\n"
    "    {\n"
    "        private readonly string _name;\n"
    "\n"
    "        public Widget(string name)\n"
    "        {\n"
    "            _name = name;\n"
    "        }\n"
    "\n"
    "        public int GetValue()\n"
    "        {\n"
    "            return 42;\n"
    "        }\n"
    "\n"
    "        public static Widget Create(string name)\n"
    "        {\n"
    "            return new Widget(name);\n"
    "        }\n"
    "    }\n"
    "}\n"
)


def _write_csharp_fixture(root: Path) -> Path:
    widget_cs = root / "Widget.cs"
    widget_cs.write_text(_WIDGET_CS_SOURCE, encoding="utf-8")
    return widget_cs


# ---------------------------------------------------------------------------
# Registration + provenance
# ---------------------------------------------------------------------------


def test_csharp_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["csharp"]
    assert spec.suffixes == frozenset({".cs"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (Stage 1 trap, mirrors Go): never "regex-heuristic" -- C# has no fallback.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None


def test_target_language_for_path_reports_csharp() -> None:
    assert repo_map._target_language_for_path("Widget.cs") == "csharp"
    assert repo_map._language_for_path("Widget.cs") == "csharp"
    assert repo_map._provider_language_for_path("Widget.cs") == "csharp"


# ---------------------------------------------------------------------------
# defs
# ---------------------------------------------------------------------------


def test_defs_finds_class_with_tree_sitter_provenance(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    # "IWidget" (unlike "Widget") has no same-named constructor, so this is the clean
    # single-definition case; see test_defs_finds_constructor_and_method_as_function_kind below
    # for the "Widget" class-plus-constructor-share-a-name case.
    payload = repo_map.build_symbol_defs("IWidget", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 1
    definition = payload["definitions"][0]
    assert definition["kind"] == "class"
    assert definition["provenance"] == "tree-sitter"
    assert definition["file"].replace("\\", "/").endswith("Widget.cs")


def test_defs_finds_interface_struct_enum_record_as_class_kind(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    for name in ("IWidget", "WidgetStruct", "WidgetKind", "WidgetRecord"):
        payload = repo_map.build_symbol_defs(name, tmp_path)
        assert not payload.get("no_match"), f"expected a definition for {name}"
        assert payload["definitions"][0]["kind"] == "class", f"{name} should be kind=class"


def test_defs_finds_constructor_and_method_as_function_kind(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    ctor_payload = repo_map.build_symbol_defs("Widget", tmp_path)
    create_payload = repo_map.build_symbol_defs("Create", tmp_path)

    # "Widget" itself resolves to the class declaration (kind=class); the constructor sharing
    # the same name is a distinct node also named "Widget" -- both are legitimate definitions.
    kinds = {d["kind"] for d in ctor_payload["definitions"]}
    assert "class" in kinds
    assert "function" in kinds

    assert not create_payload.get("no_match")
    assert all(d["kind"] == "function" for d in create_payload["definitions"])


def test_defs_finds_both_interface_and_impl_methods_sharing_a_name(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("GetValue", tmp_path)

    assert not payload.get("no_match")
    # One method_declaration in IWidget (no body), one in Widget (with body) -- both resolve.
    assert len(payload["definitions"]) == 2
    assert all(d["kind"] == "function" for d in payload["definitions"])
    lines = sorted(d["start_line"] for d in payload["definitions"])
    assert lines[0] != lines[1]


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


def test_source_returns_full_method_body(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    payload = repo_map.build_symbol_source("Create", tmp_path)

    assert not payload.get("no_match")
    assert payload["sources"], "expected at least one source block for Create"
    source_text = payload["sources"][0]["source"]
    assert "public static Widget Create(string name)" in source_text
    assert "return new Widget(name);" in source_text


# ---------------------------------------------------------------------------
# imports: plain / dotted / aliased / static / global using directives
# ---------------------------------------------------------------------------


def test_csharp_imports_and_symbols_extracts_using_directive_targets(tmp_path: Path) -> None:
    source = (
        "using System;\n"
        "using System.Collections.Generic;\n"
        "using MyAlias = System.Text.StringBuilder;\n"
        "using static System.Math;\n"
        "global using System.Linq;\n"
        "\n"
        "namespace App;\n"
        "\n"
        "public class Program\n"
        "{\n"
        "}\n"
    )
    cs_file = tmp_path / "Program.cs"
    cs_file.write_text(source, encoding="utf-8")

    imports, symbols = lang_csharp.csharp_imports_and_symbols(cs_file)

    assert imports == sorted({
        "System",
        "System.Collections.Generic",
        "System.Text.StringBuilder",  # the ALIASED target, never the alias "MyAlias" itself
        "System.Math",
        "System.Linq",
    })
    assert any(s["name"] == "Program" and s["kind"] == "class" for s in symbols)


def test_build_repo_map_surfaces_csharp_imports_and_symbols(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(tmp_path)

    file_imports = [
        entry for entry in repo_map_payload["imports"] if entry["file"].endswith("Widget.cs")
    ]
    assert file_imports, "expected an imports entry for Widget.cs"
    assert "System" in file_imports[0]["imports"]
    assert "System.Collections.Generic" in file_imports[0]["imports"]
    assert "System.Text.StringBuilder" in file_imports[0]["imports"]

    symbol_names = {
        s["name"] for s in repo_map_payload["symbols"] if s["file"].endswith("Widget.cs")
    }
    assert {"Widget", "IWidget", "WidgetKind", "WidgetRecord", "WidgetStruct", "Create"}.issubset(
        symbol_names
    )


# ---------------------------------------------------------------------------
# #74-follow-up: tg imports (csharp_imports_with_lines / build_file_imports) -- foundational
# tier, mirrors test_lang_java.py's test_file_imports_returns_java_import_statements_with_lines.
# ---------------------------------------------------------------------------


def test_csharp_imports_with_lines_extracts_using_directives_with_lines(tmp_path: Path) -> None:
    cs_file = _write_csharp_fixture(tmp_path)

    entries = lang_csharp.csharp_imports_with_lines(cs_file)

    modules = {entry["module"]: entry["line"] for entry in entries}
    assert modules == {
        "System": 1,
        "System.Collections.Generic": 2,
        # the ALIASED target, never the alias "MyAlias" itself (mirrors
        # csharp_imports_and_symbols's own extraction).
        "System.Text.StringBuilder": 3,
    }


def test_csharp_imports_with_lines_non_cs_suffix_returns_empty(tmp_path: Path) -> None:
    not_cs = tmp_path / "Widget.txt"
    not_cs.write_text("using System;\n", encoding="utf-8")

    assert lang_csharp.csharp_imports_with_lines(not_cs) == []


def test_csharp_imports_with_lines_grammar_absent_returns_empty(
    tmp_path: Path, monkeypatch
) -> None:
    cs_file = _write_csharp_fixture(tmp_path)
    monkeypatch.setattr(lang_csharp, "_csharp_parser", lambda: None)

    assert lang_csharp.csharp_imports_with_lines(cs_file) == []


def test_file_imports_returns_csharp_using_directives_with_lines(tmp_path: Path) -> None:
    cs_file = _write_csharp_fixture(tmp_path)

    payload = repo_map.build_file_imports(cs_file)

    assert payload["result_incomplete"] is False
    modules = {entry["module"]: entry["line"] for entry in payload["imports"]}
    assert modules == {
        "System": 1,
        "System.Collections.Generic": 2,
        "System.Text.StringBuilder": 3,
    }
    # Foundational tier: raw import statements are real, but resolving them to a specific file
    # (C# needs a .csproj/assembly-reference map that does not exist yet) is deferred -- every
    # row must be unresolved and never presumed external, matching the fail-closed contract.
    assert all(entry["resolved"] is None for entry in payload["imports"])
    assert all(entry["external"] is False for entry in payload["imports"])


# ---------------------------------------------------------------------------
# Deferred caller-graph, grammar PRESENT: honest resolution_gaps, not a silent proven-zero.
#
# Coordinator-flagged verification (parallel Go/PHP precedent): a language whose grammar IS
# installed (defs/source/imports all work) but whose LanguageSpec.import_update_target is None
# must still surface a resolution_gaps entry -- otherwise `tg refs`/`tg callers`/
# `tg blast-radius` returning zero rows for a C# consumer is indistinguishable from "genuinely
# zero", when it actually means "the reverse-import graph was never built for this language".
# This is the SAME generic mechanism Go's own import_update_target=None gap exercises
# (_language_coverage_gaps_for_universe, driven purely by `spec.import_update_target is None`) --
# no C#-specific code required, but pinned here as an explicit regression guard.
# ---------------------------------------------------------------------------


def test_refs_grammar_present_still_reports_import_resolution_gap(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("Widget", tmp_path)

    assert not payload.get("no_match")
    gaps = payload["resolution_gaps"]
    csharp_gaps = [gap for gap in gaps if gap["language"] == "csharp"]
    assert len(csharp_gaps) == 1
    # NOT "fail-closed" (that's the grammar-ABSENT case, covered separately below) -- this is
    # the narrower "grammar works fine, but no reverse-import resolver exists yet" gap.
    assert "fail-closed" not in csharp_gaps[0]["reason"]
    assert "reverse-import" in csharp_gaps[0]["reason"]
    assert csharp_gaps[0]["files_affected"] >= 1
    # Honesty floor: the remediation must tell an agent to treat a zero count as UNKNOWN, not
    # proven-zero -- the exact failure mode this test guards against.
    assert (
        "not proven-zero" in csharp_gaps[0]["remediation"]
        or "UNKNOWN" in (csharp_gaps[0]["remediation"])
    )


# ---------------------------------------------------------------------------
# Grammar-absent: fail-closed, resolution_gaps, honest exit code.
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_resolution_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_csharp_fixture(tmp_path)
    # A python symbol elsewhere in the same repo so refs has something REAL to find -- the
    # resolution_gaps floor is about a C# file being an honestly-labeled BYSTANDER in the scan
    # universe, not about the query's own target living in the grammar-missing language.
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(lang_csharp, "_csharp_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("Widget", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    defs_gaps = defs_payload["resolution_gaps"]
    assert any(gap["language"] == "csharp" for gap in defs_gaps)
    csharp_gap = next(gap for gap in defs_gaps if gap["language"] == "csharp")
    assert "fail-closed" in csharp_gap["reason"]

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    assert not refs_payload.get("no_match")
    gaps = refs_payload["resolution_gaps"]
    assert any(gap["language"] == "csharp" for gap in gaps)
    csharp_refs_gap = next(gap for gap in gaps if gap["language"] == "csharp")
    assert "fail-closed" in csharp_refs_gap["reason"]
    assert csharp_refs_gap["files_affected"] >= 1
    assert "fall back to plain literal-text/regex matching" not in csharp_refs_gap["remediation"]


def test_grammar_absent_cli_exit_code_is_honest_not_found(tmp_path: Path, monkeypatch) -> None:
    """A C#-only target with the grammar missing must exit 1 (honest not-found) -- never a
    silent 0 and never a crash."""
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    _write_csharp_fixture(tmp_path)
    monkeypatch.setattr(lang_csharp, "_csharp_parser", lambda: None)

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "Widget"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Agent capsule
# ---------------------------------------------------------------------------


def test_agent_capsule_reports_csharp_target_language(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    payload = agent_capsule.build_agent_capsule("Widget", tmp_path)

    assert payload["context_consistency"]["primary_target_language"] == "csharp"


# ---------------------------------------------------------------------------
# Deep-AST guard: explicit-stack DFS must not raise RecursionError (lang_go.py F26 precedent).
# ---------------------------------------------------------------------------


def _deep_nested_csharp_source(depth: int) -> str:
    return (
        "public class Deep\n{\n    public int Target()\n    {\n        return "
        + ("(" * depth)
        + "1"
        + (")" * depth)
        + ";\n    }\n}\n"
    )


def test_csharp_walkers_survive_pathologically_deep_ast_without_recursion_error(
    tmp_path: Path,
) -> None:
    depth = sys.getrecursionlimit() + 500
    deep_cs = tmp_path / "Deep.cs"
    deep_cs.write_text(_deep_nested_csharp_source(depth), encoding="utf-8")

    imports, symbols = lang_csharp.csharp_imports_and_symbols(deep_cs)
    assert imports == []
    assert any(s["name"] == "Target" and s["kind"] == "function" for s in symbols)
    assert any(s["name"] == "Deep" and s["kind"] == "class" for s in symbols)

    sources = lang_csharp.csharp_parser_symbol_sources(deep_cs, "Target")
    assert len(sources) == 1
    assert sources[0]["kind"] == "function"


# ---------------------------------------------------------------------------
# Grammar-missing import failure (package not installed) -- distinct from monkeypatched None.
# ---------------------------------------------------------------------------


def test_csharp_parser_returns_none_when_grammar_module_missing(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "tree_sitter_c_sharp":
            raise ImportError("simulated missing grammar")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    lang_csharp._csharp_parser.cache_clear()
    try:
        assert lang_csharp._csharp_parser() is None
    finally:
        lang_csharp._csharp_parser.cache_clear()


# ---------------------------------------------------------------------------
# Read/parse-error guards return ([], []) rather than raising.
# ---------------------------------------------------------------------------


def test_csharp_imports_and_symbols_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "DoesNotExist.cs"
    imports, symbols = lang_csharp.csharp_imports_and_symbols(missing)
    assert imports == []
    assert symbols == []


def test_csharp_imports_and_symbols_non_cs_suffix_returns_empty(tmp_path: Path) -> None:
    other = tmp_path / "Widget.txt"
    other.write_text("not csharp", encoding="utf-8")
    imports, symbols = lang_csharp.csharp_imports_and_symbols(other)
    assert imports == []
    assert symbols == []
