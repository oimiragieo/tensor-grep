from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _edit_plan_seed(project: Path, symbol: str) -> dict[str, object]:
    payload = repo_map.build_symbol_blast_radius_render(symbol, project)
    return dict(payload["edit_plan_seed"])


def _suggested_edit(
    seed: dict[str, object], *, file_path: Path, edit_kind: str
) -> dict[str, object]:
    resolved = str(file_path.resolve())
    for current in seed["suggested_edits"]:
        if current["file"] == resolved and current["edit_kind"] == edit_kind:
            return dict(current)
    raise AssertionError(f"missing {edit_kind!r} edit for {resolved}")


def _caller_entries(payload: dict[str, object], *, file_path: Path) -> list[dict[str, object]]:
    resolved = str(file_path.resolve())
    return [dict(current) for current in payload["callers"] if current["file"] == resolved]


def _reference_entries(payload: dict[str, object], *, file_path: Path) -> list[dict[str, object]]:
    resolved = str(file_path.resolve())
    return [dict(current) for current in payload["references"] if current["file"] == resolved]


def _workspace_fixture(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    _write(
        project / "Cargo.toml",
        '[workspace]\nmembers = ["app", "shared"]\n',
    )
    _write(
        project / "shared" / "Cargo.toml",
        '[package]\nname = "shared"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        project / "shared" / "src" / "lib.rs",
        "pub mod billing;\n",
    )
    shared_billing = project / "shared" / "src" / "billing.rs"
    _write(
        shared_billing,
        "pub fn issue_invoice() -> usize {\n    1\n}\n\npub struct Invoice;\n",
    )
    _write(
        project / "app" / "Cargo.toml",
        '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    app_lib = project / "app" / "src" / "lib.rs"
    _write(
        app_lib,
        "use shared::billing::{Invoice, issue_invoice as dispatch};\n"
        "\n"
        "pub fn settle() -> (Invoice, usize) {\n"
        "    (Invoice, dispatch())\n"
        "}\n",
    )
    local_invoice = project / "app" / "src" / "local_invoice.rs"
    _write(
        local_invoice,
        "pub struct Invoice;\n",
    )
    return {
        "project": project,
        "shared_billing": shared_billing,
        "app_lib": app_lib,
        "local_invoice": local_invoice,
    }


def test_rust_mod_tree_maps_pub_mod_declarations_from_lib_rs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    entry_path = project / "src" / "lib.rs"
    _write(entry_path, "pub mod billing;\n")
    billing_path = project / "src" / "billing.rs"
    _write(billing_path, "pub mod models;\n")
    models_path = project / "src" / "billing" / "models.rs"
    _write(models_path, "pub struct Invoice;\n")

    module_tree = repo_map._build_rust_module_tree(entry_path)

    assert module_tree["billing"] == str(billing_path.resolve())
    assert module_tree["billing::models"] == str(models_path.resolve())


def test_rust_mod_tree_maps_mod_declarations_from_main_rs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    entry_path = project / "src" / "main.rs"
    cli_path = project / "src" / "cli.rs"
    _write(entry_path, "mod cli;\n")
    _write(cli_path, "pub fn run() {}\n")

    module_tree = repo_map._build_rust_module_tree(entry_path)

    assert module_tree["cli"] == str(cli_path.resolve())


def test_rust_module_match_details_prefer_mod_declaration_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    importer_path = project / "src" / "consumer.rs"
    declared_billing = project / "src" / "billing.rs"
    unrelated_billing = project / "other" / "billing.rs"
    _write(project / "src" / "lib.rs", "pub mod billing;\n")
    _write(declared_billing, "pub struct Invoice;\n")
    _write(unrelated_billing, "pub struct Invoice;\n")
    _write(importer_path, "use crate::billing::Invoice;\n")

    declared = repo_map._rust_module_match_details(
        importer_path,
        "crate::billing",
        str(declared_billing.resolve()),
        project,
    )
    unrelated = repo_map._rust_module_match_details(
        importer_path,
        "crate::billing",
        str(unrelated_billing.resolve()),
        project,
    )

    assert declared["matched"] is True
    assert "mod-declaration" in declared["provenance"]
    assert float(declared["confidence"]) >= 0.9
    assert unrelated["matched"] is False


def test_workspace_cross_crate_resolution_prefers_workspace_member_definition(
    tmp_path: Path,
) -> None:
    fixture = _workspace_fixture(tmp_path)

    payload = repo_map.build_symbol_impact("Invoice", fixture["project"])

    assert payload["files"][0] == str(fixture["shared_billing"].resolve())
    assert str(fixture["app_lib"].resolve()) in payload["files"][:2]
    assert str(fixture["local_invoice"].resolve()) not in payload["files"][:2]


def test_workspace_cross_crate_import_updates_target_use_statement(tmp_path: Path) -> None:
    fixture = _workspace_fixture(tmp_path)

    seed = _edit_plan_seed(fixture["project"], "issue_invoice")
    import_update = _suggested_edit(seed, file_path=fixture["app_lib"], edit_kind="import-update")

    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1


def test_workspace_cross_crate_callers_include_resolution_metadata(tmp_path: Path) -> None:
    fixture = _workspace_fixture(tmp_path)

    payload = repo_map.build_symbol_callers("issue_invoice", fixture["project"])
    callers = _caller_entries(payload, file_path=fixture["app_lib"])

    assert len(callers) == 1
    assert callers[0]["line"] == 4
    assert "workspace-crate" in callers[0]["resolution_provenance"]
    assert "mod-declaration" in callers[0]["resolution_provenance"]
    assert float(callers[0]["resolution_confidence"]) >= 0.5


def test_workspace_cross_crate_references_include_resolution_metadata(tmp_path: Path) -> None:
    fixture = _workspace_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("issue_invoice", fixture["project"])
    refs = _reference_entries(payload, file_path=fixture["app_lib"])

    assert any(ref["line"] == 4 for ref in refs)
    assert any("workspace-crate" in ref["resolution_provenance"] for ref in refs)
    assert all(float(ref["resolution_confidence"]) >= 0.5 for ref in refs)


def test_missing_workspace_cargo_uses_partial_resolution_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    importer_path = project / "app" / "src" / "lib.rs"
    definition_path = project / "shared" / "src" / "lib.rs"
    _write(importer_path, "use shared::Invoice;\n")
    _write(definition_path, "pub struct Invoice;\n")

    details = repo_map._rust_module_match_details(
        importer_path,
        "shared",
        str(definition_path.resolve()),
        project,
    )

    assert details["matched"] is True
    assert "partial-resolution" in details["provenance"]
    assert float(details["confidence"]) < 0.3


def test_malformed_workspace_cargo_degrades_gracefully(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / "Cargo.toml", '[workspace\nmembers = ["app"]\n')

    workspace = repo_map._parse_rust_workspace_members(project)

    assert workspace["exists"] is False
    assert workspace["members"] == {}


def test_workspace_cargo_is_parsed_once_per_repo_map_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = _workspace_fixture(tmp_path)
    calls = {"count": 0}
    original = repo_map._parse_rust_workspace_members

    def _counting_parse(root: Path) -> dict[str, object]:
        calls["count"] += 1
        return original(root)

    monkeypatch.setattr(repo_map, "_parse_rust_workspace_members", _counting_parse)

    payload = repo_map.build_repo_map(fixture["project"])
    repo_map.build_symbol_callers_from_map(payload, "issue_invoice")
    repo_map.build_symbol_refs_from_map(payload, "issue_invoice")

    assert calls["count"] == 1


def test_existing_same_crate_rust_use_alias_resolution_still_works(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    module_path = src_dir / "billing.rs"
    consumer_path = src_dir / "consumer.rs"
    _write(
        module_path,
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
    )
    _write(
        consumer_path,
        "use crate::billing::{issue_invoice as dispatch};\n\n"
        "pub fn settle_invoice() -> usize {\n"
        "    dispatch()\n"
        "}\n",
    )

    payload = repo_map.build_symbol_callers("issue_invoice", project)

    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])
