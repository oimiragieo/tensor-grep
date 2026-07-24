"""PATH A STAGE 1 -- PHP symbol graph (lang_php.py) tests.

Sibling of ``test_lang_go.py`` (Go's Stage 1 landing), scoped to what this PR actually ships:
DEFS + IMPORTS only. The fixture is a single PHP file --
``namespace`` + two ``use`` imports (one plain, one aliased) + an ``interface`` + a ``trait`` + an
``enum`` + a ``class`` (implementing the interface, using the trait, with a constructor and a
method) + a top-level ``function`` -- used to verify:

- ``php_imports_and_symbols``: every class-like declaration (class/interface/trait/enum) is
  extracted as kind "class"; every function-like declaration (function/method) is extracted as
  kind "function", with correct 1-based start/end lines; every ``use`` import is recorded as its
  raw backslash-qualified name (alias dropped, matching Python's dotted ``node.module`` role).
- ``php_parser_symbol_sources``: full source text lookup for the ``tg source`` command,
  including the case where two distinct declarations share a name (the interface's abstract
  ``greet`` stub and the class's concrete ``greet`` implementation).
- Registration + provenance: PHP's ``LanguageSpec`` reports "tree-sitter" when parsed,
  "grammar-missing" (never a silent regex/heuristic swap) when the grammar is absent, and the
  four cross-file caller-graph callables (``references_and_calls`` and friends) are explicitly
  ``None`` -- this is DEFERRED scope, not an oversight (see ``lang_php.py``'s module docstring).
- ``_target_language_for_path`` agrees with the registry (the "MOST-FORGOTTEN seam" ``lang_go.py``
  and ``test_lang_registry.py`` warn about -- miss it and the agent capsule's
  query-language-vs-target-language confidence cap silently misfires on PHP targets).
- ``build_repo_map``/``build_symbol_defs`` surface PHP symbols+imports end to end, with the same
  ``resolution_gaps`` honesty floor Go already established: a grammar-missing PHP file is a
  "fail-closed" gap, and even a grammar-PRESENT PHP file is an honest "import_resolution_only"
  gap (``import_update_target is None``) -- `tg callers`/`tg blast-radius` must never read PHP's
  currently-absent reverse-import resolution as a proven zero.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import lang_php, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: namespace + 2 `use` imports + interface + trait + enum + class (implements +
# trait-use + constructor + method) + a top-level function. Line numbers below are
# 1-based and load-bearing for the exact-line assertions -- see the module docstring.
# ---------------------------------------------------------------------------


def _write_php_fixture(root: Path) -> Path:
    php_file = root / "Widget.php"
    php_file.write_text(
        "<?php\n"  # 1
        "\n"  # 2
        "namespace App\\Models;\n"  # 3
        "\n"  # 4
        "use App\\Contracts\\Named;\n"  # 5
        "use App\\Utils\\Str as S;\n"  # 6
        "\n"  # 7
        "interface Greetable\n"  # 8
        "{\n"  # 9
        "    public function greet(): string;\n"  # 10
        "}\n"  # 11
        "\n"  # 12
        "trait Loggable\n"  # 13
        "{\n"  # 14
        "    public function log(string $message): void\n"  # 15
        "    {\n"  # 16
        "        echo $message;\n"  # 17
        "    }\n"  # 18
        "}\n"  # 19
        "\n"  # 20
        "enum Status\n"  # 21
        "{\n"  # 22
        "    case Active;\n"  # 23
        "    case Inactive;\n"  # 24
        "}\n"  # 25
        "\n"  # 26
        "class Widget implements Greetable\n"  # 27
        "{\n"  # 28
        "    use Loggable;\n"  # 29
        "\n"  # 30
        "    private string $label;\n"  # 31
        "\n"  # 32
        "    public function __construct(string $label)\n"  # 33
        "    {\n"  # 34
        "        $this->label = $label;\n"  # 35
        "    }\n"  # 36
        "\n"  # 37
        "    public function greet(): string\n"  # 38
        "    {\n"  # 39
        '        return "hi " . S::upper($this->label);\n'  # 40
        "    }\n"  # 41
        "}\n"  # 42
        "\n"  # 43
        "function make_widget(string $label): Widget\n"  # 44
        "{\n"  # 45
        "    return new Widget($label);\n"  # 46
        "}\n",  # 47
        encoding="utf-8",
    )
    return php_file


# ---------------------------------------------------------------------------
# Registration + provenance
# ---------------------------------------------------------------------------


def test_php_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["php"]
    assert spec.suffixes == frozenset({".php"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (Stage 1 trap, like Go): never "regex-heuristic"/"heuristic" -- PHP has no
    # fallback when the grammar is missing.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None
    # DEFERRED scope (see lang_php.py's module docstring): the cross-file caller-graph is a
    # follow-up, not shipped here. Pin this explicitly so a future PR that wires one of these in
    # must consciously update this test rather than silently drift.
    assert spec.references_and_calls is None
    assert spec.provider_alias_calls is None
    assert spec.file_imports_symbol_from_definition is None
    assert spec.import_update_target is None
    assert spec.prime_repo_context is None
    assert spec.classify_ref_kind is None


def test_target_language_for_path_reports_php() -> None:
    assert repo_map._target_language_for_path("src/Widget.php") == "php"
    assert repo_map._language_for_path("src/Widget.php") == "php"
    assert repo_map._provider_language_for_path("src/Widget.php") == "php"


# ---------------------------------------------------------------------------
# php_imports_and_symbols: direct unit coverage (kinds/lines + qualified `\`-names)
# ---------------------------------------------------------------------------


def test_php_imports_and_symbols_extracts_qualified_backslash_imports(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    imports, _symbols = lang_php.php_imports_and_symbols(php_file)

    # Backslash preserved as-written (PHP's namespace separator, not a dot); alias ("as S")
    # dropped, matching Python's dotted node.module role (the SOURCE path, not a local name).
    assert imports == ["App\\Contracts\\Named", "App\\Utils\\Str"]


def test_php_imports_and_symbols_extracts_all_def_kinds_with_correct_lines(
    tmp_path: Path,
) -> None:
    php_file = _write_php_fixture(tmp_path)

    _imports, symbols = lang_php.php_imports_and_symbols(php_file)

    actual = [
        (item["name"], item["kind"], item["start_line"], item["end_line"]) for item in symbols
    ]
    expected = [
        ("Greetable", "class", 8, 11),
        ("greet", "function", 10, 10),
        ("Loggable", "class", 13, 19),
        ("log", "function", 15, 18),
        ("Status", "class", 21, 25),
        ("Widget", "class", 27, 42),
        ("__construct", "function", 33, 36),
        ("greet", "function", 38, 41),
        ("make_widget", "function", 44, 47),
    ]
    assert actual == expected
    # Every symbol carries this file's path and the line-number/start_line agreement
    # `_symbol_record` guarantees for every other language.
    for item in symbols:
        assert item["file"] == str(php_file)
        assert item["line"] == item["start_line"]


def test_php_imports_and_symbols_non_php_suffix_returns_empty(tmp_path: Path) -> None:
    other_file = tmp_path / "widget.txt"
    other_file.write_text("<?php class Widget {}\n", encoding="utf-8")

    assert lang_php.php_imports_and_symbols(other_file) == ([], [])


def test_php_imports_and_symbols_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.php"

    assert lang_php.php_imports_and_symbols(missing) == ([], [])


def test_php_imports_and_symbols_grammar_absent_returns_empty(tmp_path: Path, monkeypatch) -> None:
    php_file = _write_php_fixture(tmp_path)
    monkeypatch.setattr(lang_php, "_php_parser", lambda: None)

    assert lang_php.php_imports_and_symbols(php_file) == ([], [])


def test_php_trait_use_in_class_body_is_not_mistaken_for_a_namespace_import(
    tmp_path: Path,
) -> None:
    """`use Loggable;` inside the class body is PHP's trait-use statement (grammar node type
    `use_declaration`), a different construct from a namespace import (`namespace_use_clause`
    nested under `namespace_use_declaration`) -- verified directly against the real grammar
    before writing the extractor. Only the two real namespace imports must show up."""
    php_file = _write_php_fixture(tmp_path)

    imports, _symbols = lang_php.php_imports_and_symbols(php_file)

    assert "Loggable" not in imports
    assert len(imports) == 2


# ---------------------------------------------------------------------------
# #74-follow-up: tg imports (php_imports_with_lines / build_file_imports) -- foundational tier,
# mirrors test_lang_java.py's test_file_imports_returns_java_import_statements_with_lines.
# ---------------------------------------------------------------------------


def test_php_imports_with_lines_extracts_use_statements_with_lines(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    entries = lang_php.php_imports_with_lines(php_file)

    modules = {entry["module"]: entry["line"] for entry in entries}
    assert modules == {"App\\Contracts\\Named": 5, "App\\Utils\\Str": 6}


def test_php_imports_with_lines_non_php_suffix_returns_empty(tmp_path: Path) -> None:
    not_php = tmp_path / "Widget.txt"
    not_php.write_text("use App\\Contracts\\Named;\n", encoding="utf-8")

    assert lang_php.php_imports_with_lines(not_php) == []


def test_php_imports_with_lines_grammar_absent_returns_empty(tmp_path: Path, monkeypatch) -> None:
    php_file = _write_php_fixture(tmp_path)
    monkeypatch.setattr(lang_php, "_php_parser", lambda: None)

    assert lang_php.php_imports_with_lines(php_file) == []


def test_file_imports_returns_php_use_statements_with_lines(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    payload = repo_map.build_file_imports(php_file)

    assert payload["result_incomplete"] is False
    modules = {entry["module"]: entry["line"] for entry in payload["imports"]}
    assert modules == {"App\\Contracts\\Named": 5, "App\\Utils\\Str": 6}
    # Foundational tier: raw import statements are real, but resolving them to a specific file
    # (PHP needs a PSR-4/composer.json autoload-map reader that does not exist yet) is deferred --
    # every row must be unresolved and never presumed external, matching the fail-closed contract.
    assert all(entry["resolved"] is None for entry in payload["imports"])
    assert all(entry["external"] is False for entry in payload["imports"])


# ---------------------------------------------------------------------------
# php_parser_symbol_sources: `tg source` companion
# ---------------------------------------------------------------------------


def test_php_parser_symbol_sources_finds_both_greet_declarations(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    sources = lang_php.php_parser_symbol_sources(php_file, "greet")

    assert len(sources) == 2
    assert {item["start_line"] for item in sources} == {10, 38}
    assert all(item["kind"] == "function" for item in sources)
    impl = next(item for item in sources if item["start_line"] == 38)
    assert "S::upper" in impl["source"]


def test_php_parser_symbol_sources_finds_top_level_function(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    sources = lang_php.php_parser_symbol_sources(php_file, "make_widget")

    assert len(sources) == 1
    assert sources[0]["kind"] == "function"
    assert sources[0]["start_line"] == 44
    assert "return new Widget($label);" in sources[0]["source"]


def test_php_parser_symbol_sources_no_match_returns_empty(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    assert lang_php.php_parser_symbol_sources(php_file, "NoSuchSymbol") == []


# ---------------------------------------------------------------------------
# Integration: build_repo_map / build_symbol_defs surface PHP end to end.
# ---------------------------------------------------------------------------


def test_build_repo_map_surfaces_php_symbols_and_imports(tmp_path: Path) -> None:
    _write_php_fixture(tmp_path)

    payload = repo_map.build_repo_map(tmp_path)

    symbol_names = {item["name"] for item in payload["symbols"]}
    assert {"Widget", "Greetable", "Loggable", "Status", "greet", "make_widget"} <= symbol_names

    php_import_entries = [
        entry for entry in payload["imports"] if str(entry["file"]).endswith("Widget.php")
    ]
    assert len(php_import_entries) == 1
    assert php_import_entries[0]["imports"] == ["App\\Contracts\\Named", "App\\Utils\\Str"]
    # Registry-driven provenance labeling (repo_map._symbol_navigation_provenance_for_path)
    # comes for free once PHP is registered -- was "heuristic" before this PR.
    assert php_import_entries[0]["provenance"] == "tree-sitter"


def test_defs_finds_class_with_tree_sitter_provenance(tmp_path: Path) -> None:
    _write_php_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Widget", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 1
    definition = payload["definitions"][0]
    assert definition["kind"] == "class"
    assert definition["provenance"] == "tree-sitter"
    assert definition["file"].replace("\\", "/").endswith("Widget.php")


def test_defs_finds_interface_trait_and_enum_as_class_kind(tmp_path: Path) -> None:
    _write_php_fixture(tmp_path)

    for name in ("Greetable", "Loggable", "Status"):
        payload = repo_map.build_symbol_defs(name, tmp_path)
        assert not payload.get("no_match"), f"expected a definition for {name}"
        assert payload["definitions"][0]["kind"] == "class"


# ---------------------------------------------------------------------------
# resolution_gaps honesty floor (mirrors lang_go.py's audit #81 #4 precedent).
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_fail_closed_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_php_fixture(tmp_path)
    monkeypatch.setattr(lang_php, "_php_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("Widget", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    defs_gaps = defs_payload["resolution_gaps"]
    php_gap = next(gap for gap in defs_gaps if gap["language"] == "php")
    assert "fail-closed" in php_gap["reason"]
    assert "Coverage gap detected" in defs_payload["message"]


def test_grammar_present_still_flags_import_resolution_only_gap(tmp_path: Path) -> None:
    """audit #81 #4 parity: PHP's LanguageSpec sets import_update_target=None (the cross-file
    caller-graph is deferred), so _language_coverage_gaps_for_universe must flag that as an
    honest partial-capability gap even though the grammar IS installed and defs/`tg source`
    both work fine -- never read as resolution_gaps == [] (indistinguishable from "PHP has full
    capability")."""
    _write_php_fixture(tmp_path)
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")

    payload = repo_map.build_symbol_refs("Target", tmp_path)

    assert not payload.get("no_match")
    resolution_gaps = payload["resolution_gaps"]
    php_gaps = [gap for gap in resolution_gaps if gap["language"] == "php"]
    assert len(php_gaps) == 1
    assert php_gaps[0]["files_affected"] >= 1
    assert "reverse-import" in php_gaps[0]["reason"]
    assert "fail-closed" not in php_gaps[0]["reason"]
