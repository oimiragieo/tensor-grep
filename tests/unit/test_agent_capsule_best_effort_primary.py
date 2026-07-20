"""v20 dogfood gap #2: `tg agent` truncating mid-scan previously returned an EMPTY primary target
(`primary_target.file == ""`, `symbol: None`) whenever `_primary_target` could not resolve one --
safe, but useless, even though the underlying `rm` (repo map) already held every file/symbol the
scan reached before the deadline cut it off.

`build_agent_capsule_from_map` (agent_capsule.py) now substitutes a BEST-EFFORT primary derived
straight from that already-scanned `rm` -- via `_best_effort_primary_target_from_map` -- whenever
the scan was truncated (`scan_truncated`) AND the real ranking pass came back with no primary at
all (`not target.get("file")`). The substitute is flagged clearly non-authoritative
(`partial_primary: True`, `primary_basis: "deadline_truncated_best_effort"`, an appended evidence
entry) and re-enters every EXISTING confidence-cap/ask-reason gate unmodified: the scan-truncation
confidence cap (`_cap_primary_target_confidence`), the forced scan-truncated ask-reason, and the T2
corroborated-resolution uplift's own unconditional `scan_truncated` disqualifier. The exit-2
contract (`main._scan_incomplete`, keyed only on `scan_limit`/`caller_scan_limit`/`partial`/
`caller_scan_truncated`) is untouched by construction -- this fix never sets or clears any of those
fields, only `primary_target`.

Covers (per the build task):
  1. Positive: a truncated scan whose query matches a SYMBOL NAME (but no file PATH) gets a
     non-empty, clearly-flagged best-effort primary, with the exit-2/ask-required/confidence-cap
     contract fully preserved.
  2. Negative: a COMPLETE (non-truncated) scan is byte-identical to before this fix -- no new keys
     on `primary_target`.
  2b. A truncated scan that still resolved a NORMAL (non-empty) primary is likewise untouched --
      the guard's second clause (`not target.get("file")`) must gate on emptiness, not on
      truncation alone.
  3. Bounded cost: the new pass never looks past `_BEST_EFFORT_PRIMARY_SCAN_CAP` items, proven
     structurally (an out-of-cap "perfect" match loses to an in-cap "partial" match) rather than
     by a flaky wall-clock assertion alone; a full-pipeline run against a 50k-symbol synthetic
     tree still completes in well under a second.

Plus focused unit coverage of the three fallback tiers inside
`_best_effort_primary_target_from_map` itself (symbol-name match -> file-path match ->
query-independent centrality -> None).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule, repo_map
from tensor_grep.cli.main import _scan_incomplete

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _fake_symbol(name: str, file: str, *, kind: str = "function", line: int = 1) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "file": file,
        "line": line,
        "start_line": line,
        "end_line": line,
    }


def _truncated_empty_primary_payload() -> dict[str, Any]:
    """Simulates `repo_map.build_context_render_from_map`'s return when the scan truncated before
    ranking ever produced a primary: no navigation_pack/edit_plan_seed primary, no rendered
    sources, `scan_limit.possibly_truncated` (+ `partial`) stamped."""
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "native",
        "files": [],
        "sources": [],
        "scan_limit": {"possibly_truncated": True, "reason": "deadline"},
        "partial": True,
        "validation_commands": [],
        "edit_plan_seed": {},
        "navigation_pack": {},
        "candidate_edit_targets": {},
        "context_consistency": {},
    }


def _write_symbol_project(tmp_path: Path, *, file_name: str, symbol_name: str) -> Path:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    source_file = project / file_name
    source_file.write_text(f"def {symbol_name}(payload):\n    return payload\n", encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# 1. Positive: full pipeline, truncated scan + symbol-name query match
# ---------------------------------------------------------------------------


def test_capsule_emits_best_effort_primary_on_truncated_scan_with_symbol_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `impl.py` deliberately shares no token with the query -- the best-effort match must come
    # from the SYMBOL name, never the file path.
    project = _write_symbol_project(
        tmp_path, file_name="impl.py", symbol_name="process_widget_report"
    )
    rm = repo_map.build_repo_map(project)
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _truncated_empty_primary_payload(),
    )

    past_deadline = time.monotonic() - 5.0
    result = agent_capsule.build_agent_capsule_from_map(
        rm, "process_widget_report", deadline_monotonic=past_deadline, max_tokens=8000
    )

    primary = result["primary_target"]
    assert primary["file"] == str((project / "impl.py").resolve())
    assert primary["symbol"] == "process_widget_report"
    assert primary["partial_primary"] is True
    assert primary["primary_basis"] == "deadline_truncated_best_effort"
    assert "deadline-truncated-best-effort" in primary["evidence"]

    # The exit-2 / honesty contract must survive the substitution untouched.
    assert result["partial"] is True
    assert _scan_incomplete(result) is True
    assert result["ask_user_before_editing"]["required"] is True
    assert result["confidence"]["overall"] < 0.75
    assert primary["confidence"] < 0.75


# ---------------------------------------------------------------------------
# 2. Negative: a complete scan is byte-identical to before this fix
# ---------------------------------------------------------------------------


def test_complete_scan_never_sets_partial_primary(tmp_path: Path) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "a.py").write_text("def solo_widget():\n    return 1\n", encoding="utf-8")

    payload = agent_capsule.build_agent_capsule("solo_widget", project, max_tokens=8000)

    assert payload.get("partial") is not True
    primary = payload["primary_target"]
    assert primary["file"] == str((project / "a.py").resolve())
    assert "partial_primary" not in primary
    assert "primary_basis" not in primary
    assert primary["evidence"] == ["parser-backed", "heuristic"]


def test_truncated_scan_with_resolved_primary_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's SECOND clause (`not target.get("file")`) must gate this fix on emptiness, not
    on `scan_truncated` alone -- a truncated scan that still resolved a normal primary must be
    left exactly as before."""
    project = _write_symbol_project(
        tmp_path, file_name="impl.py", symbol_name="process_widget_report"
    )
    rm = repo_map.build_repo_map(project)
    resolved_file = str((project / "impl.py").resolve())

    def _resolved_but_truncated_payload(*args: object, **kwargs: object) -> dict[str, Any]:
        return {
            "routing_backend": "RepoMap",
            "routing_reason": "context-render",
            "semantic_provider": "native",
            "files": [resolved_file],
            "sources": [
                {
                    "file": resolved_file,
                    "symbol": "process_widget_report",
                    "name": "process_widget_report",
                    "start_line": 1,
                    "end_line": 2,
                    "source": "def process_widget_report(payload):\n    return payload\n",
                }
            ],
            "scan_limit": {"possibly_truncated": True, "reason": "deadline"},
            "partial": True,
            "validation_commands": [],
            "edit_plan_seed": {
                "primary_file": resolved_file,
                "primary_symbol": {"name": "process_widget_report", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.9},
            },
            "navigation_pack": {
                "primary_target": {
                    "file": resolved_file,
                    "symbol": "process_widget_report",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                },
                "follow_up_reads": [],
            },
            "candidate_edit_targets": {},
            "context_consistency": {
                "primary_file_included": True,
                "rendered_context_includes_primary": True,
            },
        }

    monkeypatch.setattr(repo_map, "build_context_render_from_map", _resolved_but_truncated_payload)

    result = agent_capsule.build_agent_capsule_from_map(
        rm, "process_widget_report", max_tokens=8000
    )

    assert result["partial"] is True
    primary = result["primary_target"]
    assert primary["file"] == resolved_file
    assert "partial_primary" not in primary
    assert "primary_basis" not in primary


