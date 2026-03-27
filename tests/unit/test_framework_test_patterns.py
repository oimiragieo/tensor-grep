import json
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


@pytest.fixture(autouse=True)
def _clear_framework_caches() -> None:
    for name in dir(repo_map):
        if "validation" not in name and "test_function" not in name and "framework" not in name:
            continue
        candidate = getattr(repo_map, name)
        cache_clear = getattr(candidate, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()
    yield
    for name in dir(repo_map):
        if "validation" not in name and "test_function" not in name and "framework" not in name:
            continue
        candidate = getattr(repo_map, name)
        cache_clear = getattr(candidate, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_package_json(
    project: Path,
    *,
    dev_dependencies: dict[str, str] | None = None,
) -> None:
    project.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if dev_dependencies:
        payload["devDependencies"] = dev_dependencies
    (project / "package.json").write_text(json.dumps(payload), encoding="utf-8")


def _suggested_edit(seed: dict[str, object], *, file_path: Path, edit_kind: str) -> dict[str, object]:
    resolved = str(file_path.resolve())
    for current in seed["suggested_edits"]:
        if current["file"] == resolved and current["edit_kind"] == edit_kind:
            return dict(current)
    raise AssertionError(f"missing {edit_kind!r} edit for {resolved}")


def test_context_render_associates_pytest_parametrize_tests_via_framework_pattern(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write(
        project / "src" / "payments.py",
        "def create_invoice(total):\n"
        "    return total + 1\n",
    )
    test_path = project / "tests" / "test_cases.py"
    _write(
        test_path,
        "import pytest\n\n"
        '@pytest.mark.parametrize("amount", [1, 2])\n'
        "def test_create_invoice(amount):\n"
        "    assert amount > 0\n",
    )

    payload = repo_map.build_context_render("create_invoice", project)

    assert payload["edit_plan_seed"]["validation_tests"] == [str(test_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "uv run pytest tests/test_cases.py -k test_create_invoice -q",
        "uv run pytest tests/test_cases.py -q",
        "uv run pytest -q",
    ]
    assert payload["test_matches"][0]["path"] == str(test_path.resolve())
    assert payload["test_matches"][0]["association"]["edge_kind"] == "framework-pattern"
    assert payload["test_matches"][0]["association"]["confidence"] == "moderate"
    assert "framework-pattern" in payload["test_matches"][0]["association"]["provenance"]


def test_context_render_uses_jest_test_name_pattern_for_describe_it_targets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_package_json(project, dev_dependencies={"jest": "^29.0.0"})
    _write(
        project / "src" / "payments.js",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    test_path = project / "tests" / "ui_flow.test.js"
    _write(
        test_path,
        'describe("create invoice", () => {\n'
        '  it("creates receipt", () => {\n'
        "    expect(1).toBe(1);\n"
        "  });\n"
        "});\n",
    )

    payload = repo_map.build_context_render("createInvoice", project)

    assert payload["edit_plan_seed"]["validation_tests"] == [str(test_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        'npx jest tests/ui_flow.test.js --testNamePattern "create invoice creates receipt"',
        "npx jest tests/ui_flow.test.js",
        "npx jest",
    ]
    assert payload["test_matches"][0]["path"] == str(test_path.resolve())
    assert payload["test_matches"][0]["association"]["edge_kind"] == "framework-pattern"
    assert payload["test_matches"][0]["association"]["confidence"] == "moderate"
    assert "framework-pattern" in payload["test_matches"][0]["association"]["provenance"]


def test_blast_radius_recognizes_tokio_tests_for_validation_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(
        project / "Cargo.toml",
        "[package]\n"
        'name = "sample"\n'
        'version = "0.1.0"\n',
    )
    _write(
        project / "src" / "lib.rs",
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n",
    )
    test_path = project / "tests" / "integration_checks.rs"
    _write(
        test_path,
        "#[tokio::test]\n"
        "async fn issue_invoice_smoke() {\n"
        "    assert_eq!(1, 1);\n"
        "}\n",
    )

    payload = repo_map.build_symbol_blast_radius_render("issue_invoice", project)

    assert payload["edit_plan_seed"]["validation_tests"] == [str(test_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "cargo test issue_invoice_smoke",
        "cargo test --test integration_checks",
        "cargo test",
    ]
    assert payload["test_matches"][0]["path"] == str(test_path.resolve())
    assert payload["test_matches"][0]["association"]["edge_kind"] == "framework-pattern"
    assert payload["test_matches"][0]["association"]["confidence"] == "moderate"
    assert "framework-pattern" in payload["test_matches"][0]["association"]["provenance"]


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_context_render_includes_import_updates_for_default_import_resolution(
    tmp_path: Path,
    suffix: str,
) -> None:
    project = tmp_path / "project"
    _write(
        project / "src" / f"payments{suffix}",
        "export default function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = project / "src" / f"service{suffix}"
    _write(
        service_path,
        'import makeInvoice from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )

    payload = repo_map.build_context_render("createInvoice", project)
    import_update = _suggested_edit(payload["edit_plan_seed"], file_path=service_path, edit_kind="import-update")

    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1


def test_blast_radius_includes_import_updates_for_workspace_resolution(tmp_path: Path) -> None:
    project = tmp_path / "workspace"
    _write(
        project / "Cargo.toml",
        "[workspace]\n"
        'members = ["app", "shared"]\n',
    )
    _write(
        project / "shared" / "Cargo.toml",
        "[package]\n"
        'name = "shared"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n',
    )
    _write(project / "shared" / "src" / "lib.rs", "pub mod billing;\n")
    _write(
        project / "shared" / "src" / "billing.rs",
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n",
    )
    _write(
        project / "app" / "Cargo.toml",
        "[package]\n"
        'name = "app"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n',
    )
    app_lib = project / "app" / "src" / "lib.rs"
    _write(
        app_lib,
        "use shared::billing::issue_invoice;\n\n"
        "pub fn settle() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
    )

    payload = repo_map.build_symbol_blast_radius_render("issue_invoice", project)
    import_update = _suggested_edit(payload["edit_plan_seed"], file_path=app_lib, edit_kind="import-update")

    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1
