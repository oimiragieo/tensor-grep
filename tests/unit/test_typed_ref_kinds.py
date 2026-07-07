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


def test_mcp_agent_capsule_related_call_sites_carry_ref_kind(tmp_path: Path) -> None:
    """The ``tg_agent_capsule`` MCP tool surfaces ``related_call_sites`` built from blast-radius
    callers -- confirm ref_kind survives that hop too (moat closer: an agent consuming the MCP
    capsule can tell a real call site from a type/field/value mention without re-parsing).
    """
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
