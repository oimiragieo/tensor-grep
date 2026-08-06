"""F7 Task 11 wave 2 -- C# cross-file caller resolution (namespace / ``using``).

Product observable (design exit criterion): a caller in a DIFFERENT file appears in
``blast_radius_floor`` AND is BOUND to the selected definition via namespace/``using``
confirmation -- not a bare literal-name match.

Design note: C# is NOT ``.csproj``-manifest-backed for symbols (no manifest maps namespaces to
files). Resolution uses namespace + stem-as-type index evidence plus directory-suffix matching
of the resolved FQN's namespace against ``definition_dirs`` -- sharing only the *reader*
plumbing shape with PHP's wave 2b, not a composer-style strategy.

MUTATION-PROOF (run after GREEN): assertions below are pinned to LITERALS (0.9 /
"csharp-namespace-type-confirmation"), never to ``lang_csharp._CSHARP_CONFIRMED_CONFIDENCE`` /
``_CSHARP_CROSS_FILE_CONFIRMED_PROVENANCE``, so the test is an independent witness. Named tests
that MUST go red when ``_csharp_type_resolves_into_definition_dirs`` is forced to
``return False`` unconditionally:
    - ``test_csharp_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
    - ``test_csharp_cross_file_call_row_uses_confirmed_namespace_band``
(``test_csharp_file_imports_symbol_from_definition_accepts_imported_definition`` stays green --
it asserts bool visibility, not confidence.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import lang_csharp, lang_registry, repo_map
from tensor_grep.cli.prepare_service import _build_prepare_blast_radius_floor


def _csharp_namespace_fixture(root: Path) -> dict[str, Path]:
    """Multi-project layout: Caller imports Lib.Services.Foo; decoy Other.Services.Foo unimported.

    Selected definition = Lib Foo.GetCount. Cross-file Caller.cs must bind via ``using`` +
    namespace directory-suffix evidence. Decoy Foo shares the method name but must not earn the
    confirmed band (and must not make file_imports claim Caller sees the decoy).
    """
    lib_foo = root / "lib" / "Lib" / "Services" / "Foo.cs"
    lib_foo.parent.mkdir(parents=True, exist_ok=True)
    lib_foo.write_text(
        "namespace Lib.Services;\n"
        "\n"
        "public class Foo {\n"
        "    public int GetCount() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    decoy_foo = root / "decoy" / "Other" / "Services" / "Foo.cs"
    decoy_foo.parent.mkdir(parents=True, exist_ok=True)
    decoy_foo.write_text(
        "namespace Other.Services;\n"
        "\n"
        "public class Foo {\n"
        "    public int GetCount() {\n"
        "        return 99;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    caller = root / "app" / "App" / "Caller.cs"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        "namespace App;\n"
        "\n"
        "using Lib.Services;\n"
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
    """Import of the registry field must succeed; the assertion is the behaviour."""
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
    # Decoy: same simple type name, different namespace -- must NOT resolve.
    assert not lang_csharp.csharp_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "GetCount",
        str(paths["decoy_foo"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_csharp_file_imports_demotes_when_namespace_cannot_be_established(
    tmp_path: Path,
) -> None:
    """Flat files with no namespace declaration must fail closed (False), never guess."""
    definition = tmp_path / "Foo.cs"
    definition.write_text(
        "public class Foo {\n    public int GetCount() { return 1; }\n}\n",
        encoding="utf-8",
    )
    caller = tmp_path / "Caller.cs"
    caller.write_text(
        "using Lib.Services;\n"
        "public class Caller {\n"
        "    int Use(Foo f) { return f.GetCount(); }\n"
        "}\n",
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
def test_csharp_file_imports_demotes_when_stem_does_not_match_declared_type(
    tmp_path: Path,
) -> None:
    """Definition file stem must match declared type name -- fail closed otherwise."""
    definition = tmp_path / "NotFoo.cs"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        "namespace Lib.Services;\npublic class Foo {\n    public int GetCount() { return 1; }\n}\n",
        encoding="utf-8",
    )
    caller = tmp_path / "Caller.cs"
    caller.write_text(
        "using Lib.Services;\n"
        "public class Caller {\n"
        "    int Use(Foo f) { return f.GetCount(); }\n"
        "}\n",
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
    """Behaviour-specific: cross-file call must lift to the confirmed namespace band.

    Expected values are LITERALS (0.9 / "csharp-namespace-type-confirmation"), not the module
    constants -- see the module docstring's mutation-proof note (a mutation that reassigns the
    constant must not hide behind a test reading its expected value from that same constant).
    """
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
    """Exit criterion: cross-file Caller in blast_radius_floor, bound to selected Lib Foo."""
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
