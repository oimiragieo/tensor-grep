"""Regression coverage for diff-impact's test-reference risk cap."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.diff_impact as di
import tensor_grep.cli.diff_impact_refs as diff_impact_refs
from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app


def _dormant_diff(root: Path, test_name: str | None = None) -> str:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "core.py").write_text("def dormant():\n    return 2\n", encoding="utf-8")
    if test_name is not None:
        test_file = root / test_name
        source = (
            "package pkg\nvar observed = dormant\n"
            if test_file.suffix == ".go"
            else "from pkg.core import dormant\nobserved = dormant\n"
        )
        test_file.write_text(source, encoding="utf-8")
    return (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n+++ b/pkg/core.py\n"
        "@@ -1,2 +1,2 @@\n def dormant():\n"
        "-    return 1\n+    return 2\n"
    )


@pytest.mark.parametrize(
    "test_name",
    ["core.test.js", "core.spec.ts", "test_core.py", "core_test.go", "core.test.jsx"],
)
def test_non_call_test_reference_disables_uncalled_cap(tmp_path: Path, test_name: str) -> None:
    diff = _dormant_diff(tmp_path, test_name)
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)
    if test_name == "core.test.jsx":
        assert payload["partial"] is False
        assert payload["affected_tests"] == []
    assert "risk_reasons" in payload
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_getattr_string_reference_disables_uncalled_cap(tmp_path: Path) -> None:
    diff = _dormant_diff(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        'from pkg import core\nobserved = getattr(core, "dormant")\n', encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_tier"] == "critical"
    assert payload["risk_reasons"]
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    assert payload["risk_cap_evidence"] == "complete"


def test_imported_alias_call_disables_uncalled_cap_and_preserves_gate_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    test_file = tmp_path / "test_core.py"
    test_file.write_text("from pkg.core import dormant as observed\nobserved()\n", encoding="utf-8")

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_star_import_call_disables_uncalled_cap_and_preserves_gate_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_core.py").write_text(
        'from pkg.core import *\nglobals()["dor" + "mant"]()\n', encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_typed_module_alias_dynamic_getattr_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app\nalias: object = app\ngetattr(alias, "chan" + "ged")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    assert "reference_lookup_unreadable_paths" not in payload
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_module_import_assign_alias_getattr_disables_uncalled_cap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app_path = tmp_path / "app.py"
    app_path.write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app as module\nalias = module\ngetattr(alias, "chan" + "ged")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_tuple_module_alias_dynamic_getattr_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app\nalias, = (app,)\ngetattr(alias, "chan" + "ged")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_builtins_getattr_assigned_alias_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app, builtins\nlookup = builtins.getattr\nlookup(app, "chan" + "ged")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_builtins_module_alias_getattr_concat_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app\nimport builtins as b\nb.getattr(app, "r" + "un")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def run():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_getattr_callable_alias_dynamic_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app\nlookup = getattr\nlookup(app, chr(97))()\n",
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_container_held_module_dict_get_marks_cap_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "app.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app\nmods = [app]\nmods[0].__dict__.get(chr(97))()\n",
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_module_dict_get_dynamic_marks_cap_incomplete(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "app.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app\napp.__dict__.get(chr(97))()\n",
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_list_held_module_reference_disables_uncalled_cap(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app\n[app][0].changed()\n",
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 1
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] != "medium"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_aliased_getattr_literal_reference_disables_uncalled_cap(tmp_path: Path) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_core.py").write_text(
        'from builtins import getattr as g\nimport pkg.core as alias\ng(alias, "dor" + "mant")()\n',
        encoding="utf-8",
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_module_dict_alias_access_marks_cap_incomplete(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "app.py").write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        'import app\nalias = app\nalias.__dict__["chan" + "ged"]()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_dynamic_getattr_on_module_binding_marks_cap_incomplete(tmp_path: Path) -> None:
    app_path = tmp_path / "app.py"
    app_path.write_text("def changed():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app as module\ngetattr(module, name)()\n",
        encoding="utf-8",
    )
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n def changed():\n-    return 1\n+    return 2\n"
    )
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


@pytest.mark.parametrize(
    "source",
    [
        'import pkg.core\ngetattr(pkg.core, "dor" + "mant")()\n',
        'from pkg import core as alias\ngetattr(alias, "dor" + "mant")()\n',
    ],
    ids=["unaliased_module_import", "package_submodule_alias"],
)
def test_dynamic_getattr_concat_on_module_binding_marks_cap_incomplete(
    tmp_path: Path, source: str
) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_core.py").write_text(source, encoding="utf-8")

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_non_windows_reference_scan_keeps_ordinary_file_evidence_complete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "consumer.py"
    source.write_text("value = 1\n", encoding="utf-8")
    repo_map_payload = {"path": str(tmp_path), "files": [str(source)], "tests": []}
    monkeypatch.setattr(
        repo_map,
        "build_symbol_refs_from_map",
        lambda *args, **kwargs: {"references": [], "string_refs": []},
    )

    with monkeypatch.context() as patch:
        patch.setattr(diff_impact_refs.sys, "platform", "darwin")
        evidence = diff_impact_refs._test_reference_evidence(
            repo_map_payload, "dormant", None, None, ("pkg/core.py",)
        )

    assert evidence == (True, False)


def test_non_windows_reference_scan_confines_opened_identity(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "consumer.py"
    source.write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")
    repo_map_payload = {"path": str(tmp_path), "files": [str(source)], "tests": []}
    monkeypatch.setattr(
        repo_map,
        "build_symbol_refs_from_map",
        lambda *args, **kwargs: {"references": [], "string_refs": []},
    )
    real_open = Path.open
    real_stat = Path.stat
    opened_fd: int | None = None

    def swapped_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal opened_fd
        if path == source:
            handle = real_open(outside, *args, **kwargs)
            opened_fd = handle.fileno()
            return handle
        return real_open(path, *args, **kwargs)

    def swapped_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == source and opened_fd is not None:
            return os.fstat(opened_fd)
        return real_stat(path, *args, **kwargs)

    real_readlink = os.readlink

    def opened_target(path: Any, *args: Any, **kwargs: Any) -> Any:
        if opened_fd is not None and str(path) == f"/proc/self/fd/{opened_fd}":
            return str(outside)
        return real_readlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(diff_impact_refs.sys, "platform", "linux")
        patch.setattr(Path, "open", swapped_open)
        patch.setattr(Path, "stat", swapped_stat)
        patch.setattr(os, "readlink", opened_target)
        evidence = diff_impact_refs._test_reference_evidence(
            repo_map_payload, "dormant", None, None, ("pkg/core.py",)
        )

    assert opened_fd is not None
    assert evidence == (False, False)


def test_package_qualified_module_alias_dynamic_getattr_marks_cap_incomplete(
    tmp_path: Path,
) -> None:
    module = tmp_path / "src" / "auditedpkg" / "app" / "changed.py"
    module.parent.mkdir(parents=True)
    module.write_text("def dormant():\n    return 2\n", encoding="utf-8")
    (tmp_path / "test_changed.py").write_text(
        'import auditedpkg.app.changed as changed_alias\ngetattr(changed_alias, "dor" + "mant")()\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/src/auditedpkg/app/changed.py b/src/auditedpkg/app/changed.py\n"
        "--- a/src/auditedpkg/app/changed.py\n+++ b/src/auditedpkg/app/changed.py\n"
        "@@ -1,2 +1,2 @@\n def dormant():\n-    return 1\n+    return 2\n"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_unrelated_never_gap_keeps_uncalled_cap(tmp_path: Path) -> None:
    diff = _dormant_diff(tmp_path)
    baseline = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)
    assert baseline["risk_tier"] == "medium"
    assert baseline["risk_cap_evidence"] == "complete"
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "unrelated"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["blast_radius_score"] == baseline["blast_radius_score"]
    assert payload["risk_tier"] == "medium"
    assert payload["risk_cap_evidence"] == "complete"
    assert "capped_uncalled_symbols" in payload["risk_reasons"]


def test_imported_alias_caller_disables_uncalled_cap(tmp_path: Path) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "consumer.py").write_text(
        "from pkg.core import dormant as observed\nresult = observed()\n", encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_alias_call_preserves_precap_payload_and_gate(tmp_path: Path, monkeypatch: Any) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_core.py").write_text(
        "from pkg.core import dormant as observed\nobserved()\n", encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["blast_radius_score"] == 1.0
    assert payload["risk_tier"] == "critical"
    assert payload["risk_cap_evidence"] == "complete"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "critical"]).exit_code == 2


def test_import_only_alias_disables_uncalled_cap(tmp_path: Path, monkeypatch: Any) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_core.py").write_text(
        "from pkg.core import dormant as observed\n", encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "critical"]).exit_code == 2


def test_shadowed_alias_does_not_create_public_python_ast_ref(tmp_path: Path) -> None:
    _dormant_diff(tmp_path)
    test_file = tmp_path / "test_core.py"
    test_file.write_text(
        "from pkg.core import dormant as observed\ndef test_shadow(observed):\n    observed()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("dormant", tmp_path)

    assert not any(
        row["file"] == str(test_file.resolve()) and row.get("provenance") == "python-ast"
        for row in payload["references"]
    )


@pytest.mark.parametrize("with_submodule", [False, True], ids=["deletion", "mixed_submodule"])
def test_unanalyzed_deletion_disables_cap_and_preserves_gate_exit(
    tmp_path: Path, monkeypatch: Any, with_submodule: bool
) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "test_removed.py").write_text(
        "from pkg.removed import tested\n\ndef test_removed():\n    assert tested() == 1\n",
        encoding="utf-8",
    )
    diff += (
        "diff --git a/pkg/removed.py b/pkg/removed.py\n"
        "deleted file mode 100644\n"
        "--- a/pkg/removed.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def tested():\n-    return 1\n"
    )
    if with_submodule:
        diff += (
            "diff --git a/vendor/lib b/vendor/lib\n"
            "index 1111111..2222222 160000\n"
            "--- a/vendor/lib\n+++ b/vendor/lib\n"
            "@@ -1 +1 @@\n"
            "-Subproject commit 1111111\n+Subproject commit 2222222\n"
        )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["deleted_files"] == ["pkg/removed.py"]
    assert "deleted_files_symbols_not_analyzed" in payload["downgrade_reasons"]
    if with_submodule:
        assert payload["submodule_changed_files"] == ["vendor/lib"]
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_reference_deadline_preserves_completed_analysis_and_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "core.py").write_text(
        "def alpha():\n    return 2\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        'from pkg import core\nfirst = getattr(core, "alpha")\nsecond = getattr(core, "beta")\n',
        encoding="utf-8",
    )
    diff = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n+++ b/pkg/core.py\n"
        "@@ -1,5 +1,5 @@\n"
        " def alpha():\n-    return 1\n+    return 2\n"
        " \n def beta():\n-    return 1\n+    return 2\n"
    )
    baseline = di.build_diff_blast_radius(root=tmp_path, diff_text=diff, deadline_seconds=3)
    assert [symbol["name"] for symbol in baseline["changed_symbols"]] == ["alpha", "beta"]

    real = repo_map.build_symbol_refs_from_map

    def consume_remaining_budget(*args: Any, **kwargs: Any) -> dict[str, Any]:
        deadline = kwargs["deadline_monotonic"]
        time.sleep(max(0.0, deadline - time.monotonic() + 0.01))
        return real(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_refs_from_map", consume_remaining_budget)
    bounded = di.build_diff_blast_radius(root=tmp_path, diff_text=diff, deadline_seconds=3)

    def existing(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"risk_reasons", "risk_cap_evidence"}
        }

    assert existing(bounded) == existing(baseline)
    assert bounded["risk_cap_evidence"] == "incomplete"
    assert "capped_uncalled_symbols" not in bounded["risk_reasons"]

    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: baseline)
    baseline_exit = CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "critical"]).exit_code
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: bounded)
    bounded_exit = CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "critical"]).exit_code
    assert bounded_exit == baseline_exit == 2


def test_malformed_test_reference_lookup_preserves_payload_and_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_bytes(b"from pkg import core\n# \xff\n")

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    legacy_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"risk_reasons", "risk_cap_evidence"}
    }
    assert legacy_payload == {
        "root": str(tmp_path).replace("\\", "/"),
        "ref": None,
        "staged": False,
        "changed_files": ["pkg/core.py"],
        "changed_symbols": [
            {
                "name": "dormant",
                "kind": "function",
                "file": "pkg/core.py",
                "line": 1,
                "start_line": 1,
                "end_line": 2,
            }
        ],
        "callers": [],
        "affected_files": [str(tmp_path / "pkg" / "core.py").replace("\\", "/"), "pkg/core.py"],
        "affected_tests": [str(tests_dir / "test_broken.py").replace("\\", "/")],
        "blast_radius_score": 1.0,
        "risk_tier": "critical",
        "partial": False,
        "downgrade_reasons": [],
        "symbol_count": 1,
        "caller_count": 0,
        "file_count": 2,
        "test_count": 1,
        "deleted_files": [],
        "not_analyzed_paths": [],
        "unparsed_changed_files": [],
        "binary_files": [],
        "mode_changed_files": [],
        "submodule_changed_files": [],
        "result_incomplete": False,
        "incomplete_reason": None,
    }
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload.copy())
    result = CliRunner().invoke(app, ["diff-impact", "--json"])
    assert result.exit_code == 0


def test_refs_deadline_cutoff_marks_diff_impact_partial_and_uncapped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    real = repo_map.build_symbol_refs_from_map
    calls: list[tuple[float | None, str]] = []

    def expire_during_refs(
        repo_map_payload: dict[str, Any], symbol: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((kwargs.get("deadline_monotonic"), symbol))
        payload = real(repo_map_payload, symbol, **kwargs)
        monkeypatch.setattr(di.time, "monotonic", lambda: kwargs["deadline_monotonic"] + 1)
        return payload

    monkeypatch.setattr(repo_map, "build_symbol_refs_from_map", expire_during_refs)
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff, deadline_seconds=60)
    assert calls and calls[0][0] is not None
    assert payload["partial"] is False
    assert payload["downgrade_reasons"] == []
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


def test_refs_lookup_reused_by_symbol_name_with_existing_limits(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    monkeypatch.setattr(
        di,
        "map_changed_lines_to_symbols",
        lambda *args: [
            {"name": "dormant", "file": "pkg/core.py"},
            {"name": "dormant", "file": "pkg/other.py"},
        ],
    )
    calls: list[tuple[float | None, int | None]] = []

    def record_refs(*args: Any) -> tuple[bool, bool]:
        calls.append((args[2], args[3]))
        return True, True

    monkeypatch.setattr(di, "_test_reference_evidence", record_refs)
    payload = di.build_diff_blast_radius(
        root=tmp_path, diff_text=diff, deadline_seconds=60, max_repo_files=3
    )
    assert [symbol["name"] for symbol in payload["changed_symbols"]] == ["dormant", "dormant"]
    assert len(calls) == 1
    assert calls[0][0] is not None
    assert calls[0][1] == 3


def test_refs_lookup_failure_is_dispositioned_and_disables_cap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)

    def fail_refs(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("reference parser failed")

    monkeypatch.setattr(repo_map, "build_symbol_refs_from_map", fail_refs)
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]

    ledger_path = (
        Path(__file__).resolve().parents[1] / "fixtures/governance/handler-dispositions.json"
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert any(
        record["module"] == "cli/diff_impact_refs.py"
        and record["enclosing_symbol"] == "_test_reference_evidence"
        and record["handler_index_within_symbol"] == 0
        and record["category"] == "INTENTIONAL-BOUNDARY"
        for record in ledger
    ), "refs lookup failure boundary has no disposition record"


def test_symlink_swap_during_import_read_disables_cap_and_preserves_gate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    link = tmp_path / "consumer.py"
    link.write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("from pkg.core import dormant\n", encoding="utf-8")
    baseline = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)
    assert baseline["risk_tier"] == "medium"
    assert baseline["risk_cap_evidence"] == "complete"

    real_open = Path.open
    armed = False
    swapped = False
    real_refs = repo_map.build_symbol_refs_from_map

    def arm_after_refs(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal armed
        result = real_refs(*args, **kwargs)
        armed = True
        return result

    def swap_before_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if armed and path == link and not swapped:
            link.unlink()
            link.symlink_to(outside)
            swapped = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_refs_from_map", arm_after_refs)
    monkeypatch.setattr(Path, "open", swap_before_open)
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert swapped
    assert link.is_symlink()
    assert os.path.samefile(link, outside)
    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "critical"]).exit_code == 2


def test_parser_recursion_disables_cap_and_returns_completed_payload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "consumer.py").write_text("value = 1\n", encoding="utf-8")
    baseline = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)
    assert baseline["risk_tier"] == "medium"

    real_refs = repo_map.build_symbol_refs_from_map
    real_parse = diff_impact_refs.ast.parse
    armed = False

    def arm_after_refs(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal armed
        result = real_refs(*args, **kwargs)
        armed = True
        return result

    def deep_expression_parse(source: Any, *args: Any, **kwargs: Any) -> Any:
        if armed and isinstance(source, str) and source.startswith("def dormant():"):
            raise RecursionError("deep expression parser recursion")
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_refs_from_map", arm_after_refs)
    monkeypatch.setattr(diff_impact_refs.ast, "parse", deep_expression_parse)
    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["risk_cap_evidence"] == "incomplete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    assert {
        key: value
        for key, value in payload.items()
        if key not in {"risk_tier", "risk_reasons", "risk_cap_evidence"}
    } == {
        key: value
        for key, value in baseline.items()
        if key not in {"risk_tier", "risk_reasons", "risk_cap_evidence"}
    }
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--json"]).exit_code == 0


def test_deep_expression_parser_failure_marks_reference_incomplete(
    tmp_path: Path, monkeypatch: Any
) -> None:
    deep_file = tmp_path / "consumer.py"
    deep_file.write_text("value = " + "+" * 10000 + "1\n", encoding="utf-8")
    repo_map_payload = {"path": str(tmp_path), "files": [str(deep_file)], "tests": []}
    monkeypatch.setattr(
        repo_map,
        "build_symbol_refs_from_map",
        lambda *args, **kwargs: {"references": [], "string_refs": []},
    )

    assert diff_impact_refs._test_reference_evidence(
        repo_map_payload, "dormant", None, None, ("app.py",)
    ) == (False, False)


@pytest.mark.parametrize("consumer_name", ["consumer.py", "test_consumer.py"])
def test_unrelated_module_import_does_not_disable_uncalled_cap(
    tmp_path: Path, consumer_name: str
) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / consumer_name).write_text(
        "from unrelated_library import dormant\n", encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 0
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "medium"
    assert "capped_uncalled_symbols" in payload["risk_reasons"]
