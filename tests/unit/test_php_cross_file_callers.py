"""F7 Task 11 wave 2b -- PHP cross-file caller resolution (namespace / `use` evidence).

Product observable (design exit criterion): a caller in a DIFFERENT file appears in
``blast_radius_floor`` AND is BOUND to the selected definition via `use`/namespace confirmation --
not a bare literal-name match.

MUTATION-PROOF (run after GREEN): the two constant-collision mutations
``lang_java.py``'s docstring warns are non-discriminating for Java also do not discriminate here,
for the identical reason (module-attribute mutation, module-attribute-read assertion -- both would
move together). Assertions below are pinned to LITERALS (0.9 /
"php-use-namespace-confirmation"), never to ``lang_php._PHP_CONFIRMED_CONFIDENCE`` /
``_PHP_CROSS_FILE_CONFIRMED_PROVENANCE``, so the test is an independent witness of the mechanism,
not of the constant. Named tests that MUST go red when
``_php_type_resolves_into_definition_dirs`` is forced to ``return False`` unconditionally:
    - ``test_php_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
    - ``test_php_cross_file_call_row_uses_confirmed_use_namespace_band``
(``test_php_file_imports_symbol_from_definition_accepts_imported_definition`` stays green -- it
exercises the SEPARATE regex-based ``php_file_imports_symbol_from_definition`` boolean gate, not
the AST-walk confirmation band.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import lang_php, lang_registry, repo_map
from tensor_grep.cli.prepare_service import _build_prepare_blast_radius_floor


def _psr4_php_fixture(root: Path) -> dict[str, Path]:
    """Two-package PHP layout: Caller imports App\\Lib\\Foo; decoy App\\Other\\Foo is unimported.

    Selected definition = Lib Foo::getCount. Cross-file Caller.php must bind via `use` + namespace
    evidence. Decoy Foo shares the method name but must not earn the confirmed band.
    """
    lib_foo = root / "lib" / "App" / "Lib" / "Foo.php"
    lib_foo.parent.mkdir(parents=True, exist_ok=True)
    lib_foo.write_text(
        "<?php\n"
        "namespace App\\Lib;\n"
        "\n"
        "class Foo {\n"
        "    public function getCount() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    decoy_foo = root / "decoy" / "App" / "Other" / "Foo.php"
    decoy_foo.parent.mkdir(parents=True, exist_ok=True)
    decoy_foo.write_text(
        "<?php\n"
        "namespace App\\Other;\n"
        "\n"
        "class Foo {\n"
        "    public function getCount() {\n"
        "        return 99;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    # NOTE: `$foo = new Foo();` (no type hint) would NOT confirm cross-file -- PHP has no local
    # variable type declarations, so a bare untyped local receiver can never earn the confirmed
    # band (documented, honest limitation -- see the module docstring's "RESOLUTION CONFIDENCE"
    # section). The confirmable population is a TYPED receiver: a typed parameter here.
    caller = root / "app" / "App" / "Http" / "Caller.php"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        "<?php\n"
        "namespace App\\Http;\n"
        "\n"
        "use App\\Lib\\Foo;\n"
        "\n"
        "class Caller {\n"
        "    public function useLib(Foo $foo) {\n"
        "        return $foo->getCount();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return {"lib_foo": lib_foo, "decoy_foo": decoy_foo, "caller": caller}


@pytest.mark.requires_grammar
def test_php_file_imports_symbol_from_definition_is_wired() -> None:
    """Import of the registry field must succeed; the assertion is the behaviour."""
    spec = lang_registry.LANGUAGE_REGISTRY["php"]
    assert spec.file_imports_symbol_from_definition is not None
    assert spec.file_imports_symbol_from_definition is (
        lang_php.php_file_imports_symbol_from_definition
    )


@pytest.mark.requires_grammar
def test_php_file_imports_symbol_from_definition_accepts_imported_definition(
    tmp_path: Path,
) -> None:
    paths = _psr4_php_fixture(tmp_path)
    source = paths["caller"].read_text(encoding="utf-8")

    assert lang_php.php_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "getCount",
        str(paths["lib_foo"]),
        tmp_path,
    )
    # Decoy: same class/method name, different namespace, not imported -- must NOT resolve.
    assert not lang_php.php_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "getCount",
        str(paths["decoy_foo"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_php_file_imports_demotes_when_definition_stem_does_not_match_declared_class(
    tmp_path: Path,
) -> None:
    """A definition file whose declared class name does not match its own filename stem must fail
    closed (False), never guess -- PSR-4's autoload contract is exactly this 1:1 requirement, so a
    mismatch means the mapping is genuinely unestablishable from this file alone."""
    definition = tmp_path / "NotFoo.php"
    definition.write_text(
        "<?php\nnamespace App\\Lib;\nclass Foo {\n    public function getCount() { return 1; }\n}\n",
        encoding="utf-8",
    )
    caller = tmp_path / "Caller.php"
    caller.write_text(
        "<?php\nuse App\\Lib\\Foo;\nclass Caller {\n"
        "    function use_it(Foo $f) { return $f->getCount(); }\n"
        "}\n",
        encoding="utf-8",
    )
    assert not lang_php.php_file_imports_symbol_from_definition(
        caller,
        caller.read_text(encoding="utf-8"),
        "getCount",
        str(definition),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_php_file_imports_symbol_from_definition_aliased_use_still_resolves(
    tmp_path: Path,
) -> None:
    """An aliased `use App\\Lib\\Foo as LibFoo;` still proves the FQN is imported -- an alias
    renames the LOCAL binding, not the imported identity."""
    paths = _psr4_php_fixture(tmp_path)
    caller = tmp_path / "Aliased.php"
    caller.write_text(
        "<?php\nnamespace App\\Http;\nuse App\\Lib\\Foo as LibFoo;\n"
        "class Aliased {\n    function m() { $f = new LibFoo(); return $f->getCount(); }\n}\n",
        encoding="utf-8",
    )
    assert lang_php.php_file_imports_symbol_from_definition(
        caller,
        caller.read_text(encoding="utf-8"),
        "getCount",
        str(paths["lib_foo"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_php_cross_file_call_row_uses_confirmed_use_namespace_band(tmp_path: Path) -> None:
    """Behaviour-specific RED: before this wave the cross-file call is demoted (0.6 /
    php-name-heuristic). After wave 2b, `use` + namespace-directory confirmation must lift
    Caller.php's call to the confirmed band.

    Expected values are LITERALS (0.9 / "php-use-namespace-confirmation"), not
    ``lang_php._PHP_CONFIRMED_CONFIDENCE`` / ``_PHP_CROSS_FILE_CONFIRMED_PROVENANCE``, on purpose
    -- see the module docstring's mutation-proof note.
    """
    _psr4_php_fixture(tmp_path)
    payload = repo_map.build_symbol_callers("getCount", tmp_path)

    assert not payload.get("no_match"), payload
    caller_rows = [row for row in payload["callers"] if Path(str(row["file"])).name == "Caller.php"]
    assert caller_rows, payload["callers"]
    for row in caller_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["php-use-namespace-confirmation"], row


@pytest.mark.requires_grammar
def test_php_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition(
    tmp_path: Path,
) -> None:
    """Exit criterion: cross-file Caller in blast_radius_floor, bound to selected lib Foo.

    Binding is proved by the confirmed use/namespace band on the caller rows that feed the floor.
    """
    paths = _psr4_php_fixture(tmp_path)
    rm = repo_map.build_repo_map(tmp_path)
    target = {
        "symbol": "getCount",
        "file": str(paths["lib_foo"]),
        "confidence": 0.9,
    }
    call_site_evidence = {
        "status": "skipped",
        "reason": "primary symbol was not explicitly requested by query",
    }
    floor, _deadline_partial = _build_prepare_blast_radius_floor(
        path=str(tmp_path),
        rm=rm,
        target=target,
        call_site_evidence=call_site_evidence,
        related_call_sites=[],
        deadline_monotonic=None,
    )

    assert floor.get("symbol") == "getCount", floor
    assert floor.get("source") == "supplementary_blast_radius", floor
    top = floor.get("top_callers") or []
    caller_names = {Path(str(row.get("file") or "")).name for row in top}
    assert "Caller.php" in caller_names, (floor, caller_names)

    radius = repo_map.build_symbol_blast_radius_from_map(rm, "getCount")
    bound_rows = [
        row for row in (radius.get("callers") or []) if Path(str(row["file"])).name == "Caller.php"
    ]
    assert bound_rows, radius
    for row in bound_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["php-use-namespace-confirmation"], row
        assert Path(str(row["file"])).resolve() == paths["caller"].resolve()


@pytest.mark.requires_grammar
def test_php_parent_scope_never_cross_file_confirms(tmp_path: Path) -> None:
    """`parent::` must stay demoted even when a use/namespace-visible directory match exists for
    the enclosing class's own name -- the parent class is not a `parent::` scope's own literal
    class name, so it never reaches the cross-file scope-confirmation path at all (only a literal
    `Foo::`/`self::`/`static::` scope can)."""
    paths = _psr4_php_fixture(tmp_path)
    caller = tmp_path / "ParentCaller.php"
    caller.write_text(
        "<?php\nnamespace App\\Http;\nuse App\\Lib\\Foo;\n"
        "class ParentCaller extends Foo {\n"
        "    function m() { return parent::getCount(); }\n"
        "}\n",
        encoding="utf-8",
    )
    references, calls = lang_php.php_references_and_calls(
        caller,
        "getCount",
        tmp_path,
        definition_dirs=frozenset({str(paths["lib_foo"].parent)}),
    )
    assert references and calls, (references, calls)
    for row in references + calls:
        assert row["resolution_confidence"] == 0.6, row
        assert row["resolution_provenance"] == ["php-name-heuristic"], row
