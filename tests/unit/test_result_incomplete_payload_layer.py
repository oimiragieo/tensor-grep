"""Round-6 council fix (Fix B): stamp `result_incomplete` + `scan_remediation` at the PAYLOAD-
ASSEMBLY layer (repo_map.build_symbol_defs_from_map), not just in the CLI emitter
(_annotate_result_completeness in main.py). Before this fix, MCP consumers and *_json builders that
call the repo_map builders directly (bypassing the CLI) got a clean payload for a symbol that wasn't
found only because the scan was truncated -- a silent false-empty. See _mark_result_incomplete in
repo_map.py and the OR-preserving fix in main.py's _annotate_result_completeness.
"""

from __future__ import annotations

from pathlib import Path

import tensor_grep.cli.repo_map as repo_map


def _write_filler_files(root: Path, count: int) -> None:
    # Names sort alphabetically BEFORE the target file so a 1-file scan window keeps these
    # and drops the target's file.
    for index in range(count):
        (root / f"aaa_filler_{index}.py").write_text(
            f"def filler_{index}():\n    return {index}\n", encoding="utf-8"
        )


def test_truncated_no_match_sets_result_incomplete_at_payload_layer(tmp_path: Path) -> None:
    _write_filler_files(tmp_path, 3)
    (tmp_path / "zzz_target.py").write_text(
        "def needle_symbol():\n    return 1\n\n\ndef caller():\n    return needle_symbol()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("needle_symbol", str(tmp_path), max_repo_files=1)

    # Sanity: the scan really was truncated and really did miss the symbol.
    assert payload.get("no_match") is True
    scan_limit = payload.get("scan_limit")
    assert isinstance(scan_limit, dict)
    assert scan_limit.get("possibly_truncated") is True

    # The payload-layer fix: honesty signal present WITHOUT going through the CLI emitter.
    assert payload.get("result_incomplete") is True
    assert payload.get("scan_remediation")


def test_matched_result_does_not_set_result_incomplete(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def needle_symbol():\n    return 1\n\n\ndef caller():\n    return needle_symbol()\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_callers("needle_symbol", str(tmp_path))

    assert payload.get("no_match") is not True
    assert not payload.get("result_incomplete")
    assert "result_incomplete" not in payload


def test_complete_no_match_does_not_set_result_incomplete(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def something_else():\n    return 1\n", encoding="utf-8")

    payload = repo_map.build_symbol_callers("totally_absent_symbol", str(tmp_path))

    assert payload.get("no_match") is True
    # No max_repo_files cap was supplied, so the scan was complete -- no scan_limit at all.
    assert "scan_limit" not in payload or not payload["scan_limit"].get("possibly_truncated")
    assert "result_incomplete" not in payload


def test_mark_result_incomplete_helper_sets_both_keys_additively() -> None:
    payload: dict = {"no_match": True}
    repo_map._mark_result_incomplete(payload, remediation="do the thing")
    assert payload["result_incomplete"] is True
    assert payload["scan_remediation"] == "do the thing"


def test_mark_result_incomplete_helper_does_not_clobber_existing_remediation() -> None:
    payload: dict = {"scan_remediation": "already set"}
    repo_map._mark_result_incomplete(payload, remediation="different")
    assert payload["result_incomplete"] is True
    assert payload["scan_remediation"] == "already set"
