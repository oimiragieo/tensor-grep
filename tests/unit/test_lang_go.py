"""PATH A STAGE 1 -- Go symbol graph (lang_go.py) tests.

First language expansion beyond the original four (python/javascript/typescript/rust). The
fixture below is a tiny multi-package Go module (``go.mod`` + two packages + a ``_test.go``)
used to verify:

- ``defs``: an exported function is found with provenance "tree-sitter".
- ``refs``/``callers``: a cross-package call (``foo.Helper(...)``) resolves through the
  ``go.mod`` import context with ``ref_kind == "call"`` and
  ``resolution_provenance == ["go-import-resolution"]``; a same-package call (from the
  internal ``_test.go``) is also found.
- Type-position usage (``var w foo.Widget`` / ``foo.Widget{...}``) is classified
  ``ref_kind == "type"``.
- An UNEXPORTED symbol (``helper``) is only ever a caller from WITHIN its own package -- a
  cross-package file that happens to import the package can never legally call it, matching Go's
  own visibility rule, and tensor-grep's caller-scan must not fabricate a cross-package hit.
- A method call through an arbitrary (non-package-alias) receiver variable (``w.Write(...)``) is
  equifinal -- emitted, never dropped, but capped at ``resolution_confidence<=0.7`` with
  ``resolution_provenance == ["receiver-heuristic"]`` (never fabricated precision).
- Grammar-absent (monkeypatched ``lang_go._go_parser`` -> ``None``): fail-closed, zero
  fabricated rows, an honest ``resolution_gaps`` entry, and an honest (non-zero, non-silent)
  CLI exit code for a target that lives entirely in the grammar-missing language.
- The agent capsule reports ``primary_target_language == "go"`` and populates
  ``related_call_sites`` from the cross-package caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli import agent_capsule, lang_go, lang_registry, repo_map

# ---------------------------------------------------------------------------
# Fixture: go.mod + pkg/foo (exported Helper + unexported helper + Widget/Write) + pkg/bar
# (cross-package caller) + cmd/app (receiver-heuristic caller) + pkg/foo/foo_test.go
# (same-package caller).
# ---------------------------------------------------------------------------


def _write_go_fixture(root: Path) -> dict[str, Path]:
    (root / "go.mod").write_text("module example.com/widgetmod\n\ngo 1.21\n", encoding="utf-8")

    foo_dir = root / "pkg" / "foo"
    foo_dir.mkdir(parents=True)
    foo_go = foo_dir / "foo.go"
    foo_go.write_text(
        "package foo\n"
        "\n"
        "// Helper is exported: other packages may call it.\n"
        "func Helper(x int) int {\n"
        "\treturn x + helper()\n"
        "}\n"
        "\n"
        "// helper is unexported: only this package may call it.\n"
        "func helper() int {\n"
        "\treturn 1\n"
        "}\n"
        "\n"
        "// Widget is an exported struct with an exported method.\n"
        "type Widget struct {\n"
        "\tName string\n"
        "}\n"
        "\n"
        "// Write has an ambiguous (equifinal) receiver-call surface from a caller that only\n"
        "// knows the receiver variable's name, not its static type.\n"
        "func (w *Widget) Write(data []byte) (int, error) {\n"
        "\treturn len(data), nil\n"
        "}\n",
        encoding="utf-8",
    )
    foo_test_go = foo_dir / "foo_test.go"
    foo_test_go.write_text(
        "package foo\n"
        "\n"
        'import "testing"\n'
        "\n"
        # Deliberately NOT named TestHelper: a name containing the target symbol as a
        # substring skews the agent-capsule's file/symbol ranking (a query for "Helper" then
        # ties against this test function itself), which is a ranking-heuristic artifact
        # unrelated to what this fixture is testing (the actual call site inside the body).
        "func TestFooPackageBehavior(t *testing.T) {\n"
        "\tif Helper(1) != 2 {\n"
        '\t\tt.Fatal("unexpected")\n'
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )

    bar_dir = root / "pkg" / "bar"
    bar_dir.mkdir(parents=True)
    bar_go = bar_dir / "bar.go"
    bar_go.write_text(
        "package bar\n"
        "\n"
        "import (\n"
        '\t"example.com/widgetmod/pkg/foo"\n'
        ")\n"
        "\n"
        "// UseFoo cross-package-calls the exported Helper and references the exported Widget\n"
        "// type.\n"
        "func UseFoo() int {\n"
        "\tvar w foo.Widget\n"
        '\tw.Name = "test"\n'
        "\treturn foo.Helper(3)\n"
        "}\n",
        encoding="utf-8",
    )

    app_dir = root / "cmd" / "app"
    app_dir.mkdir(parents=True)
    main_go = app_dir / "main.go"
    main_go.write_text(
        "package main\n"
        "\n"
        "import (\n"
        '\t"fmt"\n'
        "\n"
        '\t"example.com/widgetmod/pkg/foo"\n'
        ")\n"
        "\n"
        "func main() {\n"
        '\tw := &foo.Widget{Name: "app"}\n'
        '\tn, err := w.Write([]byte("hello"))\n'
        "\tfmt.Println(n, err)\n"
        "}\n",
        encoding="utf-8",
    )

    return {
        "foo.go": foo_go,
        "foo_test.go": foo_test_go,
        "bar.go": bar_go,
        "main.go": main_go,
    }


# ---------------------------------------------------------------------------
# Registration + provenance
# ---------------------------------------------------------------------------


def test_go_is_registered_with_tree_sitter_provenance() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["go"]
    assert spec.suffixes == frozenset({".go"})
    assert spec.provenance_when_parsed == "tree-sitter"
    # Fail-closed (Stage 1 trap): never "regex-heuristic"/"heuristic" -- Go has no fallback.
    assert spec.provenance_when_missing == "grammar-missing"
    assert spec.parser_for_path is not None


def test_target_language_for_path_reports_go() -> None:
    assert repo_map._target_language_for_path("pkg/foo/foo.go") == "go"
    assert repo_map._language_for_path("pkg/foo/foo.go") == "go"
    assert repo_map._provider_language_for_path("pkg/foo/foo.go") == "go"


# ---------------------------------------------------------------------------
# defs
# ---------------------------------------------------------------------------


def test_defs_finds_exported_function_with_tree_sitter_provenance(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_defs("Helper", tmp_path)

    assert not payload.get("no_match")
    assert len(payload["definitions"]) == 1
    definition = payload["definitions"][0]
    assert definition["kind"] == "function"
    assert definition["provenance"] == "tree-sitter"
    assert definition["file"].replace("\\", "/").endswith("pkg/foo/foo.go")


def test_defs_finds_method_and_struct(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    write_payload = repo_map.build_symbol_defs("Write", tmp_path)
    widget_payload = repo_map.build_symbol_defs("Widget", tmp_path)

    assert not write_payload.get("no_match")
    assert write_payload["definitions"][0]["kind"] == "method"
    assert not widget_payload.get("no_match")
    assert widget_payload["definitions"][0]["kind"] == "struct"


# ---------------------------------------------------------------------------
# refs / callers: cross-package call resolution + type-position classification
# ---------------------------------------------------------------------------


def test_refs_cross_package_call_has_call_ref_kind_and_import_resolution(
    tmp_path: Path,
) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("Helper", tmp_path)

    assert payload["resolution_gaps"] == []
    cross_package_refs = [
        ref
        for ref in payload["references"]
        if ref["file"].replace("\\", "/").endswith("pkg/bar/bar.go")
    ]
    assert cross_package_refs, "expected a cross-package reference to Helper in bar.go"
    for ref in cross_package_refs:
        assert ref["ref_kind"] == "call"
        assert ref["resolution_provenance"] == ["go-import-resolution"]
        assert ref["resolution_confidence"] >= 0.9

    same_package_refs = [
        ref
        for ref in payload["references"]
        if ref["file"].replace("\\", "/").endswith("pkg/foo/foo_test.go")
    ]
    assert same_package_refs, "expected the internal _test.go call to Helper to be found"
    assert all(ref["ref_kind"] == "call" for ref in same_package_refs)


def test_callers_cross_package_ref_kind_call(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_callers("Helper", tmp_path)

    caller_files = {str(Path(c["file"])) for c in payload["callers"]}
    assert any(f.replace("\\", "/").endswith("pkg/bar/bar.go") for f in caller_files)
    assert any(f.replace("\\", "/").endswith("pkg/foo/foo_test.go") for f in caller_files)
    assert all(c["ref_kind"] == "call" for c in payload["callers"])


def test_type_position_reference_classified_as_type(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("Widget", tmp_path)

    type_refs = [ref for ref in payload["references"] if ref["ref_kind"] == "type"]
    assert type_refs, "expected at least one type-position reference to Widget"
    referencing_files = {Path(ref["file"]).name for ref in type_refs}
    assert "bar.go" in referencing_files or "main.go" in referencing_files


# ---------------------------------------------------------------------------
# Unexported symbol: cross-package is NOT a caller
# ---------------------------------------------------------------------------


def test_unexported_symbol_has_no_cross_package_caller(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_callers("helper", tmp_path)

    caller_files = {Path(c["file"]).name for c in payload["callers"]}
    # helper() is unexported -- only foo.go (same package, Helper calling helper()) may call
    # it. bar.go and main.go import the "foo" package but can never legally reference the
    # unexported symbol, and must NOT appear as callers.
    assert "bar.go" not in caller_files
    assert "main.go" not in caller_files
    assert caller_files, "expected the intra-package caller (Helper -> helper()) to be found"
    assert caller_files == {"foo.go"}


def test_go_file_imports_symbol_from_definition_exported_gate(tmp_path: Path) -> None:
    fixture = _write_go_fixture(tmp_path)
    foo_source = fixture["foo.go"].read_text(encoding="utf-8")
    bar_source = fixture["bar.go"].read_text(encoding="utf-8")

    # Cross-package + exported -> True (bar.go imports the package Helper's definition lives in).
    assert lang_go.go_file_imports_symbol_from_definition(
        fixture["bar.go"], bar_source, "Helper", str(fixture["foo.go"]), repo_root=tmp_path
    )
    # Cross-package + UNexported -> False, even though bar.go imports the same package.
    assert not lang_go.go_file_imports_symbol_from_definition(
        fixture["bar.go"], bar_source, "helper", str(fixture["foo.go"]), repo_root=tmp_path
    )
    # Same package (no import needed) -> True even for an unexported symbol.
    assert lang_go.go_file_imports_symbol_from_definition(
        fixture["foo.go"], foo_source, "helper", str(fixture["foo.go"]), repo_root=tmp_path
    )


# ---------------------------------------------------------------------------
# Equifinal receiver-method call: never dropped, never over-confident.
# ---------------------------------------------------------------------------


def test_receiver_method_call_is_equifinal_low_confidence(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = repo_map.build_symbol_callers("Write", tmp_path)

    assert payload["callers"], "expected the w.Write(...) call in main.go to be found"
    for caller in payload["callers"]:
        assert caller["ref_kind"] == "call"
        assert caller["resolution_provenance"] == ["receiver-heuristic"]
        assert caller["resolution_confidence"] <= 0.7


# ---------------------------------------------------------------------------
# Grammar-absent: fail-closed, resolution_gaps, honest exit code.
# ---------------------------------------------------------------------------


def test_grammar_absent_yields_no_fabricated_defs_and_resolution_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _write_go_fixture(tmp_path)
    # A python symbol elsewhere in the same repo so refs/callers has something REAL to find --
    # the resolution_gaps floor is about a Go file being an honestly-labeled BYSTANDER in the
    # scan universe, not about the query's own target living in the grammar-missing language
    # (that case short-circuits to no_match, exercised separately below).
    (tmp_path / "target.py").write_text("def Target():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)

    defs_payload = repo_map.build_symbol_defs("Helper", tmp_path)
    assert defs_payload.get("no_match") is True
    assert defs_payload["definitions"] == []
    # F13: `tg defs` used to return a bare no_match here -- indistinguishable from "Helper simply
    # does not exist anywhere". It must attach the same resolution_gaps honesty floor refs/callers
    # already get, plus a hint in the message.
    defs_gaps = defs_payload["resolution_gaps"]
    assert any(gap["language"] == "go" for gap in defs_gaps)
    defs_go_gap = next(gap for gap in defs_gaps if gap["language"] == "go")
    assert "fail-closed" in defs_go_gap["reason"]
    assert "Coverage gap detected" in defs_payload["message"]

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    assert not refs_payload.get("no_match")
    gaps = refs_payload["resolution_gaps"]
    assert any(gap["language"] == "go" for gap in gaps)
    go_gap = next(gap for gap in gaps if gap["language"] == "go")
    assert "fail-closed" in go_gap["reason"]
    assert go_gap["files_affected"] >= 1
    # F12: the remediation text must be HONEST for Go -- it has no regex fallback, so it must not
    # claim "falls back to plain literal-text/regex matching" (that claim is only true for a
    # genuinely UNREGISTERED language, e.g. .java).
    assert "fall back to plain literal-text/regex matching" not in go_gap["remediation"]
    assert (
        "NO rows" in go_gap["remediation"]
        or "no plain-text/regex fallback" in (go_gap["remediation"])
    )


def test_go_coverage_gap_remediation_is_honest_about_zero_rows() -> None:
    fail_closed_text = repo_map._language_coverage_gap_remediation("go", fail_closed=True)
    fallback_text = repo_map._language_coverage_gap_remediation("java", fail_closed=False)

    assert "fall back to plain literal-text/regex matching" not in fail_closed_text
    assert "fall back to plain literal-text/regex matching" in fallback_text


def test_grammar_absent_cli_exit_code_is_honest_not_found(tmp_path: Path, monkeypatch) -> None:
    """A Go-only target with the grammar missing must exit 1 (honest not-found) -- never a
    silent 0 (which would imply a fabricated/incorrect match) and never a crash."""
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    _write_go_fixture(tmp_path)
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "Helper"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Agent capsule
# ---------------------------------------------------------------------------


def test_agent_capsule_reports_go_target_language_and_call_sites(tmp_path: Path) -> None:
    _write_go_fixture(tmp_path)

    payload = agent_capsule.build_agent_capsule("Helper", tmp_path, include_blast_radius=True)

    assert payload["context_consistency"]["primary_target_language"] == "go"
    related_call_sites = payload.get("related_call_sites")
    assert related_call_sites
    assert any(Path(str(site.get("file", ""))).name == "bar.go" for site in related_call_sites)


# ---------------------------------------------------------------------------
# F8: generic receiver -> base type name (not "MyType[T]").
# ---------------------------------------------------------------------------


def test_generic_receiver_method_associates_with_base_type_name(tmp_path: Path) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/genmod\n\ngo 1.21\n", encoding="utf-8")
    box_go = tmp_path / "box.go"
    box_go.write_text(
        "package genmod\n"
        "\n"
        "type Box[T any] struct {\n"
        "\tValue T\n"
        "}\n"
        "\n"
        "func (b *Box[T]) Get() T {\n"
        "\treturn b.Value\n"
        "}\n"
        "\n"
        "func (b Box[T]) Peek() T {\n"
        "\treturn b.Value\n"
        "}\n",
        encoding="utf-8",
    )

    _, symbols = lang_go.go_imports_and_symbols(box_go)
    type_spec = next(s for s in symbols if s["name"] == "Box")
    pointer_receiver_method = next(s for s in symbols if s["name"] == "Get")
    value_receiver_method = next(s for s in symbols if s["name"] == "Peek")

    # Before the F8 fix these were "Box[T]" -- never matching the type_spec's plain "Box" name.
    assert pointer_receiver_method["receiver_type"] == "Box"
    assert value_receiver_method["receiver_type"] == "Box"
    assert pointer_receiver_method["receiver_type"] == type_spec["name"]


# ---------------------------------------------------------------------------
# F9: package-qualified const/var read -> "value", not "field".
# ---------------------------------------------------------------------------


def test_package_qualified_var_read_classified_as_value(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/cfgmod\n\ngo 1.21\n", encoding="utf-8")
    config_dir = tmp_path / "pkg" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.go").write_text(
        "package config\n\nvar DefaultTimeout = 30\n",
        encoding="utf-8",
    )
    app_dir = tmp_path / "cmd" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.go").write_text(
        "package main\n"
        "\n"
        "import (\n"
        '\t"example.com/cfgmod/pkg/config"\n'
        ")\n"
        "\n"
        "func main() {\n"
        "\t_ = config.DefaultTimeout\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("DefaultTimeout", tmp_path)

    assert not payload.get("no_match")
    assert payload["references"], "expected the package-qualified read to be found"
    # Before the F9 fix every non-call selector classified "field", even a plain package-level
    # const/var read.
    assert all(row["ref_kind"] == "value" for row in payload["references"])
    assert all(
        row["resolution_provenance"] == ["go-import-resolution"] for row in payload["references"]
    )


# ---------------------------------------------------------------------------
# F10 / F25: an alias-selector CALL only earns high confidence when the resolved package is
# confirmed to actually own the queried symbol.
# ---------------------------------------------------------------------------


def test_shadowed_alias_call_downgrades_to_receiver_heuristic_confidence(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/shadowmod\n\ngo 1.21\n", encoding="utf-8")

    widget_dir = tmp_path / "pkg" / "widget"
    widget_dir.mkdir(parents=True)
    (widget_dir / "widget.go").write_text(
        "package widget\n"
        "\n"
        "type Widget struct{}\n"
        "\n"
        "func (wg *Widget) Write(data []byte) (int, error) {\n"
        "\treturn len(data), nil\n"
        "}\n",
        encoding="utf-8",
    )

    # A second, unrelated package that does NOT define "Write" at all -- its own import alias
    # happens to be named "w", the same name a LOCAL variable shadows it with below.
    other_dir = tmp_path / "pkg" / "other"
    other_dir.mkdir(parents=True)
    (other_dir / "other.go").write_text(
        "package other\n\ntype Other struct{}\n",
        encoding="utf-8",
    )

    app_dir = tmp_path / "cmd" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "main.go").write_text(
        "package main\n"
        "\n"
        "import (\n"
        '\t"example.com/shadowmod/pkg/widget"\n'
        '\tw "example.com/shadowmod/pkg/other"\n'
        ")\n"
        "\n"
        "func main() {\n"
        "\tvar real widget.Widget\n"
        "\tw := &real\n"
        '\tn, err := w.Write([]byte("hello"))\n'
        "\t_ = n\n"
        "\t_ = err\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("Write", tmp_path)

    assert payload["callers"], "expected the shadowed w.Write(...) call to still be found"
    for caller in payload["callers"]:
        assert caller["ref_kind"] == "call"
        # Before the F10/F25 fix this fabricated resolution_confidence=0.95
        # "go-import-resolution" purely because "w" lexically resolves to SOME import, even
        # though that import (pkg/other) does not define "Write" at all.
        assert caller["resolution_provenance"] == ["receiver-heuristic"]
        assert caller["resolution_confidence"] <= 0.7


def test_go_package_defines_function_fallback_confirms_or_denies(tmp_path: Path) -> None:
    """Direct unit coverage of the F10 fallback path (``definition_dirs=None``, e.g. a standalone
    caller of ``go_references_and_calls`` outside the repo_map.py refs/callers dispatch)."""
    (tmp_path / "go.mod").write_text("module example.com/directmod\n\ngo 1.21\n", encoding="utf-8")

    real_dir = tmp_path / "pkg" / "real"
    real_dir.mkdir(parents=True)
    (real_dir / "real.go").write_text(
        "package real\n\nfunc Process() int {\n\treturn 1\n}\n",
        encoding="utf-8",
    )
    empty_dir = tmp_path / "pkg" / "empty"
    empty_dir.mkdir(parents=True)
    (empty_dir / "empty.go").write_text("package empty\n", encoding="utf-8")

    caller_dir = tmp_path / "cmd" / "confirmed"
    caller_dir.mkdir(parents=True)
    confirmed_go = caller_dir / "main.go"
    confirmed_go.write_text(
        "package main\n"
        "\n"
        "import (\n"
        '\t"example.com/directmod/pkg/real"\n'
        ")\n"
        "\n"
        "func main() {\n"
        "\treal.Process()\n"
        "}\n",
        encoding="utf-8",
    )

    unconfirmed_dir = tmp_path / "cmd" / "unconfirmed"
    unconfirmed_dir.mkdir(parents=True)
    unconfirmed_go = unconfirmed_dir / "main.go"
    unconfirmed_go.write_text(
        "package main\n"
        "\n"
        "import (\n"
        '\tprocess "example.com/directmod/pkg/empty"\n'
        ")\n"
        "\n"
        "func main() {\n"
        "\tprocess.Process()\n"
        "}\n",
        encoding="utf-8",
    )

    lang_go.prime_go_repo_context(tmp_path)

    _, confirmed_calls = lang_go.go_references_and_calls(confirmed_go, "Process", tmp_path)
    _, unconfirmed_calls = lang_go.go_references_and_calls(unconfirmed_go, "Process", tmp_path)

    assert confirmed_calls and confirmed_calls[0]["resolution_confidence"] >= 0.9
    assert confirmed_calls[0]["resolution_provenance"] == ["go-import-resolution"]

    assert unconfirmed_calls and unconfirmed_calls[0]["resolution_confidence"] <= 0.7
    assert unconfirmed_calls[0]["resolution_provenance"] == ["receiver-heuristic"]


# ---------------------------------------------------------------------------
# F11: import-path extraction falls back to quote-stripped literal text.
# ---------------------------------------------------------------------------


class _FakeStringLiteralNode:
    """Minimal stand-in for a tree-sitter node shaped like an OLDER ``tree_sitter_go`` grammar's
    import path literal -- no ``interpreted_string_literal_content`` child at all."""

    def __init__(self, start_byte: int, end_byte: int) -> None:
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.children: list[Any] = []
        self.type = "interpreted_string_literal"


def test_import_path_extraction_falls_back_to_quote_stripped_text() -> None:
    source_bytes = b'"example.com/widgetmod/pkg/foo"'
    fake_node = _FakeStringLiteralNode(0, len(source_bytes))

    assert (
        lang_go._go_import_spec_path_text(fake_node, source_bytes)
        == "example.com/widgetmod/pkg/foo"
    )


def test_import_path_extraction_returns_none_for_missing_path_field() -> None:
    assert lang_go._go_import_spec_path_text(None, b"") is None


# ---------------------------------------------------------------------------
# F24: an intervening (nested, non-go.work) go.mod stops import resolution.
# ---------------------------------------------------------------------------


def test_nested_go_mod_boundary_stops_import_resolution(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/nestedmod\n\ngo 1.21\n", encoding="utf-8")

    # A nested module NOT listed in any go.work `use` entry (e.g. simply forgotten) -- its own
    # go.mod makes this directory tree a SEPARATE module even though it sits under the parent
    # module's own path prefix.
    nested_dir = tmp_path / "toolsmod"
    nested_dir.mkdir()
    (nested_dir / "go.mod").write_text(
        "module example.com/nestedmod/toolsmod\n\ngo 1.21\n", encoding="utf-8"
    )
    sub_dir = nested_dir / "sub"
    sub_dir.mkdir()
    (sub_dir / "sub.go").write_text(
        "package sub\n\nfunc Process() int {\n\treturn 1\n}\n",
        encoding="utf-8",
    )

    context = lang_go.prime_go_repo_context(tmp_path)
    target_dir = lang_go._go_import_path_to_dir("example.com/nestedmod/toolsmod/sub", context)

    # Before the F24 fix this greedily resolved to <root>/toolsmod/sub via the ROOT module's own
    # prefix, silently treating a separate nested module's directory as part of the parent.
    assert target_dir is None
