from pathlib import Path

from tensor_grep.cli import repo_map


def test_build_symbol_callers_resolves_typescript_import_aliases(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.ts"
    consumer_path = src_dir / "consumer.ts"
    util_path.write_text(
        "export function foo() {\n"
        "  return 1;\n"
        "}\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        'import { foo as bar } from "./util";\n'
        "export function callBar() {\n"
        "  return bar();\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("foo", project)

    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])


def test_build_symbol_refs_resolves_javascript_import_aliases(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.js"
    consumer_path = src_dir / "consumer.js"
    util_path.write_text(
        "export function foo() {\n"
        "  return 1;\n"
        "}\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        'import { foo as bar } from "./util";\n'
        "const value = bar();\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("foo", project)

    assert any(ref["file"] == str(consumer_path.resolve()) for ref in payload["references"])


def test_build_symbol_callers_resolves_rust_use_aliases(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "billing.rs"
    consumer_path = src_dir / "consumer.rs"
    module_path.write_text(
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        "use crate::billing::{issue_invoice as dispatch};\n\n"
        "pub fn settle_invoice() -> usize {\n"
        "    dispatch()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("issue_invoice", project)

    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])


def test_build_symbol_impact_prefers_the_rust_module_selected_by_use_chain(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    billing_path = src_dir / "billing.rs"
    other_path = src_dir / "other.rs"
    consumer_path = src_dir / "consumer.rs"
    billing_path.write_text("pub struct Type;\n", encoding="utf-8")
    other_path.write_text("pub struct Type;\n", encoding="utf-8")
    consumer_path.write_text(
        "use crate::billing::Type;\n\n"
        "pub fn build() {\n"
        "    let _ = Type;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_impact("Type", project)

    assert payload["files"][0] == str(billing_path.resolve())
    assert str(consumer_path.resolve()) in payload["files"][:2]
    assert str(other_path.resolve()) not in payload["files"][:2]


def test_build_symbol_callers_discovers_non_filename_tests_by_import_graph(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    module_path = src_dir / "core.py"
    integration_test_path = tests_dir / "integration_checks.py"
    unrelated_test_path = tests_dir / "test_unrelated.py"
    module_path.write_text(
        "def foo():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    integration_test_path.write_text(
        "from src.core import foo\n\n"
        "def test_flow():\n"
        "    assert foo() == 1\n",
        encoding="utf-8",
    )
    unrelated_test_path.write_text(
        "def test_other():\n"
        "    assert 1 == 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("foo", project)

    assert payload["tests"][0] == str(integration_test_path.resolve())
    assert str(unrelated_test_path.resolve()) not in payload["tests"]


def test_build_symbol_impact_excludes_unrelated_tests(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    module_path = src_dir / "core.py"
    integration_test_path = tests_dir / "integration_checks.py"
    unrelated_test_path = tests_dir / "test_unrelated.py"
    module_path.write_text(
        "def foo():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    integration_test_path.write_text(
        "from src.core import foo\n\n"
        "def test_flow():\n"
        "    assert foo() == 1\n",
        encoding="utf-8",
    )
    unrelated_test_path.write_text(
        "def test_other():\n"
        "    assert 1 == 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_impact("foo", project)

    assert payload["tests"][0] == str(integration_test_path.resolve())
    assert str(unrelated_test_path.resolve()) not in payload["tests"]