# ---------------------------------------------------------------------------
# 3. Bounded cost
# ---------------------------------------------------------------------------


def test_best_effort_helper_symbol_pass_never_looks_past_the_scan_cap() -> None:
    """The symbol-name-match pass only ever considers the first
    `_BEST_EFFORT_PRIMARY_SCAN_CAP` entries of `rm["symbols"]` -- an item placed further out must
    lose even when it would objectively out-score every in-cap candidate, proving the cap is a
    real hard stop and not an optimization that merely happens not to matter on small inputs."""
    cap = agent_capsule._BEST_EFFORT_PRIMARY_SCAN_CAP
    query = "integration flow handler"
    filler = [_fake_symbol(f"noise_{i}", f"/repo/noise_{i}.py") for i in range(cap - 1)]
    in_cap_partial_match = _fake_symbol("flow_handler_module", "/repo/partial.py", line=7)
    out_of_cap_perfect_match = _fake_symbol("integration_flow_handler", "/repo/perfect.py", line=99)
    rm = {
        "path": "/repo",
        "files": [s["file"] for s in filler] + ["/repo/partial.py", "/repo/perfect.py"],
        "symbols": [*filler, in_cap_partial_match, out_of_cap_perfect_match],
        "imports": [],
    }
    # Sanity precondition: the "perfect" match really would outscore the in-cap partial match if
    # both were visible to the same pass -- otherwise this test would pass for the wrong reason.
    terms = repo_map._symbol_query_terms(query)
    assert repo_map._score_symbol(out_of_cap_perfect_match, terms) > repo_map._score_symbol(
        in_cap_partial_match, terms
    )

    candidate = agent_capsule._best_effort_primary_target_from_map(rm, query)

    assert candidate is not None
    assert candidate["file"] == "/repo/partial.py"
    assert candidate["symbol"] == "flow_handler_module"


