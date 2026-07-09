from pathlib import Path

from tensor_grep.cli import repo_map


def test_build_symbol_callers_resolves_typescript_import_aliases(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.ts"
    consumer_path = src_dir / "consumer.ts"
    util_path.write_text(
        "export function foo() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        'import { foo as bar } from "./util";\nexport function callBar() {\n  return bar();\n}\n',
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("foo", project)

    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])


def test_build_symbol_callers_surfaces_typescript_import_only_consumers(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.ts"
    import_only_path = src_dir / "imports_only.ts"
    caller_path = src_dir / "caller.ts"
    util_path.write_text(
        "export function foo() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    import_only_path.write_text(
        'import { foo } from "./util";\nexport const label = "no call";\n',
        encoding="utf-8",
    )
    caller_path.write_text(
        'import { foo } from "./util";\nexport function callFoo() {\n  return foo();\n}\n',
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("foo", project)

    caller_files = {caller["file"] for caller in payload["callers"]}
    assert str(caller_path.resolve()) in caller_files
    assert str(import_only_path.resolve()) not in caller_files

    consumer_files = set(payload["import_graph_consumer_files"])
    assert consumer_files == {str(caller_path.resolve()), str(import_only_path.resolve())}
    import_only = next(
        consumer
        for consumer in payload["import_graph_consumers"]
        if consumer["file"] == str(import_only_path.resolve())
    )
    assert import_only["line"] == 1
    assert import_only["end_line"] == 1
    assert import_only["text"] == 'import { foo } from "./util";'
    assert import_only["kind"] == "import-consumer"
    assert import_only["edge_kind"] == "reverse-import"
    assert import_only["definition_file"] == str(util_path.resolve())
    assert import_only["module"] == "./util"
    assert import_only["provenance"] in {"parser-backed", "heuristic"}


def test_build_symbol_blast_radius_preserves_callers_and_import_consumers(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.ts"
    import_only_path = src_dir / "imports_only.ts"
    caller_path = src_dir / "caller.ts"
    util_path.write_text(
        "export function foo() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    import_only_path.write_text(
        'import { foo } from "./util";\nexport const label = "no call";\n',
        encoding="utf-8",
    )
    caller_path.write_text(
        'import { foo } from "./util";\nexport function callFoo() {\n  return foo();\n}\n',
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius("foo", project)

    caller_files = {caller["file"] for caller in payload["callers"]}
    assert str(caller_path.resolve()) in caller_files
    assert str(import_only_path.resolve()) not in caller_files
    assert set(payload["import_graph_consumer_files"]) == {
        str(caller_path.resolve()),
        str(import_only_path.resolve()),
    }
    assert payload["import_graph_consumer_count"] == 2
    assert any(
        match["path"] == str(import_only_path.resolve()) and "import-consumer" in match["reasons"]
        for match in payload["file_matches"]
    )


def test_build_symbol_refs_resolves_javascript_import_aliases(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    util_path = src_dir / "util.js"
    consumer_path = src_dir / "consumer.js"
    util_path.write_text(
        "export function foo() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        'import { foo as bar } from "./util";\nconst value = bar();\n',
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
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
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
        "use crate::billing::Type;\n\npub fn build() {\n    let _ = Type;\n}\n",
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
        "def foo():\n    return 1\n",
        encoding="utf-8",
    )
    integration_test_path.write_text(
        "from src.core import foo\n\ndef test_flow():\n    assert foo() == 1\n",
        encoding="utf-8",
    )
    unrelated_test_path.write_text(
        "def test_other():\n    assert 1 == 1\n",
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
        "def foo():\n    return 1\n",
        encoding="utf-8",
    )
    integration_test_path.write_text(
        "from src.core import foo\n\ndef test_flow():\n    assert foo() == 1\n",
        encoding="utf-8",
    )
    unrelated_test_path.write_text(
        "def test_other():\n    assert 1 == 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_impact("foo", project)

    assert payload["tests"][0] == str(integration_test_path.resolve())
    assert str(unrelated_test_path.resolve()) not in payload["tests"]


# ---------------------------------------------------------------------------------------------
# audit #81 #3: `from . import x` -- invisible in the callers/blast-radius import-graph-consumer
# path (recall gap; #460 already fixed the sibling `tg imports`/`tg importers` primitive for the
# exact same import shape).
# ---------------------------------------------------------------------------------------------


def test_build_symbol_callers_recalls_python_bare_relative_import_consumer(
    tmp_path: Path,
) -> None:
    """`from . import helpers` has no dotted `node.module` text -- `main.py` never references
    "foo" anywhere in its own source, so the ONLY way it can appear here is via the
    import-graph-consumer path (`_python_file_imports_symbol_from_definition` /
    `_python_import_update_target`), not the direct call-site scan. Before the fix, the
    `if not node.module: continue` guard in both functions silently dropped this shape."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    main_path = pkg / "main.py"
    main_path.write_text("from . import helpers\n", encoding="utf-8")

    payload = repo_map.build_symbol_callers("foo", project)

    caller_files = {caller["file"] for caller in payload["callers"]}
    assert str(main_path.resolve()) not in caller_files

    consumer_files = set(payload["import_graph_consumer_files"])
    assert str(main_path.resolve()) in consumer_files
    consumer = next(
        current
        for current in payload["import_graph_consumers"]
        if current["file"] == str(main_path.resolve())
    )
    assert consumer["module"] == "helpers"
    assert consumer["definition_file"] == str(helpers_path.resolve())
    assert consumer["kind"] == "import-consumer"
    assert consumer["edge_kind"] == "reverse-import"


def test_build_symbol_blast_radius_recalls_python_bare_relative_import_consumer(
    tmp_path: Path,
) -> None:
    """Same shape as the callers test above, through the blast-radius payload (which wraps
    `build_symbol_callers_from_map` internally, so both consume the same fix)."""
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    helpers_path = pkg / "helpers.py"
    helpers_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
    main_path = pkg / "main.py"
    main_path.write_text("from . import helpers\n", encoding="utf-8")

    payload = repo_map.build_symbol_blast_radius("foo", project)

    assert str(main_path.resolve()) in set(payload["import_graph_consumer_files"])


# ---------------------------------------------------------------------------------------------
# audit #81 #4: Go's LanguageSpec has import_update_target=None -> import_graph_consumers can
# never be populated for Go, and (with the grammar installed) resolution_gaps stayed silently
# empty about it -- an honesty-floor gap, not a recall gap: the reverse-import edge is a real,
# permanent capability hole, so it must be SURFACED, not silently read as "proven zero".
# ---------------------------------------------------------------------------------------------


def test_build_symbol_callers_flags_go_import_graph_gap_not_silent_empty(
    tmp_path: Path,
) -> None:
    """The direct package-qualified call-site scan (independent of import_update_target) still
    finds the real cross-package call, proving Go support otherwise works -- only the
    reverse-import-graph edge is missing, and that absence must be an honest resolution_gaps
    entry rather than a silent empty list."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    util_dir = project / "util"
    util_dir.mkdir()
    util_go = util_dir / "util.go"
    util_go.write_text(
        "package util\n\nfunc Foo() int {\n\treturn 1\n}\n",
        encoding="utf-8",
    )
    main_go = project / "main.go"
    main_go.write_text(
        'package main\n\nimport "example.com/app/util"\n\nfunc main() {\n\tutil.Foo()\n}\n',
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("Foo", project)

    caller_files = {caller["file"] for caller in payload["callers"]}
    assert str(main_go.resolve()) in caller_files, "Go direct call-site resolution must still work"

    assert payload["import_graph_consumer_count"] == 0

    go_gaps = [gap for gap in payload["resolution_gaps"] if gap["language"] == "go"]
    assert len(go_gaps) == 1, (
        f"expected exactly one honest 'go' resolution_gaps entry: {payload['resolution_gaps']}"
    )
    assert go_gaps[0]["files_affected"] >= 1
    assert "reverse-import" in go_gaps[0]["reason"]
    assert "fail-closed" not in go_gaps[0]["reason"]
