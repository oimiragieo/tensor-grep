"""Compare every existing diff-impact payload scenario with origin/main tier pins."""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

_CHANGED_TIERS = {
    "tests/unit/test_diff_impact.py::test_diff_impact_scenario_risk_pins#3": ("critical", "medium"),
    "tests/unit/test_diff_impact.py::test_real_repo_spaces_nonascii_and_deleted#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact.py::test_real_repo_unicode_separator_filename#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact.py::test_uncalled_symbol_risk_cap_requires_complete_symbol_data[parsed]#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_autocrlf_crlf_working_tree_is_analysed_with_no_git_process#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_batch_session_is_closed_after_the_run#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_controls_ordinary_file_is_analyzed_and_deleted_file_keeps_its_handling#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_crlf_only_content_is_still_analysed#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_eol_attribute_crlf_working_tree_is_still_analysed#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_identity_clean_filter_is_still_analysed#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_sha256_repository_staged_blob_is_read_and_verified#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_sha256_repository_working_tree_is_bound_with_the_right_algorithm#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_staged_fifty_file_diff_uses_one_batch_process_not_per_file_spawns#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_symlink_replaced_by_a_regular_file_is_analysed_normally#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_unstaged_control_still_uses_and_verifies_the_working_tree#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_unstaged_crlf_working_tree_with_autocrlf_is_not_a_hash_mismatch#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_unstaged_fifty_file_diff_spawns_no_content_process#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_valid_python_file_with_symbols_is_unchanged#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_valid_uppercase_python_file_with_symbols_reports_them[app.PY]#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_valid_uppercase_python_file_with_symbols_reports_them[app.Py]#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_valid_uppercase_python_file_with_symbols_reports_them[app.pY]#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_diff_impact_hardening.py::test_worktree_extraction_reads_a_snapshot_never_a_second_open_of_the_repo_file#1": (
        "critical",
        "medium",
    ),
    "tests/unit/test_symbol_graph_coverage_gaps.py::test_diff_impact_valid_file_has_no_unparsed_files#1": (
        "critical",
        "medium",
    ),
}

_CAPTURED: list[dict[str, Any]] = []
_COUNTS: dict[str, int] = defaultdict(int)


def pytest_sessionstart() -> None:
    import tensor_grep.cli.diff_impact as di

    original = di.build_diff_blast_radius

    def capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(*args, **kwargs)
        node = os.environ.get("PYTEST_CURRENT_TEST", "").split(" (")[0]
        if node.startswith("tests/"):
            _COUNTS[node] += 1
            _CAPTURED.append({
                "scenario_id": f"{node}#{_COUNTS[node]}",
                "risk_tier": payload["risk_tier"],
                "blast_radius_score": payload["blast_radius_score"],
                "risk_reasons": payload.get("risk_reasons", []),
            })
        return payload

    di.build_diff_blast_radius = capture


def pytest_sessionfinish() -> None:
    output = os.environ.get("TG_DIFF_IMPACT_PIN_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(_CAPTURED, indent=2) + "\n", encoding="utf-8")


def test_every_existing_diff_impact_scenario_matches_origin_main_pin(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    expected_rows = json.loads(
        (Path(__file__).parent / "fixtures" / "diff_impact_tier_pins.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {row["scenario_id"]: row for row in expected_rows}
    assert len(expected) == len(expected_rows)
    output = tmp_path / "captured.json"
    environment = os.environ.copy()
    environment["TG_DIFF_IMPACT_PIN_OUTPUT"] = str(output)
    environment["PYTHONPATH"] = os.pathsep.join([
        environment.get("PYTHONPATH", ""),
        str(Path(__file__).parent),
    ])
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        r"C:\Users\oimir\AppData\Local\Temp\tgprog\run_to.py",
        "240",
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "pytest",
        "tests/unit/test_diff_impact.py",
        "tests/unit/test_diff_impact_hardening.py",
        "tests/unit/test_symbol_graph_coverage_gaps.py",
        "-q",
        "-o",
        "addopts=--import-mode=importlib --strict-markers --tb=short",
        "-p",
        "no:cacheprovider",
        "-p",
        "test_diff_impact_tier_pins",
        "--basetemp",
        str(tmp_path / "nested"),
    ]
    result = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=260,
        check=False,
    )
    actual_rows = json.loads(output.read_text(encoding="utf-8"))
    actual = {row["scenario_id"]: row for row in actual_rows}
    assert len(actual) == len(actual_rows)
    assert _CHANGED_TIERS.keys() <= expected.keys()
    for scenario_id, pin in expected.items():
        if scenario_id not in actual:
            continue
        old_tier = pin["risk_tier"]
        new_tier = old_tier
        if scenario_id in _CHANGED_TIERS:
            assert _CHANGED_TIERS[scenario_id][0] == old_tier
            new_tier = _CHANGED_TIERS[scenario_id][1]
            assert "capped_uncalled_symbols" in actual[scenario_id]["risk_reasons"]
        assert (actual[scenario_id]["risk_tier"], actual[scenario_id]["blast_radius_score"]) == (
            new_tier,
            pin["blast_radius_score"],
        ), scenario_id
    assert actual.keys() == expected.keys()
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
