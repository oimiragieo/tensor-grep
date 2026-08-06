"""C# symbol graph (lang_csharp.py) tests -- deep symbol-intelligence tier expansion.

Foundational scope (mirrors PATH A Stage 1's lang_go.py precedent, not the pre-registry
Rust/JS/TS inline pattern): C# gets its own ``LanguageSpec`` entry + dedicated module providing
``defs``/``source``/``imports``/``agent`` support (classes/interfaces/structs/enums/records +
methods/constructors, plus ``using`` directive extraction). Task 10B wires
``references_and_calls`` (in-file). F7 Task 11 wave 2 wires
``file_imports_symbol_from_definition`` (namespace/``using``). Remaining cross-file fields
(``import_update_target`` / ``prime_repo_context``) stay deferred.

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
from typing import Any

import pytest

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
    # Task 10B: in-file references_and_calls. F7 Task 11 wave 2: file_imports_symbol_from_definition
    # (namespace/using). Remaining cross-file fields stay deferred.
    assert spec.references_and_calls is not None
    assert spec.provider_alias_calls is None
    assert spec.file_imports_symbol_from_definition is (
        lang_csharp.csharp_file_imports_symbol_from_definition
    )
    assert spec.import_update_target is None
    assert spec.prime_repo_context is None
    assert spec.classify_ref_kind is None


def test_target_language_for_path_reports_csharp() -> None:
    assert repo_map._target_language_for_path("Widget.cs") == "csharp"
    assert repo_map._language_for_path("Widget.cs") == "csharp"
    assert repo_map._provider_language_for_path("Widget.cs") == "csharp"


# ---------------------------------------------------------------------------
# defs
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
def test_defs_finds_interface_struct_enum_record_as_class_kind(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    for name in ("IWidget", "WidgetStruct", "WidgetKind", "WidgetRecord"):
        payload = repo_map.build_symbol_defs(name, tmp_path)
        assert not payload.get("no_match"), f"expected a definition for {name}"
        assert payload["definitions"][0]["kind"] == "class", f"{name} should be kind=class"


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


# Task 10B pre-fix RED arms: promote C# from the foundational tier to parser-backed
# refs/callers, mirroring Task 10A's Java landing (lang_java.java_references_and_calls).
#
# Both nodes below MUST fail before any Task 10B implementation lands, and each must fail
# for a BEHAVIOUR-SPECIFIC reason -- not an ImportError or a NameError, which would be a
# false red proving only that a symbol is missing. The first asserts the registry seam; the
# second asserts the product's own derived descriptor, which is the thing every doc and the
# rust_core schema test key on.


def test_csharp_references_and_calls_is_registered_non_none() -> None:
    """Task 10B RED: C# must register a real ``references_and_calls`` extractor.

    Pre-fix this is ``None``, so ``_references_and_calls_for_path`` falls through to
    ``_regex_references_and_calls``, which itself returns ``([], [])`` for any suffix outside
    ``_JS_TS_SUFFIXES | _RUST_SUFFIXES`` -- including ``.cs``. So C#'s "regex fallback" is not
    a text heuristic over C# source at all; it is an unconditional empty result.
    """
    spec = lang_registry.LANGUAGE_REGISTRY["csharp"]
    assert spec.references_and_calls is not None


def test_csharp_moves_into_the_parser_backed_tier_descriptor() -> None:
    """Task 10B RED: the product's derived tier descriptor must list csharp as parser-backed.

    Asserted against the descriptor rather than a hardcoded string, so this node cannot go
    green by editing a doc. ``_symbol_navigation_descriptor`` partitions every registered
    LanguageSpec by exactly one boolean (``references_and_calls is not None``), so csharp
    lands in exactly one of the two halves -- never both, never neither.
    """
    descriptor = repo_map._symbol_navigation_descriptor()
    parser_backed, _, foundational = descriptor.partition("+")
    assert "csharp" in parser_backed, descriptor
    assert "csharp" not in foundational, descriptor


# ---------------------------------------------------------------------------
# Task 10B: csharp_references_and_calls AST-shape coverage (mirrors test_lang_java.py's own
# references_and_calls section, adapted to C#'s own grammar shapes -- see lang_csharp.py's
# module docstring "TASK 10B" section for the exact node-shape differences from Java: C#'s
# ``invocation_expression`` has a single ``function`` field that is EITHER a bare ``identifier``
# (unqualified call) OR a ``member_access_expression`` (qualified call), and C# reuses
# ``member_access_expression`` for both a call's qualifier and a plain field/property read).
# ---------------------------------------------------------------------------


def _csharp_parser_or_skip() -> Any:
    parser = lang_csharp._csharp_parser()
    if parser is None:  # pragma: no cover - grammar always installed in this venv
        pytest.skip("tree_sitter_c_sharp grammar not installed")
    return parser


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_provenance_is_parser_backed(tmp_path: Path) -> None:
    _write_csharp_fixture(tmp_path)

    references, calls = repo_map._references_and_calls_for_path(
        tmp_path / "Widget.cs", "_name", tmp_path
    )

    assert references, "expected the tree-sitter extractor to find `_name` references"
    assert {r["ref_kind"] for r in references} == {"value"}
    assert calls == []
    assert repo_map._symbol_navigation_provenance_for_path(str(tmp_path / "Widget.cs")) == (
        "tree-sitter"
    )


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_unqualified_call(tmp_path: Path) -> None:
    cs_file = tmp_path / "Plain.cs"
    cs_file.write_text(
        "public class Plain\n{\n    public void Run()\n    {\n        DoWork();\n    }\n}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "call", 5)]
    assert [(c["kind"], c["ref_kind"], c["line"]) for c in calls] == [("call", "call", 5)]


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_object_creation_expression_constructor(
    tmp_path: Path,
) -> None:
    cs_file = tmp_path / "Ctor.cs"
    cs_file.write_text(
        "public class Ctor\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        Helper h = new Helper();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Helper")

    # "Helper" appears twice: the declared local-variable TYPE (ref_kind "type") and the
    # `new Helper()` constructor call (ref_kind "constructor").
    assert sorted(r["ref_kind"] for r in references) == ["constructor", "type"]
    assert [c["ref_kind"] for c in calls] == ["constructor"]


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_qualified_member_call(tmp_path: Path) -> None:
    cs_file = tmp_path / "Qualified.cs"
    cs_file.write_text(
        "public class Qualified\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        object h = null;\n"
        "        h.Helper();\n"
        "        Utility.Helper();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Helper")

    # Both `h.Helper()` (instance-qualified) and `Utility.Helper()` (class-qualified) resolve the
    # same way at the AST level: both are invocation_expression.function.member_access_expression
    # name matches -> ref_kind "call", one row per call site. (Deliberately a DIFFERENT qualifier
    # name than the queried symbol -- `Utility` vs `Helper` -- so the qualifier identifier itself
    # never coincidentally also matches the query and pollutes this fixture's reference count.)
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "call", 6),
        ("reference", "call", 7),
    ]
    assert len(calls) == 2


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_this_qualified_call(tmp_path: Path) -> None:
    cs_file = tmp_path / "ThisCall.cs"
    cs_file.write_text(
        "public class ThisCall\n"
        "{\n"
        "    public void DoWork() {}\n"
        "    public void Run()\n"
        "    {\n"
        "        this.DoWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    _, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    assert [(c["kind"], c["ref_kind"], c["line"]) for c in calls] == [("call", "call", 6)]


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_field_access_non_call_member(tmp_path: Path) -> None:
    cs_file = tmp_path / "FieldAccess.cs"
    cs_file.write_text(
        "public class FieldAccess\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        Widget w = null;\n"
        "        int y = w.Count;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Count")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "field", 6)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_type_reference(tmp_path: Path) -> None:
    cs_file = tmp_path / "TypeRef.cs"
    cs_file.write_text(
        "public class TypeRef : Foo\n{\n    private Foo field;\n}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Foo")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "type", 1),
        ("reference", "type", 3),
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_excludes_same_name_declaration(tmp_path: Path) -> None:
    cs_file = tmp_path / "Decl.cs"
    cs_file.write_text(
        "public class Decl\n"
        "{\n"
        "    private int count;\n"
        "    public int Read()\n"
        "    {\n"
        "        return count;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "count")

    # `private int count;` (line 3) is the DECLARATION -- must not appear. `return count;`
    # (line 6) is the one real reference.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "value", 6)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_ignores_string_literal_and_comment_occurrences(
    tmp_path: Path,
) -> None:
    cs_file = tmp_path / "Noise.cs"
    cs_file.write_text(
        "public class Noise\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        '        string s = "Helper";\n'
        "        // Helper mentioned only in a comment\n"
        "        Helper h = null;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Helper")

    # Only the REAL type reference on line 7 counts -- the string literal (line 5) and the
    # comment (line 6) must never match; a text/regex scan could not make this distinction.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "type", 7)]
    assert calls == []


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_defeats_regex_fallback(tmp_path: Path) -> None:
    """AST-only distinction the regex fallback provably cannot satisfy: for a REAL call site,
    ``_regex_references_and_calls`` (C#'s pre-Task-10B fallback) finds nothing at all, while the
    AST extractor finds the real call precisely."""
    cs_file = tmp_path / "Defeats.cs"
    cs_file.write_text(
        "public class Defeats\n{\n    public void Run()\n    {\n        DoWork();\n    }\n}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    regex_references, regex_calls = repo_map._regex_references_and_calls(cs_file, "DoWork")
    ast_references, ast_calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    assert regex_references == []
    assert regex_calls == []
    assert ast_references and ast_references[0]["ref_kind"] == "call"
    assert ast_calls and ast_calls[0]["ref_kind"] == "call"


def test_csharp_references_and_calls_grammar_absent_returns_empty_not_crash(
    tmp_path: Path, monkeypatch
) -> None:
    cs_file = tmp_path / "NoGrammar.cs"
    cs_file.write_text(
        "public class NoGrammar\n{\n    public void Run()\n    {\n        DoWork();\n    }\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lang_csharp, "_csharp_parser", lambda: None)

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    assert references == []
    assert calls == []


def test_csharp_references_and_calls_returns_empty_for_non_cs_suffix(tmp_path: Path) -> None:
    not_cs = tmp_path / "Widget.txt"
    not_cs.write_text("public class Widget {}\n", encoding="utf-8")

    references, calls = lang_csharp.csharp_references_and_calls(not_cs, "Widget")

    assert references == []
    assert calls == []


# ---------------------------------------------------------------------------
# The honest-confidence requirement: the two bands must actually DISCRIMINATE (different
# confidence, different provenance string) on a fixture with one confirmable call site and one
# unconfirmable one -- a control that never crosses the boundary between "unresolved receiver"
# and "in-file-confirmed receiver" would prove nothing (mirrors
# test_java_references_and_calls_infile_receiver_type_confirms_higher_band exactly, adapted to
# C#'s own grammar/provenance strings).
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_unconfirmed_receiver_is_demoted(tmp_path: Path) -> None:
    """`h.Helper()`'s receiver `h` is declared `object` in this file, and no type in this file
    declares a method named `Helper` -- there is nothing to confirm against, so every bucket
    entry must carry the DEMOTED band."""
    cs_file = tmp_path / "Unresolved.cs"
    cs_file.write_text(
        "public class Unresolved\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        object h = null;\n"
        "        h.Helper();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "Helper")

    assert len(calls) == 1
    assert len(references) == 1
    for entry in (*references, *calls):
        assert entry["resolution_provenance"] == ["csharp-name-heuristic"]
        assert entry["resolution_confidence"] == pytest.approx(0.6)


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_infile_receiver_type_confirms_higher_band(
    tmp_path: Path,
) -> None:
    """`t.DoWork()`'s receiver `t` is declared `Target` in THIS file, and `Target` (also declared
    in this file) directly declares a `DoWork` method -- both facts readable from the same AST,
    so this call confirms at the HIGHER band with a DIFFERENT provenance string. `o.DoWork()`
    (receiver type `object`) is the discriminating CONTROL in the same file/query shape: it must
    land in the demoted band, proving the two bands actually diverge rather than both landing on
    one value by construction -- the load-bearing fixture for this task.
    """
    cs_file = tmp_path / "Confirmed.cs"
    cs_file.write_text(
        "public class Target\n"
        "{\n"
        "    public void DoWork() {}\n"
        "}\n"
        "public class Caller\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        Target t = new Target();\n"
        "        object o = null;\n"
        "        t.DoWork();\n"
        "        o.DoWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    confirmed_calls = [c for c in calls if c["line"] == 11]
    demoted_calls = [c for c in calls if c["line"] == 12]
    assert len(confirmed_calls) == 1
    assert len(demoted_calls) == 1
    confirmed = confirmed_calls[0]
    demoted = demoted_calls[0]

    assert confirmed["resolution_provenance"] == ["csharp-infile-type-confirmation"]
    assert confirmed["resolution_confidence"] == pytest.approx(0.9)
    assert demoted["resolution_provenance"] == ["csharp-name-heuristic"]
    assert demoted["resolution_confidence"] == pytest.approx(0.6)

    # The discriminating control itself: confirmed must be STRICTLY higher, with a DIFFERENT
    # provenance string -- both arms must actually diverge, not merely both be present. This is
    # the "honest-confidence" fixture: a single-band implementation wearing two names would fail
    # this exact assertion (both bands would read identically).
    assert confirmed["resolution_confidence"] > demoted["resolution_confidence"]
    assert confirmed["resolution_provenance"] != demoted["resolution_provenance"]

    confirmed_refs = [r for r in references if r["line"] == 11]
    assert confirmed_refs[0]["resolution_provenance"] == ["csharp-infile-type-confirmation"]
    assert confirmed_refs[0]["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_this_receiver_confirms_higher_band(tmp_path: Path) -> None:
    """`this.DoWork()` inside the SAME class that declares `DoWork` must also confirm -- the
    enclosing type IS the receiver's static type by definition, no variable declaration needed.
    """
    cs_file = tmp_path / "ThisConfirmed.cs"
    cs_file.write_text(
        "public class Target\n"
        "{\n"
        "    public void DoWork() {}\n"
        "    public void Run()\n"
        "    {\n"
        "        this.DoWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    _, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    this_call = next(c for c in calls if c["line"] == 6)
    assert this_call["resolution_provenance"] == ["csharp-infile-type-confirmation"]
    assert this_call["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_unqualified_call_confirms_via_enclosing_type(
    tmp_path: Path,
) -> None:
    """An UNQUALIFIED call (`DoWork()`, no explicit receiver) has an implicit `this` -- C#
    permits omitting the qualifier entirely, unlike Java's `method_invocation` (which always has
    an explicit or absent `object` field the walker can inspect directly). Confirmed only because
    the ENCLOSING type itself declares `DoWork`."""
    cs_file = tmp_path / "Implicit.cs"
    cs_file.write_text(
        "public class Target\n"
        "{\n"
        "    public void DoWork() {}\n"
        "    public void Run()\n"
        "    {\n"
        "        DoWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    _, calls = lang_csharp.csharp_references_and_calls(cs_file, "DoWork")

    unqualified_call = next(c for c in calls if c["line"] == 6)
    assert unqualified_call["resolution_provenance"] == ["csharp-infile-type-confirmation"]
    assert unqualified_call["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_csharp_references_and_calls_property_access_confirms_higher_band(
    tmp_path: Path,
) -> None:
    """C#'s idiomatic member access is usually a PROPERTY, not a raw field -- the grammar does
    not distinguish a property read from a field read at the reference site (both are
    ``member_access_expression``), so a property declared in this file must confirm exactly like
    a field does."""
    cs_file = tmp_path / "PropConfirmed.cs"
    cs_file.write_text(
        "public class Target\n"
        "{\n"
        "    public int Count { get; set; }\n"
        "}\n"
        "public class Caller\n"
        "{\n"
        "    public void Run()\n"
        "    {\n"
        "        Target t = new Target();\n"
        "        int x = t.Count;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _csharp_parser_or_skip()

    references, _ = lang_csharp.csharp_references_and_calls(cs_file, "Count")

    prop_ref = next(r for r in references if r["line"] == 10)
    assert prop_ref["ref_kind"] == "field"
    assert prop_ref["resolution_provenance"] == ["csharp-infile-type-confirmation"]
    assert prop_ref["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_csharp_walkers_survive_pathologically_deep_ast_without_recursion_error_refs(
    tmp_path: Path,
) -> None:
    """F26-class regression guard for csharp_references_and_calls specifically (the sibling defs/
    imports walkers are already covered by
    test_csharp_walkers_survive_pathologically_deep_ast_without_recursion_error above)."""
    depth = sys.getrecursionlimit() + 500
    deep_cs = tmp_path / "DeepRefs.cs"
    deep_cs.write_text(_deep_nested_csharp_source(depth), encoding="utf-8")
    _csharp_parser_or_skip()

    # "Target" here names the deeply-nested method itself (its own definition site, excluded by
    # design) -- the point of this guard is that the walk COMPLETES without RecursionError, not
    # that it finds a hit.
    references, calls = lang_csharp.csharp_references_and_calls(deep_cs, "Target")

    assert references == []
    assert calls == []
