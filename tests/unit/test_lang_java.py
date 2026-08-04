"""PATH A STAGE 2 -- Java symbol graph (FOUNDATIONAL TIER) tests.

Java joins the symbol graph the same way Go did (own ``LanguageSpec``, tree-sitter-backed,
fail-closed with no regex fallback), but SCOPED to the foundational tier only: symbols
(classes/interfaces/enums/records/methods/constructors) and raw import declarations flow into
``build_repo_map`` / `tg defs` / `tg source` / `tg imports` / `tg agent`. The deep caller-graph
(cross-file method-call resolution powering `tg callers` / `tg blast-radius`) is intentionally
NOT implemented here -- ``LanguageSpec.references_and_calls`` /
``file_imports_symbol_from_definition`` / ``import_update_target`` / ``prime_repo_context`` /
``classify_ref_kind`` are all ``None``, deferred to a follow-up PR. See the last section below
for the honesty-floor coverage of that deferral (never a crash, always a labeled
``resolution_gaps`` entry).

Covered here:
- Registration + provenance (``tree-sitter`` when the grammar is installed, ``grammar-missing``
  when it is not -- Java has no regex fallback, mirroring Go's fail-closed contract).
- ``_target_language_for_path`` / ``_provider_language_for_path`` / ``_language_for_path`` all
  agree Java resolves to ``"java"`` (the "MOST-FORGOTTEN seam" ``test_lang_registry.py`` already
  guards dynamically for every registered language).
- ``_java_imports_and_symbols``: classes/interfaces/enums/records -> kind "class"; methods/
  constructors -> kind "function"; dotted import names (plain, multi-segment, ``static``, and
  wildcard ``*`` imports) all extracted; sorted/deduped exactly like
  ``_python_imports_and_symbols``.
- ``build_repo_map`` surfaces those symbols/imports for a real ``.java`` file on disk (the
  actual dispatch path `tg orient`/`tg agent` read).
- `tg defs` (``build_symbol_defs``) and `tg source` (``build_symbol_source``) resolve a Java
  symbol with ``provenance == "tree-sitter"`` and the exact source block.
- `tg imports` (``build_file_imports``) returns real import rows (module + line) for a ``.java``
  file instead of ``result_incomplete``.
- `tg agent` (``agent_capsule.build_agent_capsule``) reports
  ``primary_target_language == "java"``.
- Grammar-absent: fail-closed, zero fabricated symbols, an honest ``resolution_gaps`` entry.
- Deferred caller-graph: `tg refs`/`tg callers` on a Java-only target never crash and surface an
  honest ``import_resolution_only`` resolution gap instead of silently reading as "confirmed
  zero".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule, lang_java, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: a package-declared Java file with plain/multi-segment/static/wildcard imports, a
# class (with a field, a constructor, and two methods, one annotated), and a separate interface
# declaration in the same file.
# ---------------------------------------------------------------------------


def _write_java_fixture(root: Path) -> dict[str, Path]:
    widget_java = root / "Widget.java"
    widget_java.write_text(
        "package com.example.widgets;\n"
        "\n"
        "import java.util.List;\n"
        "import java.util.Map;\n"
        "import static java.lang.Math.max;\n"
        "import com.example.other.*;\n"
        "\n"
        "public class Widget implements Runnable {\n"
        "    private int count;\n"
        "\n"
        "    public Widget(int count) {\n"
        "        this.count = count;\n"
        "    }\n"
        "\n"
        "    public int getCount() {\n"
        "        return count;\n"
        "    }\n"
        "\n"
        "    @Override\n"
        "    public void run() {\n"
        "        System.out.println(count);\n"
        "    }\n"
        "}\n"
        "\n"
        "interface Shape {\n"
        "    double area();\n"
        "}\n"
        "\n"
        "enum Color {\n"
        "    RED, GREEN, BLUE\n"
        "}\n"
        "\n"
        "record Point(int x, int y) {\n"
        "}\n",
        encoding="utf-8",
    )
    return {"Widget.java": widget_java}


# ---------------------------------------------------------------------------
# Registration + provenance
# ---------------------------------------------------------------------------


def test_java_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["java"]
    assert spec.suffixes == frozenset({".java"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (mirrors Go's Stage 1 trap): never "regex-heuristic"/"heuristic" -- Java has
    # no plain-text fallback when the grammar is missing.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None
    # Task 10A: in-file AST references_and_calls is now wired (parser-backed-refs-callers
    # tier). The CROSS-FILE caller-graph fields (package/source-root import resolution, Task
    # 11A) are still explicitly deferred.
    assert spec.references_and_calls is not None
    assert spec.provider_alias_calls is None
    assert spec.file_imports_symbol_from_definition is None
    assert spec.import_update_target is None
    assert spec.prime_repo_context is None
    assert spec.classify_ref_kind is None


def test_target_and_provider_language_for_path_report_java() -> None:
    assert repo_map._target_language_for_path("Widget.java") == "java"
    assert repo_map._language_for_path("Widget.java") == "java"
    assert repo_map._provider_language_for_path("Widget.java") == "java"


@pytest.mark.requires_grammar
def test_java_provenance_is_tree_sitter_when_grammar_present() -> None:
    assert repo_map._symbol_navigation_provenance_for_path("Widget.java") == "tree-sitter"


def test_grammar_absent_monkeypatch_java_provenance_flips_to_grammar_missing(monkeypatch) -> None:
    monkeypatch.setattr(repo_map, "_java_parser", lambda: None)

    provenance = repo_map._symbol_navigation_provenance_for_path("Widget.java")

    assert provenance == "grammar-missing"
    assert provenance != ""


# ---------------------------------------------------------------------------
# _java_imports_and_symbols: direct unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_java_imports_and_symbols_extracts_classes_interface_and_methods(tmp_path: Path) -> None:
    fixture = _write_java_fixture(tmp_path)

    imports, symbols = repo_map._java_imports_and_symbols(fixture["Widget.java"])

    assert imports == [
        "com.example.other.*",
        "java.lang.Math.max",
        "java.util.List",
        "java.util.Map",
    ]

    # "Widget" is deliberately BOTH the class name and its constructor's name (Java convention),
    # so a flat name -> symbol dict would collapse the two entries -- key on (name, kind) pairs
    # instead to keep them distinct, exactly as _java_imports_and_symbols itself must.
    by_name_kind = {(s["name"], s["kind"]): s for s in symbols}
    assert set(by_name_kind) == {
        ("Widget", "class"),
        ("Widget", "function"),  # the constructor
        ("Shape", "class"),
        ("Color", "class"),
        ("Point", "class"),
        ("getCount", "function"),
        ("run", "function"),
        ("area", "function"),
    }

    widget_class = by_name_kind[("Widget", "class")]
    assert widget_class["start_line"] == 8
    assert widget_class["file"] == str(fixture["Widget.java"])

    widget_constructor = by_name_kind[("Widget", "function")]
    assert widget_constructor["start_line"] == 11

    # Sort order pinned exactly like _python_imports_and_symbols: (file, line, kind, name).
    ordering = [(s["file"], s["line"], s["kind"], s["name"]) for s in symbols]
    assert ordering == sorted(ordering)


def test_java_imports_and_symbols_returns_empty_for_non_java_suffix(tmp_path: Path) -> None:
    not_java = tmp_path / "Widget.txt"
    not_java.write_text("class Widget {}\n", encoding="utf-8")

    imports, symbols = repo_map._java_imports_and_symbols(not_java)

    assert imports == []
    assert symbols == []


def test_java_imports_and_symbols_fails_closed_when_grammar_missing(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _write_java_fixture(tmp_path)
    monkeypatch.setattr(repo_map, "_java_parser", lambda: None)

    imports, symbols = repo_map._java_imports_and_symbols(fixture["Widget.java"])

    # Mirrors _python_imports_and_symbols's guard: parser-None -> ([], []), never a crash and
    # never a silent partial-regex degrade (Java has no regex fallback).
    assert imports == []
    assert symbols == []


def test_java_imports_and_symbols_handles_unreadable_file(tmp_path: Path) -> None:
    missing = tmp_path / "DoesNotExist.java"

    imports, symbols = repo_map._java_imports_and_symbols(missing)

    assert imports == []
    assert symbols == []


# ---------------------------------------------------------------------------
# build_repo_map integration
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_build_repo_map_surfaces_java_symbols_and_imports(tmp_path: Path) -> None:
    _write_java_fixture(tmp_path)

    repo_map_payload = repo_map.build_repo_map(tmp_path)

    symbol_names = {symbol["name"] for symbol in repo_map_payload["symbols"]}
    assert {"Widget", "Shape", "Color", "Point", "getCount", "run", "area"} <= symbol_names

    java_import_entries = [
        entry for entry in repo_map_payload["imports"] if entry["file"].endswith("Widget.java")
    ]
    assert len(java_import_entries) == 1
    assert set(java_import_entries[0]["imports"]) == {
        "com.example.other.*",
        "java.lang.Math.max",
        "java.util.List",
        "java.util.Map",
    }
    assert java_import_entries[0]["provenance"] == "tree-sitter"


# ---------------------------------------------------------------------------
# tg defs / tg source
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_defs_finds_class_interface_and_method_with_tree_sitter_provenance(
    tmp_path: Path,
) -> None:
    _write_java_fixture(tmp_path)

    class_payload = repo_map.build_symbol_defs("Widget", tmp_path)
    interface_payload = repo_map.build_symbol_defs("Shape", tmp_path)
    method_payload = repo_map.build_symbol_defs("getCount", tmp_path)

    assert not class_payload.get("no_match")
    # "Widget" genuinely has TWO definitions in the fixture: the class_declaration itself and
    # its same-named constructor_declaration -- both are real, correct hits, not a dedup bug.
    class_kinds = {d["kind"] for d in class_payload["definitions"]}
    assert class_kinds == {"class", "function"}
    assert all(d["provenance"] == "tree-sitter" for d in class_payload["definitions"])

    assert not interface_payload.get("no_match")
    assert interface_payload["definitions"][0]["kind"] == "class"

    assert not method_payload.get("no_match")
    assert method_payload["definitions"][0]["kind"] == "function"
    assert method_payload["definitions"][0]["file"].replace("\\", "/").endswith("Widget.java")


@pytest.mark.requires_grammar
def test_source_returns_exact_method_body_for_java_symbol(tmp_path: Path) -> None:
    _write_java_fixture(tmp_path)

    payload = repo_map.build_symbol_source("getCount", tmp_path)

    assert not payload.get("no_match")
    assert payload["sources"], "expected a source block for getCount"
    source_block = payload["sources"][0]
    assert source_block["kind"] == "function"
    assert "return count;" in source_block["source"]
    assert source_block["source"].strip().startswith("public int getCount()")


# ---------------------------------------------------------------------------
# tg imports (build_file_imports)
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_file_imports_returns_java_import_statements_with_lines(tmp_path: Path) -> None:
    fixture = _write_java_fixture(tmp_path)

    payload = repo_map.build_file_imports(fixture["Widget.java"])

    assert payload["result_incomplete"] is False
    modules = {entry["module"]: entry["line"] for entry in payload["imports"]}
    assert modules == {
        "java.util.List": 3,
        "java.util.Map": 4,
        "java.lang.Math.max": 5,
        "com.example.other.*": 6,
    }
    # Foundational tier: raw import statements are real, but resolving them to a specific file
    # (cross-file resolution) is deferred -- every row must be unresolved, never fabricated.
    assert all(entry["resolved"] is None for entry in payload["imports"])


# ---------------------------------------------------------------------------
# tg agent (agent_capsule)
# ---------------------------------------------------------------------------


def test_agent_capsule_reports_java_target_language(tmp_path: Path) -> None:
    _write_java_fixture(tmp_path)

    payload = agent_capsule.build_agent_capsule("Widget", tmp_path)

    assert payload["context_consistency"]["primary_target_language"] == "java"


# ---------------------------------------------------------------------------
# Grammar-absent: fail-closed, resolution_gaps, no fabricated defs.
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_resolution_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_java_fixture(tmp_path)
    # A python symbol elsewhere in the same repo so refs has something real to find -- the
    # resolution_gaps floor is about the Java file being an honestly-labeled bystander in the
    # scan universe, not about the query's own target living in the grammar-missing language.
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "_java_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("Widget", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    defs_gaps = defs_payload["resolution_gaps"]
    java_gap = next(gap for gap in defs_gaps if gap["language"] == "java")
    assert "fail-closed" in java_gap["reason"]

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    assert not refs_payload.get("no_match")
    gaps = refs_payload["resolution_gaps"]
    java_refs_gap = next(gap for gap in gaps if gap["language"] == "java")
    assert "fail-closed" in java_refs_gap["reason"]
    assert java_refs_gap["files_affected"] >= 1
    assert "fall back to plain literal-text/regex matching" not in java_refs_gap["remediation"]


# ---------------------------------------------------------------------------
# Deferred caller-graph: never a crash, always an honest resolution gap.
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_refs_and_callers_never_crash_and_flag_java_as_import_resolution_gap(
    tmp_path: Path,
) -> None:
    _write_java_fixture(tmp_path)
    (tmp_path / "target.py").write_text(
        "def Target():\n    return 1\n\n\ndef caller():\n    return Target()\n",
        encoding="utf-8",
    )

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    callers_payload = repo_map.build_symbol_callers("Target", tmp_path)

    assert not refs_payload.get("no_match")
    assert not callers_payload.get("no_match")
    for payload in (refs_payload, callers_payload):
        gaps = payload["resolution_gaps"]
        java_gap = next(gap for gap in gaps if gap["language"] == "java")
        assert java_gap["files_affected"] >= 1
        assert "reverse-import" in java_gap["reason"]
        assert "fail-closed" not in java_gap["reason"]


def test_java_references_and_calls_is_registered_non_none() -> None:
    """Task 10A pre-fix RED: Java must register a real ``references_and_calls`` extractor."""
    spec = lang_registry.LANGUAGE_REGISTRY["java"]
    assert spec.references_and_calls is not None


def test_references_and_calls_base_green_regex_fallback_is_empty_for_java(
    tmp_path: Path,
) -> None:
    """Task 10A base-green characterization (run BEFORE any Task 10A code changes).

    Java is currently registered with ``references_and_calls=None``, so
    ``_references_and_calls_for_path`` falls through to ``_regex_references_and_calls``. That
    function itself gates on ``path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES`` and returns
    ``([], [])`` immediately for ANY other suffix -- INCLUDING ``.java``. So Java's "regex
    fallback" is not actually a text-heuristic scan of Java source at all: it is unconditionally
    empty, for every query, real or not. This pins that exact (surprising) baseline before Task
    10A replaces it with a real AST-backed extractor.
    """
    fixture = _write_java_fixture(tmp_path)

    references, calls = repo_map._references_and_calls_for_path(
        fixture["Widget.java"], "getCount", tmp_path
    )

    assert references == []
    assert calls == []
    # Confirm directly too: the regex fallback itself is suffix-gated away from .java.
    direct_references, direct_calls = repo_map._regex_references_and_calls(
        fixture["Widget.java"], "getCount"
    )
    assert direct_references == []
    assert direct_calls == []


# ---------------------------------------------------------------------------
# Task 10A: java_references_and_calls AST-shape coverage.
#
# Each test below calls ``lang_java.java_references_and_calls`` directly with a real parser
# object (``repo_map._java_parser()``) -- unit-level, no repo scan -- so a wrong/empty result
# pins the EXACT behavior being asserted rather than a downstream integration symptom.
# ---------------------------------------------------------------------------


def _java_parser_or_skip() -> Any:
    parser = repo_map._java_parser()
    if parser is None:  # pragma: no cover - grammar always installed in this venv
        pytest.skip("tree_sitter_java grammar not installed")
    return parser


@pytest.mark.requires_grammar
def test_java_references_and_calls_provenance_is_parser_backed(tmp_path: Path) -> None:
    """Once registered, refs/calls for a Java symbol must actually come from the tree-sitter
    walk (non-empty on a real hit), not the always-empty regex-fallback baseline pinned above.
    "count" is referenced (never called) several times in the fixture -- `this.count = count;`
    (a field_access LHS plus a plain-value RHS), `return count;`, and
    `System.out.println(count);` -- so this also exercises the "no calls for a non-call symbol"
    honesty floor.
    """
    fixture = _write_java_fixture(tmp_path)

    references, calls = repo_map._references_and_calls_for_path(
        fixture["Widget.java"], "count", tmp_path
    )

    assert references, "expected the tree-sitter extractor to find `count` references"
    assert {r["ref_kind"] for r in references} == {"field", "value"}
    assert calls == []
    assert repo_map._symbol_navigation_provenance_for_path(str(fixture["Widget.java"])) == (
        "tree-sitter"
    )


@pytest.mark.requires_grammar
def test_java_references_and_calls_method_invocation_plain_call(tmp_path: Path) -> None:
    java_file = tmp_path / "Plain.java"
    java_file.write_text(
        "class Plain {\n    void run() {\n        doWork();\n    }\n}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "doWork", parser=parser)

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "call", 3)]
    assert [(c["kind"], c["ref_kind"], c["line"]) for c in calls] == [("call", "call", 3)]


@pytest.mark.requires_grammar
def test_java_references_and_calls_object_creation_expression_constructor(
    tmp_path: Path,
) -> None:
    java_file = tmp_path / "Ctor.java"
    java_file.write_text(
        "class Ctor {\n    void run() {\n        Helper h = new Helper();\n    }\n}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "Helper", parser=parser)

    ref_kinds = [r["ref_kind"] for r in references]
    # "Helper" appears twice: the declared local-variable TYPE (type_identifier, ref_kind
    # "type") and the `new Helper()` constructor call (ref_kind "constructor").
    assert sorted(ref_kinds) == ["constructor", "type"]
    assert [c["ref_kind"] for c in calls] == ["constructor"]


@pytest.mark.requires_grammar
def test_java_references_and_calls_qualified_member_call(tmp_path: Path) -> None:
    java_file = tmp_path / "Qualified.java"
    java_file.write_text(
        "class Qualified {\n"
        "    void run() {\n"
        "        Object h = null;\n"
        "        h.helper();\n"
        "        Helper.helper();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "helper", parser=parser)

    # Both `h.helper()` (instance-qualified) and `Helper.helper()` (class-qualified) resolve the
    # SAME way at the AST level (Task 10A does no cross-file type resolution): both are
    # method_invocation.name matches -> ref_kind "call", one row per call site.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "call", 4),
        ("reference", "call", 5),
    ]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Fix-first follow-up to Task 10A: per-row resolution_confidence/resolution_provenance honesty
# banding. Java has no import/type resolver (Task 11A), so EVERY entry now carries both fields;
# the two tests below prove the two bands actually DISCRIMINATE (different confidence, different
# provenance string), not just that both are present -- a control that never crosses the boundary
# between "unresolved receiver" and "in-file-confirmed receiver" would prove nothing.
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_java_references_and_calls_unconfirmed_receiver_is_demoted(tmp_path: Path) -> None:
    """`h.helper()`'s receiver `h` is declared `Object` in this file, and no class in this file
    declares a method named `helper` -- there is nothing to confirm against, so every bucket
    entry must carry the DEMOTED band."""
    java_file = tmp_path / "Unresolved.java"
    java_file.write_text(
        "class Unresolved {\n"
        "    void run() {\n"
        "        Object h = null;\n"
        "        h.helper();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "helper", parser=parser)

    assert len(calls) == 1
    assert len(references) == 1
    for entry in (*references, *calls):
        assert entry["resolution_provenance"] == ["java-name-heuristic"]
        assert entry["resolution_confidence"] == pytest.approx(0.6)


@pytest.mark.requires_grammar
def test_java_references_and_calls_infile_receiver_type_confirms_higher_band(
    tmp_path: Path,
) -> None:
    """`t.doWork()`'s receiver `t` is declared `Target` in THIS file, and `Target` (also declared
    in this file) directly declares a `doWork` method -- both facts readable from the same AST,
    so this call must confirm at the HIGHER band with a DIFFERENT provenance string than the
    demoted case above. The second fixture (`o.doWork()`, receiver type `Object`) is the
    discriminating CONTROL in the same file/query shape: it must land in the demoted band, proving
    the two bands actually diverge rather than both landing on one value by construction.
    """
    java_file = tmp_path / "Confirmed.java"
    java_file.write_text(
        "class Target {\n"
        "    void doWork() {\n"
        "    }\n"
        "}\n"
        "class Caller {\n"
        "    void run() {\n"
        "        Target t = new Target();\n"
        "        Object o = null;\n"
        "        t.doWork();\n"
        "        o.doWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "doWork", parser=parser)

    confirmed_calls = [c for c in calls if c["line"] == 9]
    demoted_calls = [c for c in calls if c["line"] == 10]
    assert len(confirmed_calls) == 1
    assert len(demoted_calls) == 1
    confirmed = confirmed_calls[0]
    demoted = demoted_calls[0]

    assert confirmed["resolution_provenance"] == ["java-infile-type-confirmation"]
    assert confirmed["resolution_confidence"] == pytest.approx(0.9)
    assert demoted["resolution_provenance"] == ["java-name-heuristic"]
    assert demoted["resolution_confidence"] == pytest.approx(0.6)

    # The discriminating control itself: confirmed must be STRICTLY higher, with a DIFFERENT
    # provenance string -- both arms must actually diverge, not merely both be present.
    assert confirmed["resolution_confidence"] > demoted["resolution_confidence"]
    assert confirmed["resolution_provenance"] != demoted["resolution_provenance"]

    confirmed_refs = [r for r in references if r["line"] == 9]
    assert confirmed_refs[0]["resolution_provenance"] == ["java-infile-type-confirmation"]
    assert confirmed_refs[0]["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_java_references_and_calls_this_receiver_confirms_higher_band(tmp_path: Path) -> None:
    """`this.doWork()` inside the SAME class that declares `doWork` must also confirm -- the
    enclosing type IS the receiver's static type by definition, no variable declaration needed."""
    java_file = tmp_path / "ThisConfirmed.java"
    java_file.write_text(
        "class Target {\n"
        "    void doWork() {\n"
        "    }\n"
        "    void run() {\n"
        "        this.doWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    _, calls = lang_java.java_references_and_calls(java_file, "doWork", parser=parser)

    this_call = next(c for c in calls if c["line"] == 5)
    assert this_call["resolution_provenance"] == ["java-infile-type-confirmation"]
    assert this_call["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_java_references_and_calls_type_reference(tmp_path: Path) -> None:
    java_file = tmp_path / "TypeRef.java"
    java_file.write_text(
        "class TypeRef extends Foo {\n    Foo field;\n}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "Foo", parser=parser)

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "type", 1),
        ("reference", "type", 2),
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_java_references_and_calls_field_access_non_call_member(tmp_path: Path) -> None:
    java_file = tmp_path / "FieldAccess.java"
    java_file.write_text(
        "class FieldAccess {\n"
        "    void run() {\n"
        "        Widget w = null;\n"
        "        int y = w.count;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "count", parser=parser)

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "field", 4)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_java_references_and_calls_excludes_same_name_declaration(tmp_path: Path) -> None:
    java_file = tmp_path / "Decl.java"
    java_file.write_text(
        "class Decl {\n    int count;\n    int read() {\n        return count;\n    }\n}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "count", parser=parser)

    # `int count;` (line 2) is the DECLARATION -- must not appear. `return count;` (line 4) is
    # the one real reference.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "value", 4)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_java_references_and_calls_ignores_string_literal_and_comment_occurrences(
    tmp_path: Path,
) -> None:
    java_file = tmp_path / "Noise.java"
    java_file.write_text(
        "class Noise {\n"
        "    void run() {\n"
        '        String s = "Helper";\n'
        "        // Helper mentioned only in a comment\n"
        "        Helper h = null;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    references, calls = lang_java.java_references_and_calls(java_file, "Helper", parser=parser)

    # Only the REAL type reference on line 5 counts -- the string literal (line 3) and the
    # comment (line 4) must never match; a text/regex scan could not make this distinction.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "type", 5)]
    assert calls == []


@pytest.mark.requires_grammar
def test_java_references_and_calls_defeats_regex_fallback(tmp_path: Path) -> None:
    """AST-only distinction the regex fallback provably cannot satisfy: for a REAL call site,
    ``_regex_references_and_calls`` (Java's actual current fallback) finds nothing at all, while
    the AST extractor finds the real call precisely."""
    java_file = tmp_path / "Defeats.java"
    java_file.write_text(
        "class Defeats {\n    void run() {\n        doWork();\n    }\n}\n",
        encoding="utf-8",
    )
    parser = _java_parser_or_skip()

    regex_references, regex_calls = repo_map._regex_references_and_calls(java_file, "doWork")
    ast_references, ast_calls = lang_java.java_references_and_calls(
        java_file, "doWork", parser=parser
    )

    assert regex_references == []
    assert regex_calls == []
    assert ast_references and ast_references[0]["ref_kind"] == "call"
    assert ast_calls and ast_calls[0]["ref_kind"] == "call"


def test_java_references_and_calls_grammar_absent_returns_empty_not_crash(tmp_path: Path) -> None:
    java_file = tmp_path / "NoGrammar.java"
    java_file.write_text(
        "class NoGrammar {\n    void run() {\n        doWork();\n    }\n}\n",
        encoding="utf-8",
    )

    references, calls = lang_java.java_references_and_calls(java_file, "doWork", parser=None)

    assert references == []
    assert calls == []


def test_java_references_and_calls_returns_empty_for_non_java_suffix(tmp_path: Path) -> None:
    not_java = tmp_path / "Widget.txt"
    not_java.write_text("class Widget {}\n", encoding="utf-8")
    parser = repo_map._java_parser()

    references, calls = lang_java.java_references_and_calls(not_java, "Widget", parser=parser)

    assert references == []
    assert calls == []


@pytest.mark.requires_grammar
def test_java_cross_file_call_site_found_via_literal_prefilter_but_unconfirmed(
    tmp_path: Path,
) -> None:
    """Task 10A CHANGES this behavior, deliberately: prior to Task 10A, a caller of a Java method
    living in a SEPARATE Java file was never discoverable at all, because Java's
    ``references_and_calls`` was ``None`` (silently empty via the always-empty regex fallback --
    see the base-green characterization test above). Now that Java has a real in-file AST
    extractor, ``build_symbol_callers``'s shared ``_should_scan_for_symbol_callers`` literal-text
    prefilter (``_file_may_contain_literal_symbol`` -- the SAME mechanism every registered
    language already relies on for a same-package/no-import call site) admits Main.java into the
    scan because it literally contains the text "getCount", and the AST walk then correctly finds
    the real `w.getCount()` call site.

    This is still NOT true cross-file resolution, though: Java has no import-based CONFIRMATION
    wired (``import_update_target`` stays ``None``, Task 11A). The receiver `w`'s declared type
    (``Widget``) IS readable from THIS file (``Main.java``'s own ``local_variable_declaration``),
    but the method `getCount` it calls is declared in a DIFFERENT file (``Widget.java``) -- the
    in-file receiver-type confirmation ``java_references_and_calls`` now performs (see its module
    docstring) requires BOTH facts to live in the same file, so this case still demotes: the call
    row carries ``resolution_provenance=["java-name-heuristic"]`` at the demoted confidence, not
    the confirmed band. The honesty-floor ``resolution_gaps`` entry for Java is still emitted too
    -- a caller of a same-named-but-unrelated ``getCount`` elsewhere in the repo would be
    indistinguishable from a real one until Task 11A ships package/source-root resolution.
    """
    root = tmp_path
    (root / "Widget.java").write_text(
        "public class Widget {\n    public int getCount() {\n        return 1;\n    }\n}\n",
        encoding="utf-8",
    )
    (root / "Main.java").write_text(
        "public class Main {\n"
        "    public static void main(String[] args) {\n"
        "        Widget w = new Widget();\n"
        "        System.out.println(w.getCount());\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("getCount", root)

    assert not payload.get("no_match")
    caller_files = {str(Path(c["file"]).name) for c in payload["callers"]}
    assert caller_files == {"Main.java"}

    # Every row MUST carry the demoted band. This previously asserted the OPPOSITE -- that
    # `resolution_provenance` was ABSENT -- which pinned the honesty gap as correct behaviour.
    # Java has no import/type resolver, so `w.getCount()` matches on the method NAME alone;
    # nothing proves `w` is a `Widget`, and a same-name method on an unrelated class matches
    # identically. Emitting such a row with the same shape as a RESOLVED hit (e.g. Go's
    # `go-import-resolution` at 0.95) lets a consumer read a guess as a confirmation. The
    # per-language `resolution_gaps` entry discloses the missing resolver, but per LANGUAGE --
    # this asserts the disclosure survives per ROW, which is what anything ranking or filtering
    # individual callers actually reads.
    for caller in payload["callers"]:
        assert caller["resolution_provenance"] == ["java-name-heuristic"], caller
        assert caller["resolution_confidence"] == 0.6, caller

    # CONTROL: the band must be DEMOTED, not merely present. A fix that stamped 0.95 here would
    # satisfy a bare presence check while making the output MORE misleading than before.
    assert all(c["resolution_confidence"] < 0.95 for c in payload["callers"])
    gaps = payload["resolution_gaps"]
    java_gap = next(gap for gap in gaps if gap["language"] == "java")
    assert "reverse-import" in java_gap["reason"]
