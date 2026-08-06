"""F7 Task 11 wave 2 -- C# cross-file caller resolution (namespace / ``using``).

Product observable (design exit criterion): a caller in a DIFFERENT file appears in
``blast_radius_floor`` AND is BOUND to the selected definition via namespace/``using``
confirmation -- not a bare literal-name match.

MUTATION-PROOF: assertions below are pinned to LITERALS (0.9 /
"csharp-namespace-type-confirmation"), never to ``lang_csharp._CSHARP_CONFIRMED_CONFIDENCE`` /
``_CSHARP_CROSS_FILE_CONFIRMED_PROVENANCE``. Named tests that MUST go red when
``_csharp_type_resolves_into_definition_dirs`` is forced to ``return False`` unconditionally:
    - ``test_csharp_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
    - ``test_csharp_cross_file_call_row_uses_confirmed_namespace_band``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import lang_csharp, lang_registry, repo_map
from tensor_grep.cli.prepare_service import _build_prepare_blast_radius_floor


def _csharp_namespace_fixture(root: Path) -> dict[str, Path]:
    """Two-namespace layout: Caller imports Lib.Foo; decoy Other.Foo is unimported."""
    lib_foo = root / "Lib" / "Foo.cs"
    lib_foo.parent.mkdir(parents=True, exist_ok=True)
    lib_foo.write_text(
        "namespace Lib;\n"
        "\n"
        "public class Foo {\n"
        "    public int GetCount() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    decoy_foo = root / "Other" / "Foo.cs"
    decoy_foo.parent.mkdir(parents=True, exist_ok=True)
    decoy_foo.write_text(
        "namespace Other;\n"
        "\n"
        "public class Foo {\n"
        "    public int GetCount() {\n"
        "        return 99;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    caller = root / "App" / "Caller.cs"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        "namespace App;\n"
        "\n"
        "using Lib;\n"
        "\n"
        "public class Caller {\n"
        "    public int UseLib(Foo foo) {\n"
        "        return foo.GetCount();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return {"lib_foo": lib_foo, "decoy_foo": decoy_foo, "caller": caller}


@pytest.mark.requires_grammar
def test_csharp_file_imports_symbol_from_definition_is_wired() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["csharp"]
    assert spec.file_imports_symbol_from_definition is not None
    assert spec.file_imports_symbol_from_definition is (
        lang_csharp.csharp_file_imports_symbol_from_definition
    )


@pytest.mark.requires_grammar
def test_csharp_file_imports_symbol_from_definition_accepts_imported_definition(
    tmp_path: Path,
) -> None:
    paths = _csharp_namespace_fixture(tmp_path)
    source = paths["caller"].read_text(encoding="utf-8")

    assert lang_csharp.csharp_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "GetCount",
        str(paths["lib_foo"]),
        tmp_path,
    )
    assert not lang_csharp.csharp_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "GetCount",
        str(paths["decoy_foo"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_csharp_file_imports_demotes_when_stem_does_not_match_declared_type(
    tmp_path: Path,
) -> None:
    """Definition file stem must match declared type name -- fail closed otherwise."""
    definition = tmp_path / "NotFoo.cs"
    definition.write_text(
        "namespace Lib;\npublic class Foo {\n    public int GetCount() { return 1; }\n}\n",
        encoding="utf-8",
    )
    caller = tmp_path / "Caller.cs"
    caller.write_text(
        "using Lib;\npublic class Caller {\n    int Use(Foo f) { return f.GetCount(); }\n}\n",
        encoding="utf-8",
    )
    assert not lang_csharp.csharp_file_imports_symbol_from_definition(
        caller,
        caller.read_text(encoding="utf-8"),
        "GetCount",
        str(definition),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_csharp_cross_file_call_row_uses_confirmed_namespace_band(tmp_path: Path) -> None:
    _csharp_namespace_fixture(tmp_path)
    payload = repo_map.build_symbol_callers("GetCount", tmp_path)

    assert not payload.get("no_match"), payload
    caller_rows = [row for row in payload["callers"] if Path(str(row["file"])).name == "Caller.cs"]
    assert caller_rows, payload["callers"]
    for row in caller_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["csharp-namespace-type-confirmation"], row


@pytest.mark.requires_grammar
def test_csharp_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition(
    tmp_path: Path,
) -> None:
    paths = _csharp_namespace_fixture(tmp_path)
    rm = repo_map.build_repo_map(tmp_path)
    target = {
        "symbol": "GetCount",
        "file": str(paths["lib_foo"]),
        "confidence": 0.9,
    }
    call_site_evidence = {
        "status": "skipped",
        "reason": "primary symbol was not explicitly requested by query",
    }
    floor, _deadline_partial = _build_prepare_blast_radius_floor(
        path=str(tmp_path),
        rm=rm,
        target=target,
        call_site_evidence=call_site_evidence,
        related_call_sites=[],
        deadline_monotonic=None,
    )

    assert floor.get("symbol") == "GetCount", floor
    assert floor.get("source") == "supplementary_blast_radius", floor
    top = floor.get("top_callers") or []
    caller_names = {Path(str(row.get("file") or "")).name for row in top}
    assert "Caller.cs" in caller_names, (floor, caller_names)

    radius = repo_map.build_symbol_blast_radius_from_map(rm, "GetCount")
    bound_rows = [
        row for row in (radius.get("callers") or []) if Path(str(row["file"])).name == "Caller.cs"
    ]
    assert bound_rows, radius
    for row in bound_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["csharp-namespace-type-confirmation"], row
        assert Path(str(row["file"])).resolve() == paths["caller"].resolve()
