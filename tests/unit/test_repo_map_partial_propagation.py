"""Moat P0-6 step 2: the deadline PARTIAL signal must survive when a symbol builder repackages a
build_symbol_defs result into its own payload. Without _copy_partial_signal, a deadline-truncated
map silently loses partial:true/deadline_limit the moment it is wrapped by callers/impact/source.
"""

from __future__ import annotations

from pathlib import Path

import tensor_grep.cli.repo_map as repo_map


def test_copy_partial_signal_copies_when_present() -> None:
    source = {
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True, "files_scanned": 3, "files_total": 10},
    }
    payload: dict = {}
    repo_map._copy_partial_signal(payload, source)
    assert payload["partial"] is True
    assert payload["deadline_limit"]["files_scanned"] == 3
    # a defensive copy, not an alias
    source["deadline_limit"]["files_scanned"] = 99
    assert payload["deadline_limit"]["files_scanned"] == 3


def test_copy_partial_signal_is_noop_when_complete() -> None:
    payload: dict = {}
    repo_map._copy_partial_signal(payload, {"files": []})  # no partial key = complete scan
    assert "partial" not in payload
    assert "deadline_limit" not in payload


def test_symbol_builder_propagates_partial_from_defs(tmp_path: Path, monkeypatch) -> None:
    # A builder that wraps build_symbol_defs_from_map must forward the partial signal.
    partial_defs = {
        "path": str(tmp_path),
        "definitions": [],
        "files": [],
        "symbols": [],
        "imports": [],
        "tests": [],
        "no_match": True,
        "message": "No exact definition found.",
        "provider_agreement": {},
        "provider_status": {},
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True, "files_scanned": 2, "files_total": 20},
    }
    monkeypatch.setattr(repo_map, "build_symbol_defs_from_map", lambda *a, **k: dict(partial_defs))

    repo = {"path": str(tmp_path), "files": [], "symbols": [], "imports": [], "tests": []}
    result = repo_map.build_symbol_source_from_map(repo, "foo")

    assert result.get("partial") is True, "builder dropped the deadline partial signal"
    assert result["deadline_limit"]["files_scanned"] == 2
