"""F7 Task 11 wave 3 -- C/C++ cross-file caller resolution (include-path).

Product observable (design exit criterion): a caller in a DIFFERENT file appears in
``blast_radius_floor`` AND is BOUND to the selected definition via include-path
confirmation -- not a bare literal-name match.

Shared engine + two adapters (``lang_c_cpp_include`` / ``lang_c`` / ``lang_cpp``).

MUTATION-PROOF (run after GREEN):
  Force the cross-file include confirmation helpers in ``c_references_and_calls`` /
  ``cpp_references_and_calls`` to ``return False`` unconditionally. Named tests that MUST go
  red under THAT mutation:
      - ``test_c_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
      - ``test_c_cross_file_call_row_uses_confirmed_include_band``
      - ``test_cpp_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
      - ``test_cpp_cross_file_call_row_uses_confirmed_include_band``
  (``test_*_file_imports_symbol_from_definition_accepts_included_definition`` stays green --
  it asserts bool visibility, not confidence.)
  Revert the mutation; those tests must go green again with the language modules byte-identical.

Expected confidence/provenance values are LITERALS (0.9 /
``"c-include-path-confirmation"`` / ``"cpp-include-path-confirmation"``), not the module
constants -- a mutation that reassigns the constants must not hide behind a test reading its
"expected" value from those same constants (Java wave 1 receipt).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import lang_c, lang_cpp, lang_registry, repo_map
from tensor_grep.cli.prepare_service import _build_prepare_blast_radius_floor


def _c_include_fixture(root: Path) -> dict[str, Path]:
    """lib/ + decoy/ + app/: caller includes lib.h; decoy also exports get_count but is unincluded.

    Selected definition = lib/lib.c get_count. Cross-file caller.c must bind via quoted include
    resolving to lib.h (same-stem sibling of lib.c). Decoy must not earn file_imports True.
    """
    lib_h = root / "lib" / "lib.h"
    lib_h.parent.mkdir(parents=True, exist_ok=True)
    lib_h.write_text("int get_count(void);\n", encoding="utf-8")

    lib_c = root / "lib" / "lib.c"
    lib_c.write_text(
        '#include "lib.h"\n\nint get_count(void) {\n    return 1;\n}\n',
        encoding="utf-8",
    )

    decoy_h = root / "decoy" / "decoy.h"
    decoy_h.parent.mkdir(parents=True, exist_ok=True)
    decoy_h.write_text("int get_count(void);\n", encoding="utf-8")

    decoy_c = root / "decoy" / "decoy.c"
    decoy_c.write_text(
        '#include "decoy.h"\n\nint get_count(void) {\n    return 99;\n}\n',
        encoding="utf-8",
    )

    caller = root / "app" / "caller.c"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        '#include "../lib/lib.h"\n\nint use_lib(void) {\n    return get_count();\n}\n',
        encoding="utf-8",
    )
    return {"lib_c": lib_c, "lib_h": lib_h, "decoy_c": decoy_c, "caller": caller}


def _cpp_include_fixture(root: Path) -> dict[str, Path]:
    """Same layout as the C fixture with C++ suffixes / a namespaced-free free function."""
    lib_h = root / "lib" / "lib.hpp"
    lib_h.parent.mkdir(parents=True, exist_ok=True)
    lib_h.write_text("int get_count();\n", encoding="utf-8")

    lib_cpp = root / "lib" / "lib.cpp"
    lib_cpp.write_text(
        '#include "lib.hpp"\n\nint get_count() {\n    return 1;\n}\n',
        encoding="utf-8",
    )

    decoy_h = root / "decoy" / "decoy.hpp"
    decoy_h.parent.mkdir(parents=True, exist_ok=True)
    decoy_h.write_text("int get_count();\n", encoding="utf-8")

    decoy_cpp = root / "decoy" / "decoy.cpp"
    decoy_cpp.write_text(
        '#include "decoy.hpp"\n\nint get_count() {\n    return 99;\n}\n',
        encoding="utf-8",
    )

    caller = root / "app" / "caller.cpp"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        '#include "../lib/lib.hpp"\n\nint use_lib() {\n    return get_count();\n}\n',
        encoding="utf-8",
    )
    return {"lib_cpp": lib_cpp, "lib_h": lib_h, "decoy_cpp": decoy_cpp, "caller": caller}


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_c_file_imports_symbol_from_definition_is_wired() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["c"]
    assert spec.file_imports_symbol_from_definition is not None
    assert spec.file_imports_symbol_from_definition is (
        lang_c.c_file_imports_symbol_from_definition
    )


@pytest.mark.requires_grammar
def test_c_file_imports_symbol_from_definition_accepts_included_definition(
    tmp_path: Path,
) -> None:
    paths = _c_include_fixture(tmp_path)
    source = paths["caller"].read_text(encoding="utf-8")

    assert lang_c.c_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "get_count",
        str(paths["lib_c"]),
        tmp_path,
    )
    assert not lang_c.c_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "get_count",
        str(paths["decoy_c"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_c_file_imports_demotes_when_include_does_not_resolve(tmp_path: Path) -> None:
    """Unresolvable / system-only includes must fail closed (False), never guess."""
    definition = tmp_path / "lib.c"
    definition.write_text("int get_count(void) { return 1; }\n", encoding="utf-8")
    caller = tmp_path / "caller.c"
    caller.write_text(
        "#include <stdio.h>\n\nint use(void) { return get_count(); }\n",
        encoding="utf-8",
    )
    assert not lang_c.c_file_imports_symbol_from_definition(
        caller,
        caller.read_text(encoding="utf-8"),
        "get_count",
        str(definition),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_c_cross_file_call_row_uses_confirmed_include_band(tmp_path: Path) -> None:
    _c_include_fixture(tmp_path)
    payload = repo_map.build_symbol_callers("get_count", tmp_path)

    assert not payload.get("no_match"), payload
    caller_rows = [row for row in payload["callers"] if Path(str(row["file"])).name == "caller.c"]
    assert caller_rows, payload["callers"]
    for row in caller_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["c-include-path-confirmation"], row


@pytest.mark.requires_grammar
def test_c_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition(
    tmp_path: Path,
) -> None:
    paths = _c_include_fixture(tmp_path)
    rm = repo_map.build_repo_map(tmp_path)
    target = {
        "symbol": "get_count",
        "file": str(paths["lib_c"]),
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

    assert floor.get("symbol") == "get_count", floor
    assert floor.get("source") == "supplementary_blast_radius", floor
    top = floor.get("top_callers") or []
    caller_names = {Path(str(row.get("file") or "")).name for row in top}
    assert "caller.c" in caller_names, (floor, caller_names)

    radius = repo_map.build_symbol_blast_radius_from_map(rm, "get_count")
    bound_rows = [
        row for row in (radius.get("callers") or []) if Path(str(row["file"])).name == "caller.c"
    ]
    assert bound_rows, radius
    for row in bound_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["c-include-path-confirmation"], row
        assert Path(str(row["file"])).resolve() == paths["caller"].resolve()


# ---------------------------------------------------------------------------
# C++
# ---------------------------------------------------------------------------


@pytest.mark.requires_grammar
def test_cpp_file_imports_symbol_from_definition_is_wired() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["cpp"]
    assert spec.file_imports_symbol_from_definition is not None
    assert spec.file_imports_symbol_from_definition is (
        lang_cpp.cpp_file_imports_symbol_from_definition
    )


@pytest.mark.requires_grammar
def test_cpp_file_imports_symbol_from_definition_accepts_included_definition(
    tmp_path: Path,
) -> None:
    paths = _cpp_include_fixture(tmp_path)
    source = paths["caller"].read_text(encoding="utf-8")

    assert lang_cpp.cpp_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "get_count",
        str(paths["lib_cpp"]),
        tmp_path,
    )
    assert not lang_cpp.cpp_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "get_count",
        str(paths["decoy_cpp"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_cpp_cross_file_call_row_uses_confirmed_include_band(tmp_path: Path) -> None:
    _cpp_include_fixture(tmp_path)
    payload = repo_map.build_symbol_callers("get_count", tmp_path)

    assert not payload.get("no_match"), payload
    caller_rows = [row for row in payload["callers"] if Path(str(row["file"])).name == "caller.cpp"]
    assert caller_rows, payload["callers"]
    for row in caller_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["cpp-include-path-confirmation"], row


@pytest.mark.requires_grammar
def test_cpp_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition(
    tmp_path: Path,
) -> None:
    paths = _cpp_include_fixture(tmp_path)
    rm = repo_map.build_repo_map(tmp_path)
    target = {
        "symbol": "get_count",
        "file": str(paths["lib_cpp"]),
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

    assert floor.get("symbol") == "get_count", floor
    assert floor.get("source") == "supplementary_blast_radius", floor
    top = floor.get("top_callers") or []
    caller_names = {Path(str(row.get("file") or "")).name for row in top}
    assert "caller.cpp" in caller_names, (floor, caller_names)

    radius = repo_map.build_symbol_blast_radius_from_map(rm, "get_count")
    bound_rows = [
        row for row in (radius.get("callers") or []) if Path(str(row["file"])).name == "caller.cpp"
    ]
    assert bound_rows, radius
    for row in bound_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["cpp-include-path-confirmation"], row
        assert Path(str(row["file"])).resolve() == paths["caller"].resolve()
