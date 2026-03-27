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


def _caller_entries(payload: dict[str, object], *, file_path: Path) -> list[dict[str, object]]:
    resolved = str(file_path.resolve())
    return [
        dict(current)
        for current in payload["callers"]
        if current["file"] == resolved
    ]


def _reference_entries(payload: dict[str, object], *, file_path: Path) -> list[dict[str, object]]:
    resolved = str(file_path.resolve())
    return [
        dict(current)
        for current in payload["references"]
        if current["file"] == resolved
    ]


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_default_import_resolution_tracks_aliases_for_callers_and_import_updates(
    tmp_path: Path, suffix: str
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export default function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import makeInvoice from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")
    import_update = _suggested_edit(seed, file_path=service_path, edit_kind="import-update")
    caller_update = _suggested_edit(seed, file_path=service_path, edit_kind="caller-update")
    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert import_update["start_line"] == 1
    assert import_update["end_line"] == 1
    assert caller_update["start_line"] == 4
    assert caller_update["end_line"] == 4
    assert len(callers) == 1
    assert callers[0]["line"] == 4
    assert "default-import" in callers[0]["resolution_provenance"]
    assert float(callers[0]["resolution_confidence"]) >= 0.5


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_default_import_references_include_resolution_metadata(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export default function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import makeInvoice from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  const first = makeInvoice(total);\n"
        "  return first;\n"
        "}\n",
    )

    refs_payload = repo_map.build_symbol_refs("createInvoice", project)
    refs = _reference_entries(refs_payload, file_path=service_path)

    assert any(ref["line"] == 4 for ref in refs)
    assert any("default-import" in ref["resolution_provenance"] for ref in refs)
    assert all(float(ref["resolution_confidence"]) >= 0.5 for ref in refs)


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_default_import_missing_default_export_returns_no_resolution(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import makeInvoice from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )

    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    refs_payload = repo_map.build_symbol_refs("createInvoice", project)

    assert _caller_entries(callers_payload, file_path=service_path) == []
    assert _reference_entries(refs_payload, file_path=service_path) == []


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_named_re_export_chain_resolves_to_original_definition(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(
        src_dir / f"barrel{suffix}",
        'export { createInvoice } from "./payments";\n',
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import { createInvoice } from "./barrel";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")
    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert _suggested_edit(seed, file_path=service_path, edit_kind="import-update")["start_line"] == 1
    assert len(callers) == 1
    assert callers[0]["line"] == 4
    assert "re-export-chain" in callers[0]["resolution_provenance"]


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_aliased_re_export_chain_resolves_to_original_definition(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(
        src_dir / f"barrel{suffix}",
        'export { createInvoice as makeInvoice } from "./payments";\n',
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import { makeInvoice } from "./barrel";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")
    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert _suggested_edit(seed, file_path=service_path, edit_kind="import-update")["start_line"] == 1
    assert len(callers) == 1
    assert callers[0]["line"] == 4
    assert "re-export-chain" in callers[0]["resolution_provenance"]
    assert float(callers[0]["resolution_confidence"]) >= 0.5


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_default_import_follows_re_exported_default(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export default function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(
        src_dir / f"barrel{suffix}",
        'export { default } from "./payments";\n',
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import makeInvoice from "./barrel";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )

    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert len(callers) == 1
    assert callers[0]["line"] == 4
    assert "default-import" in callers[0]["resolution_provenance"]
    assert "re-export-chain" in callers[0]["resolution_provenance"]


def test_circular_re_export_chain_degrades_gracefully(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    a_path = src_dir / "a.ts"
    _write(a_path, 'export { createInvoice } from "./b";\n')
    _write(src_dir / "b.ts", 'export { createInvoice } from "./a";\n')

    resolution = repo_map._js_ts_resolve_exported_symbol(a_path, "createInvoice", project)

    assert resolution is None


def test_re_export_chain_stops_after_depth_five(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "mod0.ts",
        "export function createInvoice(total: number) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(src_dir / "mod1.ts", 'export { createInvoice as invoiceFn } from "./mod0";\n')
    _write(src_dir / "mod2.ts", 'export { invoiceFn } from "./mod1";\n')
    _write(src_dir / "mod3.ts", 'export { invoiceFn } from "./mod2";\n')
    _write(src_dir / "mod4.ts", 'export { invoiceFn } from "./mod3";\n')
    _write(src_dir / "mod5.ts", 'export { invoiceFn } from "./mod4";\n')
    mod6_path = src_dir / "mod6.ts"
    _write(mod6_path, 'export { invoiceFn } from "./mod5";\n')

    resolution = repo_map._js_ts_resolve_exported_symbol(mod6_path, "invoiceFn", project)

    assert resolution is None


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_tsconfig_path_alias_resolution_matches_definition(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    _write(
        project / "tsconfig.json",
        '{\n'
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": {\n'
        '      "@app/*": ["src/*"]\n'
        "    }\n"
        "  }\n"
        "}\n",
    )
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import { createInvoice } from "@app/payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")
    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert _suggested_edit(seed, file_path=service_path, edit_kind="import-update")["start_line"] == 1
    assert len(callers) == 1
    assert "tsconfig-path-alias" in callers[0]["resolution_provenance"]
    assert float(callers[0]["resolution_confidence"]) >= 0.5


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_tsconfig_base_url_resolution_matches_definition(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    _write(
        project / "tsconfig.json",
        '{\n'
        '  "compilerOptions": {\n'
        '    "baseUrl": "."\n'
        "  }\n"
        "}\n",
    )
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import { createInvoice } from "src/payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    seed = _edit_plan_seed(project, "createInvoice")
    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert _suggested_edit(seed, file_path=service_path, edit_kind="import-update")["start_line"] == 1
    assert len(callers) == 1
    assert "tsconfig-base-url" in callers[0]["resolution_provenance"]


def test_missing_tsconfig_uses_partial_resolution_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    importer_path = src_dir / "service.ts"
    definition_path = src_dir / "payments.ts"
    _write(
        definition_path,
        "export function createInvoice(total: number) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(importer_path, 'import { createInvoice } from "src/payments";\n')

    details = repo_map._js_ts_module_match_details(
        importer_path,
        "src/payments",
        str(definition_path.resolve()),
        project,
    )

    assert details["matched"] is True
    assert "partial-resolution" in details["provenance"]
    assert float(details["confidence"]) < 0.3


def test_tsconfig_is_parsed_once_per_repo_map_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    _write(
        project / "tsconfig.json",
        '{\n'
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": {\n'
        '      "@app/*": ["src/*"]\n'
        "    }\n"
        "  }\n"
        "}\n",
    )
    src_dir = project / "src"
    _write(
        src_dir / "payments.ts",
        "export function createInvoice(total: number) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    _write(
        src_dir / "service.ts",
        'import { createInvoice } from "@app/payments";\n'
        "\n"
        "export function buildReceipt(total: number) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    calls = {"count": 0}
    original = repo_map._parse_js_ts_tsconfig

    def _counting_parse(root: Path) -> dict[str, object]:
        calls["count"] += 1
        return original(root)

    monkeypatch.setattr(repo_map, "_parse_js_ts_tsconfig", _counting_parse)

    payload = repo_map.build_repo_map(project)
    repo_map.build_symbol_callers_from_map(payload, "createInvoice")
    repo_map.build_symbol_refs_from_map(payload, "createInvoice")

    assert calls["count"] == 1


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_existing_named_import_resolution_still_works(tmp_path: Path, suffix: str) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / f"payments{suffix}",
        "export function createInvoice(total) {\n"
        "  return total + 1;\n"
        "}\n",
    )
    service_path = src_dir / f"service{suffix}"
    _write(
        service_path,
        'import { createInvoice } from "./payments";\n'
        "\n"
        "export function buildReceipt(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )

    callers_payload = repo_map.build_symbol_callers("createInvoice", project)
    callers = _caller_entries(callers_payload, file_path=service_path)

    assert len(callers) == 1
    assert callers[0]["line"] == 4
