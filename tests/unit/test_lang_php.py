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
  "grammar-missing" (never a silent regex/heuristic swap) when the grammar is absent. Task 10C
  wired ``references_and_calls`` (in-file AST reference/call extraction); the remaining
  cross-file caller-graph callables stay explicitly ``None`` -- DEFERRED scope, not an oversight
  (see ``lang_php.py``'s module docstring).
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
from typing import Any

import pytest

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
    # Task 10C wired in-file references_and_calls (lang_php.php_references_and_calls). F7 Task 11
    # wave 2b wires file_imports_symbol_from_definition (regex-based `use`/namespace resolver).
    # The remaining cross-file caller-graph fields stay DEFERRED (see lang_php.py's module
    # docstring) -- pin this explicitly so a future PR that wires one of these in must
    # consciously update this test rather than silently drift.
    assert spec.references_and_calls is not None
    assert spec.provider_alias_calls is None
    assert spec.file_imports_symbol_from_definition is (
        lang_php.php_file_imports_symbol_from_definition
    )
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


@pytest.mark.requires_grammar
def test_php_imports_and_symbols_extracts_qualified_backslash_imports(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    imports, _symbols = lang_php.php_imports_and_symbols(php_file)

    # Backslash preserved as-written (PHP's namespace separator, not a dot); alias ("as S")
    # dropped, matching Python's dotted node.module role (the SOURCE path, not a local name).
    assert imports == ["App\\Contracts\\Named", "App\\Utils\\Str"]


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
def test_php_parser_symbol_sources_finds_both_greet_declarations(tmp_path: Path) -> None:
    php_file = _write_php_fixture(tmp_path)

    sources = lang_php.php_parser_symbol_sources(php_file, "greet")

    assert len(sources) == 2
    assert {item["start_line"] for item in sources} == {10, 38}
    assert all(item["kind"] == "function" for item in sources)
    impl = next(item for item in sources if item["start_line"] == 38)
    assert "S::upper" in impl["source"]


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
def test_defs_finds_class_with_tree_sitter_provenance(tmp_path: Path) -> None:
    _write_php_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Widget", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 1
    definition = payload["definitions"][0]
    assert definition["kind"] == "class"
    assert definition["provenance"] == "tree-sitter"
    assert definition["file"].replace("\\", "/").endswith("Widget.php")


@pytest.mark.requires_grammar
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


@pytest.mark.requires_grammar
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


# Task 10C pre-fix RED arms: promote PHP from the foundational tier to parser-backed
# refs/callers, following Java (Task 10A, PR #927) and C# (Task 10B, PR #928).
#
# Both nodes MUST fail before any implementation, and each must fail for a BEHAVIOUR-SPECIFIC
# reason. An ImportError or NameError here would be a false red -- it proves a symbol is
# missing, not that the behaviour is absent.


def test_php_references_and_calls_is_registered_non_none() -> None:
    """Task 10C RED: PHP must register a real ``references_and_calls`` extractor.

    Pre-fix this is ``None``, so ``_references_and_calls_for_path`` falls through to
    ``_regex_references_and_calls``, which returns ``([], [])`` for any suffix outside
    ``_JS_TS_SUFFIXES | _RUST_SUFFIXES`` -- including ``.php``. PHP's "regex fallback" is
    therefore not a text heuristic over PHP source; it is an unconditional empty result.
    """
    spec = lang_registry.LANGUAGE_REGISTRY["php"]
    assert spec.references_and_calls is not None


def test_php_moves_into_the_parser_backed_tier_descriptor() -> None:
    """Task 10C RED: the product's derived tier descriptor must list php as parser-backed.

    Asserted against the descriptor rather than a hardcoded string, so this node cannot be
    turned green by editing a doc. ``_symbol_navigation_descriptor`` partitions every
    registered LanguageSpec by exactly one boolean (``references_and_calls is not None``),
    so php lands in exactly one half -- never both, never neither.
    """
    descriptor = repo_map._symbol_navigation_descriptor()
    parser_backed, _, foundational = descriptor.partition("+")
    assert "php" in parser_backed, descriptor
    assert "php" not in foundational, descriptor


# ---------------------------------------------------------------------------
# Task 10C: php_references_and_calls AST-shape coverage (mirrors test_lang_java.py's /
# test_lang_csharp.py's own references_and_calls sections, adapted to PHP's own grammar shapes --
# see lang_php.py's module docstring "TASK 10C" section for the exact node-shape catalog: PHP has
# FIVE distinct call/access node types -- function_call_expression, member_call_expression,
# scoped_call_expression (Foo::bar() / self::/static::/parent::), object_creation_expression, and
# member_access_expression / scoped_property_access_expression for non-call member reads).
# ---------------------------------------------------------------------------


def _php_parser_or_skip() -> Any:
    parser = lang_php._php_parser()
    if parser is None:  # pragma: no cover - grammar always installed in this venv
        pytest.skip("tree_sitter_php grammar not installed")
    return parser


@pytest.mark.requires_grammar
def test_php_references_and_calls_provenance_is_parser_backed(tmp_path: Path) -> None:
    _write_php_fixture(tmp_path)

    references, calls = repo_map._references_and_calls_for_path(
        tmp_path / "Widget.php", "Named", tmp_path
    )

    assert references, "expected the tree-sitter extractor to find `Named` references"
    assert calls == []
    assert repo_map._symbol_navigation_provenance_for_path(str(tmp_path / "Widget.php")) == (
        "tree-sitter"
    )


@pytest.mark.requires_grammar
def test_php_references_and_calls_function_call_expression(tmp_path: Path) -> None:
    php_file = tmp_path / "Plain.php"
    php_file.write_text(
        "<?php\nfunction helper() {\n    return 1;\n}\nhelper();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "helper")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "call", 5)]
    assert [(c["kind"], c["ref_kind"], c["line"]) for c in calls] == [("call", "call", 5)]


@pytest.mark.requires_grammar
def test_php_references_and_calls_object_creation_expression_constructor(
    tmp_path: Path,
) -> None:
    php_file = tmp_path / "Ctor.php"
    php_file.write_text(
        "<?php\nclass Foo {}\n$f = new Foo();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "Foo")

    # "Foo" appears twice: the class DECLARATION (excluded, own name) and the `new Foo()`
    # constructor call (ref_kind "constructor").
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "constructor", 3)
    ]
    assert [c["ref_kind"] for c in calls] == ["constructor"]


@pytest.mark.requires_grammar
def test_php_references_and_calls_member_call_expression(tmp_path: Path) -> None:
    php_file = tmp_path / "MemberCall.php"
    php_file.write_text(
        "<?php\n$foo = new Foo();\n$foo->bar();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "bar")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "call", 3)]
    assert [(c["kind"], c["ref_kind"], c["line"]) for c in calls] == [("call", "call", 3)]


@pytest.mark.requires_grammar
def test_php_references_and_calls_scoped_call_expression(tmp_path: Path) -> None:
    php_file = tmp_path / "ScopedCall.php"
    php_file.write_text(
        "<?php\nclass Foo {\n    public static function bar() {}\n}\nFoo::bar();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    _references, calls = lang_php.php_references_and_calls(php_file, "bar")

    scoped_call = [c for c in calls if c["line"] == 5]
    assert len(scoped_call) == 1
    assert scoped_call[0]["ref_kind"] == "call"


@pytest.mark.requires_grammar
def test_php_references_and_calls_member_access_expression_non_call(tmp_path: Path) -> None:
    php_file = tmp_path / "FieldAccess.php"
    php_file.write_text(
        "<?php\n$foo = new Foo();\n$y = $foo->baz;\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "baz")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "field", 3)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_php_references_and_calls_scoped_property_access_expression(tmp_path: Path) -> None:
    php_file = tmp_path / "StaticProp.php"
    php_file.write_text(
        "<?php\nclass Foo {\n    public static $staticProp;\n}\necho Foo::$staticProp;\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "staticProp")

    static_refs = [r for r in references if r["line"] == 5]
    assert len(static_refs) == 1
    assert static_refs[0]["ref_kind"] == "field"
    assert calls == []


@pytest.mark.requires_grammar
def test_php_references_and_calls_type_reference(tmp_path: Path) -> None:
    php_file = tmp_path / "TypeRef.php"
    php_file.write_text(
        "<?php\nclass TypeRef extends Foo {\n    private Foo $field;\n}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "Foo")

    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "type", 2),
        ("reference", "type", 3),
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_php_references_and_calls_excludes_same_name_declaration(tmp_path: Path) -> None:
    php_file = tmp_path / "Decl.php"
    php_file.write_text(
        "<?php\nclass Decl {\n    private $count;\n    public function read() {\n"
        "        return $this->count;\n    }\n}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "count")

    # `private $count;` (line 3) is the DECLARATION -- must not appear. `$this->count` (line 5)
    # is the one real reference.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [
        ("reference", "field", 5)
    ]
    assert calls == []


@pytest.mark.requires_grammar
def test_php_references_and_calls_ignores_string_literal_and_comment_occurrences(
    tmp_path: Path,
) -> None:
    php_file = tmp_path / "Noise.php"
    php_file.write_text(
        '<?php\n$s = "Helper";\n// Helper mentioned only in a comment\nclass C extends Helper {}\n',
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "Helper")

    # Only the REAL type reference on line 4 counts -- the string literal (line 2) and the
    # comment (line 3) must never match; a text/regex scan could not make this distinction.
    assert [(r["kind"], r["ref_kind"], r["line"]) for r in references] == [("reference", "type", 4)]
    assert calls == []


@pytest.mark.requires_grammar
def test_php_references_and_calls_defeats_regex_fallback(tmp_path: Path) -> None:
    """AST-only distinction the regex fallback provably cannot satisfy: for a REAL call site,
    ``_regex_references_and_calls`` (PHP's pre-Task-10C fallback) finds nothing at all, while the
    AST extractor finds the real call precisely."""
    php_file = tmp_path / "Defeats.php"
    php_file.write_text(
        "<?php\nfunction doWork() {}\ndoWork();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    regex_references, regex_calls = repo_map._regex_references_and_calls(php_file, "doWork")
    ast_references, ast_calls = lang_php.php_references_and_calls(php_file, "doWork")

    assert regex_references == []
    assert regex_calls == []
    assert ast_references and ast_references[0]["ref_kind"] == "call"
    assert ast_calls and ast_calls[0]["ref_kind"] == "call"


def test_php_references_and_calls_grammar_absent_returns_empty_not_crash(
    tmp_path: Path, monkeypatch
) -> None:
    php_file = tmp_path / "NoGrammar.php"
    php_file.write_text("<?php\nfunction doWork() {}\ndoWork();\n", encoding="utf-8")
    monkeypatch.setattr(lang_php, "_php_parser", lambda: None)

    references, calls = lang_php.php_references_and_calls(php_file, "doWork")

    assert references == []
    assert calls == []


def test_php_references_and_calls_returns_empty_for_non_php_suffix(tmp_path: Path) -> None:
    not_php = tmp_path / "Widget.txt"
    not_php.write_text("class Widget {}\n", encoding="utf-8")

    references, calls = lang_php.php_references_and_calls(not_php, "Widget")

    assert references == []
    assert calls == []


# ---------------------------------------------------------------------------
# The honest-confidence requirement: the two bands must actually DISCRIMINATE (different
# confidence, different provenance string) on a fixture with one confirmable call site and one
# unconfirmable one -- a control that never crosses the boundary between "unresolved receiver"
# and "in-file-confirmed receiver" would prove nothing (mirrors
# test_csharp_references_and_calls_infile_receiver_type_confirms_higher_band exactly, adapted to
# PHP's own grammar/provenance strings). See lang_php.py's module docstring for why PHP's
# confirmable population is honestly SMALLER than Java's/C#'s (dynamic typing): an untyped
# receiver can never confirm.
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_php_references_and_calls_unconfirmed_receiver_is_demoted(tmp_path: Path) -> None:
    """``$h->helper()``'s receiver ``$h`` has NO type hint anywhere in this file, and no class in
    this file declares a method named ``helper`` -- there is nothing to confirm against, so every
    bucket entry must carry the DEMOTED band."""
    php_file = tmp_path / "Unresolved.php"
    php_file.write_text(
        "<?php\nclass Unresolved {\n    public function run($h) {\n        $h->helper();\n"
        "    }\n}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "helper")

    assert len(calls) == 1
    assert len(references) == 1
    for entry in (*references, *calls):
        assert entry["resolution_provenance"] == ["php-name-heuristic"]
        assert entry["resolution_confidence"] == pytest.approx(0.6)


@pytest.mark.requires_grammar
def test_php_references_and_calls_infile_receiver_type_confirms_higher_band(
    tmp_path: Path,
) -> None:
    """``$t->doWork()``'s receiver ``$t`` is TYPE-HINTED as ``Target`` in THIS file, and
    ``Target`` (also declared in this file) directly declares a ``doWork`` method -- both facts
    readable from the same AST, so this call confirms at the HIGHER band with a DIFFERENT
    provenance string. ``$u->doWork()`` (an UNTYPED parameter -- PHP's honest default) is the
    discriminating CONTROL in the same file/query shape: it must land in the demoted band,
    proving the two bands actually diverge rather than both landing on one value by construction
    -- the load-bearing fixture for this task.
    """
    php_file = tmp_path / "Confirmed.php"
    php_file.write_text(
        "<?php\n"
        "class Target {\n"
        "    public function doWork() {}\n"
        "}\n"
        "class Caller {\n"
        "    public function run(Target $t, $u) {\n"
        "        $t->doWork();\n"
        "        $u->doWork();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    references, calls = lang_php.php_references_and_calls(php_file, "doWork")

    confirmed_calls = [c for c in calls if c["line"] == 7]
    demoted_calls = [c for c in calls if c["line"] == 8]
    assert len(confirmed_calls) == 1
    assert len(demoted_calls) == 1
    confirmed = confirmed_calls[0]
    demoted = demoted_calls[0]

    assert confirmed["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert confirmed["resolution_confidence"] == pytest.approx(0.9)
    assert demoted["resolution_provenance"] == ["php-name-heuristic"]
    assert demoted["resolution_confidence"] == pytest.approx(0.6)

    # The discriminating control itself: confirmed must be STRICTLY higher, with a DIFFERENT
    # provenance string -- both arms must actually diverge, not merely both be present. This is
    # the "honest-confidence" fixture: a single-band implementation wearing two names would fail
    # this exact assertion (both bands would read identically).
    assert confirmed["resolution_confidence"] > demoted["resolution_confidence"]
    assert confirmed["resolution_provenance"] != demoted["resolution_provenance"]

    confirmed_refs = [r for r in references if r["line"] == 7]
    assert confirmed_refs[0]["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert confirmed_refs[0]["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_php_references_and_calls_this_receiver_confirms_higher_band(tmp_path: Path) -> None:
    """``$this->doWork()`` inside the SAME class that declares ``doWork`` must also confirm --
    the enclosing type IS the receiver's static type by definition, no variable declaration
    needed."""
    php_file = tmp_path / "ThisConfirmed.php"
    php_file.write_text(
        "<?php\nclass Target {\n    public function doWork() {}\n"
        "    public function run() {\n        $this->doWork();\n    }\n}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    _, calls = lang_php.php_references_and_calls(php_file, "doWork")

    this_call = next(c for c in calls if c["line"] == 5)
    assert this_call["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert this_call["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_php_references_and_calls_scoped_call_confirms_via_literal_class_name(
    tmp_path: Path,
) -> None:
    """``Foo::bar()`` (a literal scoped call) confirms directly against a class declared in this
    file -- PHP-specific and stronger than the instance case: no variable type-tracking needed,
    the class name is given literally."""
    php_file = tmp_path / "ScopedConfirmed.php"
    php_file.write_text(
        "<?php\nclass Foo {\n    public static function bar() {}\n}\nFoo::bar();\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    _, calls = lang_php.php_references_and_calls(php_file, "bar")

    scoped_call = next(c for c in calls if c["line"] == 5)
    assert scoped_call["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert scoped_call["resolution_confidence"] == pytest.approx(0.9)


@pytest.mark.requires_grammar
def test_php_references_and_calls_self_and_static_scope_confirm_but_parent_stays_demoted(
    tmp_path: Path,
) -> None:
    """``self::bar()``/``static::bar()`` resolve via the enclosing type and confirm; ``parent::
    bar()`` can never confirm in-file (the parent class is not guaranteed present in this file) --
    the honest, documented gap from the module docstring's RESOLUTION CONFIDENCE section."""
    php_file = tmp_path / "RelativeScope.php"
    php_file.write_text(
        "<?php\n"
        "class Foo {\n"
        "    public static function bar() {}\n"
        "    public function selfCall() { self::bar(); }\n"
        "    public function staticCall() { static::bar(); }\n"
        "    public function parentCall() { parent::bar(); }\n"
        "}\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    _, calls = lang_php.php_references_and_calls(php_file, "bar")

    self_call = next(c for c in calls if c["line"] == 4)
    static_call = next(c for c in calls if c["line"] == 5)
    parent_call = next(c for c in calls if c["line"] == 6)

    assert self_call["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert self_call["resolution_confidence"] == pytest.approx(0.9)
    assert static_call["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert static_call["resolution_confidence"] == pytest.approx(0.9)
    # The honest gap: parent:: never confirms, even though self::/static:: (same file, same
    # syntactic shape) do -- proves the demoted band is not simply "anything with ::".
    assert parent_call["resolution_provenance"] == ["php-name-heuristic"]
    assert parent_call["resolution_confidence"] == pytest.approx(0.6)


@pytest.mark.requires_grammar
def test_php_references_and_calls_global_function_confirms_via_infile_declaration(
    tmp_path: Path,
) -> None:
    """A bare ``helper()`` call confirms only because ``function helper()`` is ALSO declared in
    this file -- the PHP-specific confirmation path with no Java/C# equivalent (neither language
    has a bare top-level function). ``strlen()`` (a PHP builtin, never declared in-file) is the
    discriminating control: it must stay demoted."""
    php_file = tmp_path / "GlobalFn.php"
    php_file.write_text(
        "<?php\nfunction helper() {\n    return 1;\n}\nhelper();\nstrlen('x');\n",
        encoding="utf-8",
    )
    _php_parser_or_skip()

    _, helper_calls = lang_php.php_references_and_calls(php_file, "helper")
    _, strlen_calls = lang_php.php_references_and_calls(php_file, "strlen")

    assert helper_calls[0]["resolution_provenance"] == ["php-infile-type-confirmation"]
    assert helper_calls[0]["resolution_confidence"] == pytest.approx(0.9)
    assert strlen_calls[0]["resolution_provenance"] == ["php-name-heuristic"]
    assert strlen_calls[0]["resolution_confidence"] == pytest.approx(0.6)
