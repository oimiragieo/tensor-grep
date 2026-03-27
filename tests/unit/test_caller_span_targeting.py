import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _edit_plan_seed(project: Path, symbol: str) -> dict[str, object]:
    payload = repo_map.build_symbol_blast_radius_render(symbol, project)
    return dict(payload["edit_plan_seed"])


def _caller_updates(seed: dict[str, object], *, file_path: Path) -> list[dict[str, object]]:
    resolved = str(file_path.resolve())
    return sorted(
        [
            dict(current)
            for current in seed["suggested_edits"]
            if current["file"] == resolved and current["edit_kind"] == "caller-update"
        ],
        key=lambda current: (int(current["start_line"]), int(current["end_line"]), str(current["symbol"])),
    )


def test_python_caller_updates_target_exact_call_site_lines(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    _write(
        src_dir / "service.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    first = create_invoice(total)\n"
        "    return create_invoice(first)\n",
    )

    seed = _edit_plan_seed(project, "create_invoice")

    caller_updates = _caller_updates(seed, file_path=src_dir / "service.py")
    assert [(entry["symbol"], entry["start_line"], entry["end_line"]) for entry in caller_updates] == [
        ("build_receipt", 4, 4),
        ("build_receipt", 5, 5),
    ]
    for entry in caller_updates:
        assert entry["provenance"] == "python-ast"
        assert 0.0 < float(entry["confidence"]) <= 1.0
        assert f"calls create_invoice() on line {entry['start_line']}" in str(entry["rationale"])
        assert "signature changes" in str(entry["rationale"])


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_js_ts_caller_updates_target_exact_call_site_line(tmp_path: Path, suffix: str) -> None:
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

    caller_updates = _caller_updates(seed, file_path=src_dir / f"service{suffix}")
    assert [(entry["symbol"], entry["start_line"], entry["end_line"]) for entry in caller_updates] == [
        ("buildReceipt", 4, 4),
    ]
    entry = caller_updates[0]
    assert entry["provenance"] in {"tree-sitter", "regex-heuristic"}
    assert 0.0 < float(entry["confidence"]) <= 1.0
    assert "calls createInvoice() on line 4" in str(entry["rationale"])


def test_rust_caller_updates_target_scoped_call_site_line(tmp_path: Path) -> None:
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
        "pub fn build_receipt(total: i32) -> i32 {\n"
        "    let first = crate::payments::create_invoice(total);\n"
        "    crate::payments::create_invoice(first)\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "create_invoice")

    caller_updates = _caller_updates(seed, file_path=src_dir / "service.rs")
    assert [(entry["symbol"], entry["start_line"], entry["end_line"]) for entry in caller_updates] == [
        ("build_receipt", 2, 2),
        ("build_receipt", 3, 3),
    ]
    for entry in caller_updates:
        assert entry["provenance"] in {"tree-sitter", "regex-heuristic"}
        assert 0.0 < float(entry["confidence"]) <= 1.0
        assert f"calls create_invoice() on line {entry['start_line']}" in str(entry["rationale"])


def test_caller_updates_flag_ambiguous_same_named_symbols(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "alpha.py",
        "def foo(value):\n"
        "    return value + 1\n",
    )
    _write(
        src_dir / "beta.py",
        "def foo(value):\n"
        "    return value + 2\n",
    )
    _write(
        src_dir / "service.py",
        "def run(value):\n"
        "    return foo(value)\n",
    )

    seed = _edit_plan_seed(project, "foo")

    caller_updates = _caller_updates(seed, file_path=src_dir / "service.py")
    assert len(caller_updates) == 1
    entry = caller_updates[0]
    assert entry["ambiguous"] is True
    assert isinstance(entry["alternatives"], list)
    assert entry["alternatives"]
    alternative_files = {str(current["file"]) for current in entry["alternatives"]}
    assert alternative_files <= {
        str((src_dir / "alpha.py").resolve()),
        str((src_dir / "beta.py").resolve()),
    }
    assert all(current["symbol"] == "foo" for current in entry["alternatives"])


def test_caller_updates_deduplicate_same_file_and_line(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    _write(
        src_dir / "service.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total) + create_invoice(total)\n",
    )

    seed = _edit_plan_seed(project, "create_invoice")

    caller_updates = _caller_updates(seed, file_path=src_dir / "service.py")
    assert len(caller_updates) == 1
    assert caller_updates[0]["start_line"] == 4
    assert caller_updates[0]["end_line"] == 4


def test_cli_context_render_includes_exact_caller_update_lines(tmp_path: Path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    service_path = src_dir / "service.py"
    _write(
        service_path,
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
    )

    result = runner.invoke(
        app,
        ["context-render", "--query", "create invoice", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    caller_updates = [
        dict(current)
        for current in payload["edit_plan_seed"]["suggested_edits"]
        if current["file"] == str(service_path.resolve()) and current["edit_kind"] == "caller-update"
    ]
    assert len(caller_updates) == 1
    entry = caller_updates[0]
    assert entry["file"] == str(service_path.resolve())
    assert entry["symbol"] == "build_receipt"
    assert entry["start_line"] == 4
    assert entry["end_line"] == 4
    assert entry["edit_kind"] == "caller-update"
    assert (
        entry["rationale"]
        == "calls create_invoice() on line 4; if create_invoice's signature changes, this call site must be updated"
    )
    assert entry["provenance"] == "python-ast"
    assert 0.0 < float(entry["confidence"]) <= 1.0
