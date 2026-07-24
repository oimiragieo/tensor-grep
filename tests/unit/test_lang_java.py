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

from tensor_grep.cli import agent_capsule, lang_registry, repo_map

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
    # Foundational tier only: the caller-graph fields are explicitly deferred.
    assert spec.references_and_calls is None
    assert spec.provider_alias_calls is None
    assert spec.file_imports_symbol_from_definition is None
    assert spec.import_update_target is None
    assert spec.prime_repo_context is None
    assert spec.classify_ref_kind is None


def test_target_and_provider_language_for_path_report_java() -> None:
    assert repo_map._target_language_for_path("Widget.java") == "java"
    assert repo_map._language_for_path("Widget.java") == "java"
    assert repo_map._provider_language_for_path("Widget.java") == "java"


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


def test_java_only_target_symbol_has_no_cross_file_callers_yet(tmp_path: Path) -> None:
    """Honesty check for the explicit deferral: a caller of a Java method living in a SEPARATE
    Java file is not yet discoverable (no cross-file reference/call resolver wired for Java) --
    this must degrade to an empty, non-crashing result, never a silent wrong answer."""
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
    assert payload["callers"] == []
    gaps = payload["resolution_gaps"]
    assert any(gap["language"] == "java" for gap in gaps)
