"""F7 Task 11 wave 1 -- Java cross-file caller resolution (package / source-root).

Product observable (design exit criterion): a caller in a DIFFERENT file appears in
``blast_radius_floor`` AND is BOUND to the selected definition via package/source-root
import confirmation -- not a bare literal-name match.

MUTATION-PROOF (run after GREEN):
  CORRECTED 2026-08-05 audit: the two constant-collision mutations this docstring originally
  recommended (``_JAVA_CONFIRMED_CONFIDENCE = _JAVA_DEMOTED_CONFIDENCE`` and
  ``_JAVA_CROSS_FILE_CONFIRMED_PROVENANCE = _JAVA_DEMOTED_PROVENANCE``) do NOT discriminate --
  verified by actually running them. Both assertions below used to read their "expected" value
  from ``lang_java._JAVA_CONFIRMED_CONFIDENCE`` / ``_JAVA_CROSS_FILE_CONFIRMED_PROVENANCE``, the
  SAME constants the mutation reassigns, so mutated-actual == mutated-expected and both arms
  passed green (sha256 confirmed the mutation really landed in the file). Fixed by pinning the
  assertions to LITERALS (0.9 / "java-package-type-confirmation") so the test is an independent
  witness. The mechanism itself is sound -- proved by mutating the LOGIC instead: force
  ``_cross_file_receiver_confirmation`` in ``java_references_and_calls`` to ``return False``
  unconditionally. Named tests that MUST go red under THAT mutation:
      - ``test_java_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition``
      - ``test_java_cross_file_call_row_uses_confirmed_package_band``
  (``test_java_file_imports_symbol_from_definition_accepts_imported_definition`` stays green --
  it asserts bool visibility, not confidence.)
  Revert the mutation; those tests must go green again with ``lang_java.py`` byte-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import lang_java, lang_registry, repo_map
from tensor_grep.cli.prepare_service import _build_prepare_blast_radius_floor


def _maven_java_fixture(root: Path) -> dict[str, Path]:
    """Multi-module Maven layout: Caller imports com.lib.Foo; decoy com.other.Foo is unimported.

    Selected definition = lib Foo.getCount. Cross-file Caller.java must bind via import +
    source-root mapping. Decoy Foo shares the method name but must not earn the confirmed band
    (and must not make file_imports claim Caller sees the decoy).
    """
    lib_foo = root / "lib" / "src" / "main" / "java" / "com" / "lib" / "Foo.java"
    lib_foo.parent.mkdir(parents=True, exist_ok=True)
    lib_foo.write_text(
        "package com.lib;\n"
        "\n"
        "public class Foo {\n"
        "    public int getCount() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    decoy_foo = root / "decoy" / "src" / "main" / "java" / "com" / "other" / "Foo.java"
    decoy_foo.parent.mkdir(parents=True, exist_ok=True)
    decoy_foo.write_text(
        "package com.other;\n"
        "\n"
        "public class Foo {\n"
        "    public int getCount() {\n"
        "        return 99;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    caller = root / "app" / "src" / "main" / "java" / "com" / "app" / "Caller.java"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(
        "package com.app;\n"
        "\n"
        "import com.lib.Foo;\n"
        "\n"
        "public class Caller {\n"
        "    public int useLib() {\n"
        "        Foo foo = new Foo();\n"
        "        return foo.getCount();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    return {"lib_foo": lib_foo, "decoy_foo": decoy_foo, "caller": caller}


@pytest.mark.requires_grammar
def test_java_file_imports_symbol_from_definition_is_wired() -> None:
    """Import of the registry field must succeed; the assertion is the behaviour."""
    spec = lang_registry.LANGUAGE_REGISTRY["java"]
    assert spec.file_imports_symbol_from_definition is not None
    assert spec.file_imports_symbol_from_definition is (
        lang_java.java_file_imports_symbol_from_definition
    )


@pytest.mark.requires_grammar
def test_java_file_imports_symbol_from_definition_accepts_imported_definition(
    tmp_path: Path,
) -> None:
    paths = _maven_java_fixture(tmp_path)
    source = paths["caller"].read_text(encoding="utf-8")

    assert lang_java.java_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "getCount",
        str(paths["lib_foo"]),
        tmp_path,
    )
    # Decoy: same simple type name, different package -- must NOT resolve.
    assert not lang_java.java_file_imports_symbol_from_definition(
        paths["caller"],
        source,
        "getCount",
        str(paths["decoy_foo"]),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_java_file_imports_demotes_when_source_root_cannot_be_established(
    tmp_path: Path,
) -> None:
    """Flat files with no package/src/main/java mapping must fail closed (False), never guess."""
    definition = tmp_path / "Foo.java"
    definition.write_text(
        "public class Foo {\n    public int getCount() { return 1; }\n}\n",
        encoding="utf-8",
    )
    caller = tmp_path / "Caller.java"
    caller.write_text(
        "import com.lib.Foo;\n"
        "public class Caller {\n"
        "    int use(Foo f) { return f.getCount(); }\n"
        "}\n",
        encoding="utf-8",
    )
    assert not lang_java.java_file_imports_symbol_from_definition(
        caller,
        caller.read_text(encoding="utf-8"),
        "getCount",
        str(definition),
        tmp_path,
    )


@pytest.mark.requires_grammar
def test_java_cross_file_call_row_uses_confirmed_package_band(tmp_path: Path) -> None:
    """Behaviour-specific RED: today the cross-file call is demoted (0.6 / java-name-heuristic).

    After wave 1, package + source-root confirmation must lift Caller.java's call to the
    confirmed band. Import of ``lang_java`` / ``repo_map`` succeeds before this assertion --
    an ImportError would be a FALSE red.

    Expected values are LITERALS (0.9 / "java-package-type-confirmation"), not
    ``lang_java._JAVA_CONFIRMED_CONFIDENCE`` / ``_JAVA_CROSS_FILE_CONFIRMED_PROVENANCE``, on
    purpose: the module docstring's mutation-proof collapses the confirmed band onto the demoted
    one by reassigning those exact constants, and a test that reads its "expected" value from the
    same mutated constant can never observe the mutation (verified -- both suggested mutations
    passed green against the module-attribute form). A literal is an independent witness.
    """
    _maven_java_fixture(tmp_path)
    payload = repo_map.build_symbol_callers("getCount", tmp_path)

    assert not payload.get("no_match"), payload
    caller_rows = [
        row for row in payload["callers"] if Path(str(row["file"])).name == "Caller.java"
    ]
    assert caller_rows, payload["callers"]
    for row in caller_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["java-package-type-confirmation"], row


@pytest.mark.requires_grammar
def test_java_cross_file_caller_appears_in_blast_radius_floor_bound_to_selected_definition(
    tmp_path: Path,
) -> None:
    """Exit criterion: cross-file Caller in blast_radius_floor, bound to selected lib Foo.

    Binding is proved by the confirmed package band on the caller rows that feed the floor
    (floor top_callers alone strip resolution_* fields today -- see prepare_service). A
    pre-fix tree already admits Caller via literal-name prefilter at the DEMOTED band; this
    test fails until confirmation elevates the binding, and still requires Caller in the floor.
    """
    paths = _maven_java_fixture(tmp_path)
    rm = repo_map.build_repo_map(tmp_path)
    target = {
        "symbol": "getCount",
        "file": str(paths["lib_foo"]),
        "confidence": 0.9,
    }
    # Force the supplementary blast-radius path (capsule call-site evidence skipped).
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

    assert floor.get("symbol") == "getCount", floor
    assert floor.get("source") == "supplementary_blast_radius", floor
    top = floor.get("top_callers") or []
    caller_names = {Path(str(row.get("file") or "")).name for row in top}
    assert "Caller.java" in caller_names, (floor, caller_names)

    # Binding to the SELECTED definition (lib Foo), not literal name alone. Expected values are
    # LITERALS, not the module constants -- see the sibling test's docstring for why (a mutation
    # that reassigns the constant must not be able to hide behind a test reading its "expected"
    # value from that same constant).
    radius = repo_map.build_symbol_blast_radius_from_map(rm, "getCount")
    bound_rows = [
        row for row in (radius.get("callers") or []) if Path(str(row["file"])).name == "Caller.java"
    ]
    assert bound_rows, radius
    for row in bound_rows:
        assert row["resolution_confidence"] == 0.9, row
        assert row["resolution_provenance"] == ["java-package-type-confirmation"], row
        assert Path(str(row["file"])).resolve() == paths["caller"].resolve()