def test_best_effort_pass_bounded_wall_clock_on_large_synthetic_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-pipeline SLA check: a 50k-symbol synthetic `rm` (far larger than any realistic
    `--max-repo-files` default) must not push the capsule's wall-clock materially past the
    deadline -- the added pass is bounded by item count, not by the size of `rm`."""
    symbol_count = 50_000
    query = "integration flow handler"
    symbols = [_fake_symbol(f"noise_{i}", f"/repo/pkg/noise_{i}.py") for i in range(symbol_count)]
    symbols[250] = _fake_symbol("flow_handler_module", "/repo/pkg/partial.py", line=7)
    files = [str(s["file"]) for s in symbols]
    rm = {
        "path": "C:/repo_synthetic",
        "files": files,
        "symbols": symbols,
        "imports": [],
        "tests": [],
        "related_paths": files,
    }
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _truncated_empty_primary_payload(),
    )

    past_deadline = time.monotonic() - 1.0
    start = time.monotonic()
    result = agent_capsule.build_agent_capsule_from_map(
        rm, query, deadline_monotonic=past_deadline, max_tokens=2000
    )
    elapsed = time.monotonic() - start

    # Generous CI-safe ceiling (measured well under 0.1s locally): a coarse regression trip-wire
    # for an accidental unbounded/quadratic pass, not a tight performance pin.
    assert elapsed < 5.0
    primary = result["primary_target"]
    assert primary["file"] == "/repo/pkg/partial.py"
    assert primary["partial_primary"] is True


# ---------------------------------------------------------------------------
# Focused unit coverage of `_best_effort_primary_target_from_map`'s fallback tiers
# ---------------------------------------------------------------------------


def test_best_effort_helper_returns_none_when_rm_has_nothing_to_score() -> None:
    rm: dict[str, Any] = {"path": "/repo", "files": [], "symbols": [], "imports": []}
    assert agent_capsule._best_effort_primary_target_from_map(rm, "anything") is None


def test_best_effort_helper_prefers_symbol_name_match_over_file_path() -> None:
    rm = {
        "path": "/repo",
        "files": ["/repo/unrelated.py", "/repo/other.py"],
        "symbols": [
            _fake_symbol("unrelated_helper", "/repo/unrelated.py"),
            _fake_symbol("process_widget_report", "/repo/other.py", line=42),
        ],
        "imports": [],
    }

    candidate = agent_capsule._best_effort_primary_target_from_map(rm, "process_widget_report")

    assert candidate == {
        "file": "/repo/other.py",
        "symbol": "process_widget_report",
        "kind": "function",
        "line": 42,
    }


def test_best_effort_helper_falls_back_to_file_path_match_when_no_symbol_matches() -> None:
    rm = {
        "path": "/repo",
        "files": ["/repo/misc.py", "/repo/billing_report.py"],
        "symbols": [_fake_symbol("unrelated_helper", "/repo/misc.py")],
        "imports": [],
    }

    candidate = agent_capsule._best_effort_primary_target_from_map(rm, "billing report")

    assert candidate == {
        "file": "/repo/billing_report.py",
        "symbol": None,
        "kind": "unknown",
        "line": 1,
    }


def test_best_effort_helper_falls_back_to_centrality_when_nothing_matches() -> None:
    rm = {
        "path": "/repo",
        "files": ["/repo/hub.py", "/repo/leaf.py", "/repo/extra.py"],
        "symbols": [
            _fake_symbol("alpha", "/repo/hub.py"),
            _fake_symbol("beta", "/repo/leaf.py"),
            _fake_symbol("gamma", "/repo/extra.py"),
        ],
        "imports": [
            {"file": "/repo/leaf.py", "imports": ["hub"]},
            {"file": "/repo/extra.py", "imports": ["hub"]},
        ],
    }

    candidate = agent_capsule._best_effort_primary_target_from_map(
        rm, "zzz_completely_unrelated_term"
    )

    assert candidate == {"file": "/repo/hub.py", "symbol": None, "kind": "unknown", "line": 1}
