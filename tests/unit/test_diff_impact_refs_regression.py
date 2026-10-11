"""Regression coverage for diff-impact's test-reference risk cap."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.diff_impact as di
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

    assert payload["caller_count"] == 1
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "high"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]
    monkeypatch.setattr(di, "build_diff_blast_radius", lambda **kwargs: payload)
    assert CliRunner().invoke(app, ["diff-impact", "--fail-on-risk", "high"]).exit_code == 2


def test_imported_alias_caller_disables_uncalled_cap(tmp_path: Path) -> None:
    diff = _dormant_diff(tmp_path)
    (tmp_path / "consumer.py").write_text(
        "from pkg.core import dormant as observed\nresult = observed()\n", encoding="utf-8"
    )

    payload = di.build_diff_blast_radius(root=tmp_path, diff_text=diff)

    assert payload["caller_count"] == 1
    assert payload["risk_cap_evidence"] == "complete"
    assert payload["risk_tier"] == "critical"
    assert "capped_uncalled_symbols" not in payload["risk_reasons"]


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
