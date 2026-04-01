import ast
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _symbol(payload: dict, name: str, file_path: Path | None = None) -> dict:
    for current in payload["symbols"]:
        if current["name"] != name:
            continue
        if file_path is not None and current["file"] != str(file_path.resolve()):
            continue
        return current
    raise AssertionError(f"symbol {name!r} not found")


def _source(payload: dict, name: str, file_path: Path | None = None) -> dict:
    for current in payload["sources"]:
        if current["name"] != name:
            continue
        if file_path is not None and current["file"] != str(file_path.resolve()):
            continue
        return current
    raise AssertionError(f"source {name!r} not found")


def test_build_repo_map_includes_python_function_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "create_invoice", module_path)

    assert symbol["line"] == 1
    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 3


def test_build_repo_map_includes_python_class_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "service.py"
    module_path.write_text(
        "class PaymentService:\n    def create(self):\n        return 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "PaymentService", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 3


def test_python_function_end_line_matches_ast_end_lineno(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "decorated.py"
    source = (
        "@trace\n"
        "def create_invoice(total, tax):\n"
        '    """docstring"""\n'
        "    if total:\n"
        "        return total + tax\n"
        "    return tax\n"
    )
    module_path.write_text(source, encoding="utf-8")

    parsed = ast.parse(source)
    function_node = next(
        node
        for node in ast.walk(parsed)
        if isinstance(node, ast.FunctionDef) and node.name == "create_invoice"
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "create_invoice", module_path)

    assert symbol["start_line"] == function_node.lineno
    assert symbol["end_line"] == function_node.end_lineno


def test_build_repo_map_includes_python_method_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "service.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    def method_a(self):\n"
        "        return 1\n"
        "\n"
        "    def method_b(self):\n"
        "        return 2\n"
        "\n"
        "    def method_c(self):\n"
        "        return 3\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "method_b", module_path)

    assert symbol["start_line"] == 5
    assert symbol["end_line"] == 6


def test_build_repo_map_includes_nested_python_function_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "nested.py"
    module_path.write_text(
        "def outer():\n    def inner():\n        return 1\n\n    return inner()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "inner", module_path)

    assert symbol["start_line"] == 2
    assert symbol["end_line"] == 3


def test_build_symbol_source_returns_only_method_span(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "service.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    def method_a(self):\n"
        "        return 1\n"
        "\n"
        "    def method_b(self):\n"
        "        value = 2\n"
        "        return value\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_source("method_b", project)
    source = _source(payload, "method_b", module_path)

    assert source["start_line"] == 5
    assert source["end_line"] == 7
    assert source["source"] == "    def method_b(self):\n        value = 2\n        return value\n"


def test_build_symbol_source_returns_only_nested_function_span(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "nested.py"
    module_path.write_text(
        "def outer():\n"
        "    def inner():\n"
        "        result = 2\n"
        "        return result\n"
        "\n"
        "    return inner()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_source("inner", project)
    source = _source(payload, "inner", module_path)

    assert source["start_line"] == 2
    assert source["end_line"] == 4
    assert source["source"] == "    def inner():\n        result = 2\n        return result\n"


def test_context_render_exposes_python_primary_span(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("create invoice", project)

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 1, "end_line": 3}


def test_context_render_primary_span_isolated_for_python_method(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "service.py").write_text(
        "class PaymentService:\n"
        "    def method_a(self):\n"
        "        return 1\n"
        "\n"
        "    def method_b(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("method_b", project)

    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 5, "end_line": 6}


def test_context_render_primary_span_isolated_for_nested_python_function(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "nested.py").write_text(
        "def outer():\n    def inner():\n        return 1\n    return inner()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("inner", project)

    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 2, "end_line": 3}


def test_build_repo_map_includes_javascript_function_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.js"
    module_path.write_text(
        "export function createInvoice(total) {\n"
        "  const subtotal = total + 1;\n"
        "  return subtotal;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "createInvoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 4


def test_context_render_exposes_javascript_primary_span(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.js").write_text(
        "export function createInvoice(total) {\n"
        "  const subtotal = total + 1;\n"
        "  return subtotal;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("createInvoice", project)

    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 1, "end_line": 4}


def test_build_repo_map_includes_javascript_class_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "service.js"
    module_path.write_text(
        "export class PaymentService {\n  create() {\n    return 1;\n  }\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "PaymentService", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 5


def test_build_repo_map_includes_typescript_function_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  const subtotal = total + 1;\n"
        "  return subtotal;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "createInvoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 4


def test_context_render_exposes_rust_primary_span(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "billing.rs").write_text(
        "pub fn issue_invoice() -> usize {\n    let value = 1;\n    value\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render("issue_invoice", project)

    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 1, "end_line": 4}


def test_build_repo_map_includes_rust_function_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "billing.rs"
    module_path.write_text(
        "pub fn issue_invoice() -> usize {\n    let value = 1;\n    value\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "issue_invoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 4


def test_build_repo_map_includes_rust_impl_method_spans(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "billing.rs"
    module_path.write_text(
        "pub struct Invoice;\n"
        "\n"
        "impl Invoice {\n"
        "    pub fn build() -> Self {\n"
        "        Invoice\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "build", module_path)

    assert symbol["start_line"] == 4
    assert symbol["end_line"] == 6


def test_javascript_regex_fallback_sets_end_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.js"
    module_path.write_text(
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_javascript_parser", lambda: None)

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "createInvoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 3


def test_typescript_regex_fallback_sets_end_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number) {\n  return total + 1;\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_typescript_parser", lambda **_: None)

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "createInvoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 3


def test_rust_regex_fallback_sets_end_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "billing.rs"
    module_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_rust_parser", lambda: None)

    payload = repo_map.build_repo_map(project)
    symbol = _symbol(payload, "issue_invoice", module_path)

    assert symbol["start_line"] == 1
    assert symbol["end_line"] == 3


def test_malformed_javascript_file_does_not_crash_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "broken.js").write_text(
        "export function createInvoice(total) {\n  return total + 1;\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    assert isinstance(payload["symbols"], list)


def test_malformed_typescript_file_does_not_crash_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "broken.ts").write_text(
        "export function createInvoice(total: number) {\n  return total + 1;\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    assert isinstance(payload["symbols"], list)


def test_malformed_rust_file_does_not_crash_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    broken_path = src_dir / "broken.rs"
    broken_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_source("issue_invoice", project)

    assert isinstance(payload["sources"], list)
