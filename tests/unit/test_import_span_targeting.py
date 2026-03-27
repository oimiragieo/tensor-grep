from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _edit_plan_seed(project: Path, symbol: str) -> dict[str, object]:
    payload = repo_map.build_symbol_blast_radius_render(symbol, project)
    return dict(payload["edit_plan_seed"])


def _suggested_edit(seed: dict[str, object], *, file_path: Path, edit_kind: str) -> dict[str, object]:
    resolved = str(file_path.resolve())
    for current in seed["suggested_edits"]:
        if current["file"] == resolved and current["edit_kind"] == edit_kind:
            return dict(current)
    raise AssertionError(f"missing {edit_kind!r} edit for {resolved}")


@pytest.mark.parametrize(
    ("import_statement", "module_name"),
    [
        pytest.param("from src.payments import create_invoice\n", "src.payments", id="from-import"),
        pytest.param("import src.payments as payments\n", "src.payments", id="module-import"),
    ],
)
def test_python_import_update_targets_exact_import_statement_line(
    tmp_path: Path,
    import_statement: str,
    module_name: str,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    _write(
        src_dir / "service.py",
        import_statement
        + "\n"
        + "def build_receipt(total):\n"
        + "    return create_invoice(total)\n",
    )

    seed = _edit_plan_seed(project, "create_invoice")

    import_update = _suggested_edit(seed, file_path=src_dir / "service.py", edit_kind="import-update")
    assert import_update["symbol"] == "create_invoice"
    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1
    assert import_update["provenance"] == "parser-backed"
    assert 0.0 < float(import_update["confidence"]) <= 1.0
    assert "imports create_invoice" in str(import_update["rationale"])
    assert module_name in str(import_update["rationale"])

    caller_update = _suggested_edit(seed, file_path=src_dir / "service.py", edit_kind="caller-update")
    assert caller_update["symbol"] == "build_receipt"
    assert caller_update["start_line"] == 3
    assert caller_update["end_line"] == 4


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_js_ts_import_update_targets_exact_import_statement_line(
    tmp_path: Path,
    suffix: str,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(
        src_dir / f"service{suffix}",
        'import { createInvoice } from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")

    import_update = _suggested_edit(
        seed,
        file_path=src_dir / f"service{suffix}",
        edit_kind="import-update",
    )
    assert import_update["symbol"] == "createInvoice"
    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1
    assert import_update["provenance"] in {"parser-backed", "heuristic"}
    assert 0.0 < float(import_update["confidence"]) <= 1.0
    assert "imports createInvoice" in str(import_update["rationale"])
    assert "./payments" in str(import_update["rationale"])


def test_rust_import_update_targets_exact_use_statement_line(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.rs",
        "pub fn create_invoice(total: i32) -> i32 {\n"
        "    total + 1\n"
        "}\n",
    )
    _write(
        src_dir / "service.rs",
        "use crate::payments::create_invoice;\n"
        "\n"
        "pub fn build_receipt(total: i32) -> i32 {\n"
        "    create_invoice(total)\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "create_invoice")

    import_update = _suggested_edit(seed, file_path=src_dir / "service.rs", edit_kind="import-update")
    assert import_update["symbol"] == "create_invoice"
    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1
    assert import_update["provenance"] == "heuristic"
    assert 0.0 < float(import_update["confidence"]) <= 1.0
    assert "imports create_invoice" in str(import_update["rationale"])
    assert "crate::payments" in str(import_update["rationale"])
