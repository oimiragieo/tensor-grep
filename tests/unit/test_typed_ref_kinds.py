"""PATH A STAGE T1 -- typed ``ref_kind`` on refs/callers/blast-radius (classify-only, additive).

Every fixture below places ONE symbol in all 5 syntactic positions from the STAGE T1 taxonomy
(call, import, type-annotation, field-access, bare value) per language. The governance rule under
test is: ``ref_kind`` is ADDITIVE-ONLY -- the existing ``kind`` values ("reference"/"call") and the
existing ROW COUNTS must not change. Import-position rows (and, for JS/TS and Rust, most
type/field positions -- see the per-language docstrings) are already skipped by the pre-existing
match logic and MUST STAY skipped in T1; only rows the extractor already emits get a `ref_kind`
label.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import mcp_server, repo_map


def test_python_ref_kind_classification_five_positions(tmp_path: Path) -> None:
    """Python's ast-based extractor is the only one of the 3 that reaches 4/5 positions today.

    ``from mod import Symbol`` (import) never produces a row -- ``ast.alias`` is not a Name/
    Attribute node -- so it stays skipped in T1 (that gap is STAGE T2 material).
    """
    mod_path = tmp_path / "mod.py"
    mod_path.write_text("class Symbol:\n    pass\n", encoding="utf-8")
    use_path = tmp_path / "use.py"
    use_path.write_text(
        "from mod import Symbol\n"
        "\n"
        "\n"
        "def use_symbol(x: Symbol) -> Symbol:\n"
        "    y = x.Symbol\n"
        "    z = Symbol\n"
        "    return Symbol()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    # kind is UNCHANGED (pinned elsewhere too) -- every reference row is still "reference".
    assert all(row["kind"] == "reference" for row in use_refs.values())

    # exact ref_kind per position.
    assert use_refs[4]["ref_kind"] == "type"  # def use_symbol(x: Symbol) -> Symbol:
    assert use_refs[5]["ref_kind"] == "field"  # y = x.Symbol
    assert use_refs[6]["ref_kind"] == "value"  # z = Symbol
    assert use_refs[7]["ref_kind"] == "call"  # return Symbol()

    # import position never emitted a row before T1 and must not start now.
    assert not any(
        row["file"] == str(use_path.resolve()) and "import" in row.get("text", "")
        for row in payload["references"]
    )

    counts = payload["coverage_summary"]["reference_kind_counts"]
    assert sum(counts.values()) == len(payload["references"])
    assert counts["call"] >= 1
    assert counts["type"] >= 1
    assert counts["field"] >= 1
    assert counts["value"] >= 1

    callers_payload = repo_map.build_symbol_callers("Symbol", tmp_path)
    assert len(callers_payload["callers"]) == 1
    assert callers_payload["callers"][0]["kind"] == "call"
    assert callers_payload["callers"][0]["ref_kind"] == "call"

    blast_payload = repo_map.build_symbol_blast_radius("Symbol", tmp_path)
    assert all("ref_kind" in row for row in blast_payload["callers"])
    assert blast_payload["graph_trust_summary"]["evidence_counts"]["by_ref_kind"]["call"] == 1


def test_js_ts_ref_kind_classification_five_positions(tmp_path: Path) -> None:
    """TS's grammar splits type-position symbols into a distinct ``type_identifier`` node type
    (never matched by the walker), so only the ``extends Symbol`` heritage clause -- parsed as a
    plain ``identifier`` -- reaches "type" in T1; ``implements Symbol`` stays unmatched (0 rows),
    same as the plain ``import`` binding. Both are documented STAGE T2 gaps, not T1 regressions.
    """
    mod_path = tmp_path / "mod.ts"
    mod_path.write_text("export class Symbol {}\n", encoding="utf-8")
    use_path = tmp_path / "use.ts"
    use_path.write_text(
        'import { Symbol } from "./mod";\n'
        "\n"
        "function useSymbol(x: Symbol): Symbol {\n"
        "  const y = x.Symbol;\n"
        "  const z = Symbol;\n"
        "  Symbol();\n"
        "  z.Symbol();\n"
        "  obj.Symbol();\n"
        "  return z;\n"
        "}\n"
        "\n"
        "class Foo extends Symbol implements Symbol {\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    assert all(row["kind"] == "reference" for row in use_refs.values())

    assert use_refs[4]["ref_kind"] == "field"  # const y = x.Symbol;
    assert use_refs[5]["ref_kind"] == "value"  # const z = Symbol;
    assert use_refs[6]["ref_kind"] == "call"  # Symbol();
    assert use_refs[7]["ref_kind"] == "call"  # z.Symbol();  (field-call form)
    assert use_refs[8]["ref_kind"] == "call"  # obj.Symbol();
    assert use_refs[12]["ref_kind"] == "type"  # class Foo extends Symbol implements Symbol {

    # the `import { Symbol }` binding on line 1 stays skipped (0 rows) -- unchanged from pre-T1.
    assert 1 not in use_refs

    counts = payload["coverage_summary"]["reference_kind_counts"]
    assert sum(counts.values()) == len(payload["references"])

    callers_payload = repo_map.build_symbol_callers("Symbol", tmp_path)
    assert len(callers_payload["callers"]) == 3
    assert all(row["kind"] == "call" for row in callers_payload["callers"])
    assert all(row["ref_kind"] == "call" for row in callers_payload["callers"])


def test_rust_ref_kind_classification_five_positions(tmp_path: Path) -> None:
    """Rust's grammar splits type positions into ``type_identifier`` and bare field access into
    ``field_identifier`` (both distinct from ``identifier``), so neither reaches a row in T1 --
    only the call forms (free call, field-call, and the bare ``value`` reference) do. Widening
    the match set to cover those is STAGE T2 (it would add rows, not just labels).
    """
    mod_path = tmp_path / "mod.rs"
    mod_path.write_text("pub struct Symbol;\n", encoding="utf-8")
    use_path = tmp_path / "use_it.rs"
    use_path.write_text(
        "use crate::mod_a::Symbol;\n"
        "\n"
        "fn use_symbol(x: Symbol) -> Symbol {\n"
        "    let y = x.Symbol;\n"
        "    let z = Symbol;\n"
        "    Symbol();\n"
        "    z.Symbol();\n"
        "    obj.Symbol()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    assert all(row["kind"] == "reference" for row in use_refs.values())

    # bare field access (`x.Symbol`, non-call, line 4) and the type positions (param/return
    # annotation on line 3, struct-not-present here) stay unmatched -- 0 rows, unchanged from
    # pre-T1. The `use ...;` import binding (line 1) is likewise unmatched.
    assert 1 not in use_refs
    assert 4 not in use_refs

    assert use_refs[5]["ref_kind"] == "value"  # let z = Symbol;
    assert use_refs[6]["ref_kind"] == "call"  # Symbol();
    assert use_refs[7]["ref_kind"] == "call"  # z.Symbol();
    assert use_refs[8]["ref_kind"] == "call"  # obj.Symbol()

    counts = payload["coverage_summary"]["reference_kind_counts"]
    assert sum(counts.values()) == len(payload["references"])

    callers_payload = repo_map.build_symbol_callers("Symbol", tmp_path)
    assert len(callers_payload["callers"]) == 3
    assert all(row["kind"] == "call" for row in callers_payload["callers"])
    assert all(row["ref_kind"] == "call" for row in callers_payload["callers"])


def test_mcp_agent_capsule_related_call_sites_carry_ref_kind(tmp_path: Path, monkeypatch) -> None:
    """The ``tg_agent_capsule`` MCP tool surfaces ``related_call_sites`` built from blast-radius
    callers -- confirm ref_kind survives that hop too (moat closer: an agent consuming the MCP
    capsule can tell a real call site from a type/field/value mention without re-parsing).
    """
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    service_path = src_dir / "billing.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def settle_invoice():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    raw = mcp_server.tg_agent_capsule(
        "change create_invoice tax calculation",
        str(tmp_path),
        max_files=2,
        max_tokens=500,
    )
    import json

    payload = json.loads(raw)
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload["related_call_sites"], "fixture must produce at least one related call site"
    assert all(row.get("ref_kind") == "call" for row in payload["related_call_sites"])


# ---------------------------------------------------------------------------
# F6 / F18: Rust macro-argument identifiers stay "value"; turbofish calls classify "call".
# ---------------------------------------------------------------------------


def test_rust_macro_argument_is_not_classified_as_call(tmp_path: Path) -> None:
    """A macro's ARGUMENTS (inside its token-tree) are data, not a call site -- only the macro
    NAME position (``println``/``vec``, not matched here since we're querying a different symbol)
    is "call". Before the F6 fix, ANY identifier anywhere inside a macro invocation's token tree
    classified "call", inflating the highest-signal ref_kind count."""
    mod_path = tmp_path / "mod.rs"
    mod_path.write_text("pub struct Symbol;\n", encoding="utf-8")
    use_path = tmp_path / "use_it.rs"
    use_path.write_text(
        "use crate::mod_a::Symbol;\n"
        "\n"
        "fn use_symbol() {\n"
        '    println!("{:?}", Symbol);\n'
        "    let v = vec![Symbol];\n"
        "    let _ = v;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    assert use_refs[4]["ref_kind"] == "value"  # println!("{:?}", Symbol);
    assert use_refs[5]["ref_kind"] == "value"  # let v = vec![Symbol];
    assert not any(row["ref_kind"] == "call" for row in use_refs.values())


def test_rust_turbofish_call_classified_as_call(tmp_path: Path) -> None:
    """``foo::<T>()`` puts the function identifier under a ``generic_function`` node one layer
    below ``call_expression`` -- both the bare and path-qualified (``bar::Symbol::<T>()``) forms
    must classify "call", matching a plain ``Symbol()`` call."""
    mod_path = tmp_path / "mod.rs"
    mod_path.write_text("pub fn Symbol() {}\n", encoding="utf-8")
    use_path = tmp_path / "use_it.rs"
    use_path.write_text(
        "use crate::mod_a::Symbol;\n"
        "\n"
        "fn use_symbol() {\n"
        "    Symbol::<i32>(1);\n"
        "    bar::Symbol::<i32>(1);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    assert use_refs[4]["ref_kind"] == "call"  # Symbol::<i32>(1);
    assert use_refs[5]["ref_kind"] == "call"  # bar::Symbol::<i32>(1);


# ---------------------------------------------------------------------------
# F7: JS/TS `as`/`satisfies` operand keeps its own kind, not "type".
# ---------------------------------------------------------------------------


def test_js_ts_as_and_satisfies_operand_keeps_own_kind(tmp_path: Path) -> None:
    mod_path = tmp_path / "mod.ts"
    mod_path.write_text("export class Symbol {}\n", encoding="utf-8")
    use_path = tmp_path / "use.ts"
    use_path.write_text(
        'import { Symbol } from "./mod";\n'
        "\n"
        "function useSymbol() {\n"
        "  const a = Symbol as unknown;\n"
        "  const b = Symbol satisfies object;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    # Before the F7 fix both of these mislabeled "type" purely because `as_expression` /
    # `satisfies_expression` sat in the generic ancestor-walk type set -- the runtime VALUE
    # operand must keep its own kind ("value" here, since it's a bare identifier reference).
    assert use_refs[4]["ref_kind"] == "value"  # const a = Symbol as unknown;
    assert use_refs[5]["ref_kind"] == "value"  # const b = Symbol satisfies object;


# ---------------------------------------------------------------------------
# F17: `new Widget()` / JSX construction classify "call".
# ---------------------------------------------------------------------------


def test_js_ts_new_expression_and_jsx_classified_as_call(tmp_path: Path) -> None:
    mod_path = tmp_path / "mod.tsx"
    mod_path.write_text("export class Symbol {}\n", encoding="utf-8")
    use_path = tmp_path / "use.tsx"
    use_path.write_text(
        'import { Symbol } from "./mod";\n'
        "\n"
        "function useSymbol() {\n"
        "  const a = new Symbol();\n"
        "  const b = <Symbol/>;\n"
        "  const c = <Symbol></Symbol>;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = [row for row in payload["references"] if row["file"] == str(use_path.resolve())]

    new_expr_refs = [row for row in use_refs if row["line"] == 4]
    jsx_self_closing_refs = [row for row in use_refs if row["line"] == 5]
    jsx_pair_refs = [row for row in use_refs if row["line"] == 6]

    assert new_expr_refs and all(row["ref_kind"] == "call" for row in new_expr_refs)
    assert jsx_self_closing_refs and all(row["ref_kind"] == "call" for row in jsx_self_closing_refs)
    # <Symbol></Symbol> emits an opening- and closing-tag identifier at the AST level, but the
    # pre-existing `_dedupe_symbol_references` step (identical file/line/text) collapses them to
    # ONE row -- that dedupe is unrelated to F17 and must stay in force; what F17 fixes is the
    # LABEL on the surviving row (was "value", now "call").
    assert jsx_pair_refs and all(row["ref_kind"] == "call" for row in jsx_pair_refs)


# ---------------------------------------------------------------------------
# F20: a classifier exception must default THIS row to "value", never drop the file's rows.
# ---------------------------------------------------------------------------


def test_js_ts_classifier_exception_defaults_to_value_without_dropping_rows(
    tmp_path: Path, monkeypatch
) -> None:
    mod_path = tmp_path / "mod.ts"
    mod_path.write_text("export class Symbol {}\n", encoding="utf-8")
    use_path = tmp_path / "use.ts"
    use_path.write_text(
        'import { Symbol } from "./mod";\n'
        "\n"
        "function useSymbol(x: Symbol) {\n"
        "  return x.Symbol;\n"
        "}\n",
        encoding="utf-8",
    )

    baseline = repo_map.build_symbol_refs("Symbol", tmp_path)
    baseline_count = len([
        row for row in baseline["references"] if row["file"] == str(use_path.resolve())
    ])
    assert baseline_count > 0

    def _boom(node: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(repo_map, "_js_ts_classify_ref_kind", _boom)

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = [row for row in payload["references"] if row["file"] == str(use_path.resolve())]

    assert len(use_refs) == baseline_count
    assert all(row["ref_kind"] == "value" for row in use_refs)


def test_rust_classifier_exception_defaults_to_value_without_dropping_rows(
    tmp_path: Path, monkeypatch
) -> None:
    """``_rust_classify_ref_kind`` is only invoked for the plain-``identifier`` walker branch
    (e.g. ``let z = Symbol;``) -- the separate ``call_expression`` branch stamps ``ref_kind="call"``
    directly without going through the classifier at all, so it is unaffected by this monkeypatch
    and stays "call". F20 is about THAT classifier call never dropping the row it covers."""
    mod_path = tmp_path / "mod.rs"
    mod_path.write_text("pub struct Symbol;\n", encoding="utf-8")
    use_path = tmp_path / "use_it.rs"
    use_path.write_text(
        "use crate::mod_a::Symbol;\n"
        "\n"
        "fn use_symbol() -> Symbol {\n"
        "    let z = Symbol;\n"
        "    Symbol()\n"
        "}\n",
        encoding="utf-8",
    )

    baseline = repo_map.build_symbol_refs("Symbol", tmp_path)
    baseline_refs = {
        row["line"]: row for row in baseline["references"] if row["file"] == str(use_path.resolve())
    }
    assert baseline_refs

    def _boom(node: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(repo_map, "_rust_classify_ref_kind", _boom)

    payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    use_refs = {
        row["line"]: row for row in payload["references"] if row["file"] == str(use_path.resolve())
    }

    assert len(use_refs) == len(baseline_refs)
    assert use_refs[4]["ref_kind"] == "value"  # let z = Symbol; -- classifier boomed -> default
    assert use_refs[5]["ref_kind"] == "call"  # Symbol(); -- hardcoded by a different branch


# ---------------------------------------------------------------------------
# F19: a Call's arguments inside a type annotation are VALUES, not type syntax.
# ---------------------------------------------------------------------------


def test_python_call_argument_inside_annotation_is_not_mislabeled_type(tmp_path: Path) -> None:
    """F19 (audit #63): `_walk` propagates `in_annotation` into every child field, including an
    `ast.Call`'s `args`/`keywords` (repo_map.py:4383-4390) -- so a runtime-value argument passed
    to a call INSIDE a type annotation (e.g. ``Annotated[Head, validate(Symbol)]``) mislabeled
    the argument ref_kind "type" (it is a plain VALUE read, not type syntax) purely because the
    call happens to sit inside an annotation subtree. The callee (``validate``) and the
    annotation's own head (``AnnotationHead``) are both unaffected -- the parent-Call precedence
    (repo_map.py:4320-4321) already wins for the callee regardless of in_annotation, and the fix
    only resets in_annotation for a Call's OWN args/keywords fields, not Annotated's slice.
    """
    mod_path = tmp_path / "mod.py"
    mod_path.write_text(
        "class Symbol:\n"
        "    pass\n"
        "\n"
        "\n"
        "class AnnotationHead:\n"
        "    pass\n"
        "\n"
        "\n"
        "def validate(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    use_path = tmp_path / "use.py"
    use_path.write_text(
        "from typing import Annotated\n"
        "\n"
        "from mod import AnnotationHead, Symbol, validate\n"
        "\n"
        "\n"
        "def plain_annotation(y: Symbol) -> None:\n"
        "    pass\n"
        "\n"
        "\n"
        "def f(x: Annotated[AnnotationHead, validate(Symbol)]) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    symbol_payload = repo_map.build_symbol_refs("Symbol", tmp_path)
    symbol_refs = {
        row["line"]: row
        for row in symbol_payload["references"]
        if row["file"] == str(use_path.resolve())
    }
    # Row-count invariant (the #422 zero-count-change rule governing this whole tail PR): the
    # fix only relabels ref_kind, it must never add/remove a reference row.
    assert len(symbol_refs) == 2
    assert symbol_refs[6]["ref_kind"] == "type"  # def plain_annotation(y: Symbol) -> None:
    # Before the F19 fix this was "type" -- Symbol here is a call ARGUMENT, not type syntax,
    # even though `validate(Symbol)` sits inside `Annotated[...]`'s type-annotation subtree.
    assert symbol_refs[10]["ref_kind"] == "value"  # ...Annotated[AnnotationHead, validate(Symbol)]

    validate_payload = repo_map.build_symbol_refs("validate", tmp_path)
    validate_refs = [
        row for row in validate_payload["references"] if row["file"] == str(use_path.resolve())
    ]
    assert len(validate_refs) == 1
    assert validate_refs[0]["ref_kind"] == "call"  # the callee keeps "call" -- untouched by F19.

    head_payload = repo_map.build_symbol_refs("AnnotationHead", tmp_path)
    head_refs = [
        row for row in head_payload["references"] if row["file"] == str(use_path.resolve())
    ]
    assert len(head_refs) == 1
    # Annotated's own first arg (the actual annotation head) stays "type" -- only a Call's
    # args/keywords reset in_annotation, not Annotated's subscript slice itself.
    assert head_refs[0]["ref_kind"] == "type"


# ---------------------------------------------------------------------------
# F21: `_reference_kind_counts` must count non-dict rows too (sum == len invariant).
# ---------------------------------------------------------------------------


def test_reference_kind_counts_counts_non_dict_rows_too() -> None:
    references = [
        {"ref_kind": "call"},
        {"ref_kind": "value"},
        "not-a-dict",
        {},
    ]

    counts = repo_map._reference_kind_counts(references)

    assert sum(counts.values()) == len(references)
