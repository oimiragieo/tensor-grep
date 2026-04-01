from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import repo_map

Builder = Callable[[Path, Any | None], dict[str, Any]]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    _write(
        src_dir / "payments.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    _write(
        src_dir / "service.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
    )
    _write(
        src_dir / "report.py",
        "from src.service import build_receipt\n"
        "\n"
        "def generate_report(total):\n"
        "    return build_receipt(total)\n",
    )
    _write(
        tests_dir / "test_payments.py",
        "from src.payments import create_invoice\n"
        "\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1) == 2\n",
    )
    return project


def _without_profiling(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("_profiling", None)
    return cleaned


def _phase_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiling = payload["_profiling"]
    return {str(phase["name"]): dict(phase) for phase in profiling["phases"]}


def _build_repo_map_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    return repo_map.build_repo_map(project, _profiling_collector=collector)


def _build_context_pack_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    payload = repo_map.build_repo_map(project)
    return repo_map.build_context_pack_from_map(
        payload,
        "create invoice",
        _profiling_collector=collector,
    )


def _build_symbol_source_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    payload = repo_map.build_repo_map(project)
    return repo_map.build_symbol_source_from_map(
        payload,
        "create_invoice",
        _profiling_collector=collector,
    )


def _build_symbol_callers_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    payload = repo_map.build_repo_map(project)
    return repo_map.build_symbol_callers_from_map(
        payload,
        "create_invoice",
        _profiling_collector=collector,
    )


def _build_context_render_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    payload = repo_map.build_repo_map(project)
    return repo_map.build_context_render_from_map(
        payload,
        "create invoice",
        _profiling_collector=collector,
    )


def _build_blast_radius_render_payload(project: Path, collector: Any | None) -> dict[str, Any]:
    payload = repo_map.build_repo_map(project)
    return repo_map.build_symbol_blast_radius_render_from_map(
        payload,
        "create_invoice",
        _profiling_collector=collector,
    )


def _assert_profile_structure(payload: dict[str, Any]) -> None:
    assert set(payload["_profiling"]) == {"phases", "total_elapsed_s", "breakdown_pct"}
    assert isinstance(payload["_profiling"]["phases"], list)
    assert isinstance(payload["_profiling"]["total_elapsed_s"], float)
    assert isinstance(payload["_profiling"]["breakdown_pct"], dict)


def test_profile_collector_records_nested_phases_and_call_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    perf_counter_values = iter([1.0, 1.2, 1.5, 1.6, 1.9, 2.5])
    monkeypatch.setattr(repo_map.time, "perf_counter", lambda: next(perf_counter_values))

    collector = repo_map._ProfileCollector()
    with collector.phase("outer"):
        with collector.phase("inner"):
            pass
        with collector.phase("inner"):
            pass

    result = collector.result()
    phases = {str(phase["name"]): phase for phase in result["phases"]}

    assert phases["outer"]["calls"] == 1
    assert phases["outer"]["elapsed_s"] == pytest.approx(1.5)
    assert phases["inner"]["calls"] == 2
    assert phases["inner"]["elapsed_s"] == pytest.approx(0.6)


def test_profile_collector_disabled_phase_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_perf_counter() -> float:
        raise AssertionError("perf_counter should not be called when profiling is disabled")

    monkeypatch.setattr(repo_map.time, "perf_counter", _unexpected_perf_counter)

    collector = repo_map._ProfileCollector(enabled=False)
    with collector.phase("disabled"):
        pass

    assert collector.result() == {
        "phases": [],
        "total_elapsed_s": 0.0,
        "breakdown_pct": {},
    }


def test_build_repo_map_without_profiling_does_not_call_perf_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _build_project(tmp_path)

    def _unexpected_perf_counter() -> float:
        raise AssertionError("perf_counter should not be called without a collector")

    monkeypatch.setattr(repo_map.time, "perf_counter", _unexpected_perf_counter)

    payload = repo_map.build_repo_map(project)

    assert "_profiling" not in payload


@pytest.mark.parametrize(
    ("builder", "expected_phases"),
    [
        pytest.param(
            _build_repo_map_payload,
            {"repo_map_build", "file_walk", "file_parse"},
            id="repo-map",
        ),
        pytest.param(
            _build_context_pack_payload,
            {"context_scoring", "graph_construction", "graph_bfs", "graph_pagerank"},
            id="context-pack",
        ),
        pytest.param(
            _build_symbol_source_payload,
            {"source_extraction"},
            id="symbol-source",
        ),
        pytest.param(
            _build_symbol_callers_payload,
            {"caller_scan"},
            id="symbol-callers",
        ),
        pytest.param(
            _build_context_render_payload,
            {
                "context_scoring",
                "graph_construction",
                "graph_bfs",
                "graph_pagerank",
                "source_extraction",
                "source_rendering",
                "caller_scan",
                "edit_plan_assembly",
                "render_packing",
            },
            id="context-render",
        ),
        pytest.param(
            _build_blast_radius_render_payload,
            {
                "graph_construction",
                "graph_bfs",
                "graph_pagerank",
                "source_extraction",
                "source_rendering",
                "caller_scan",
                "edit_plan_assembly",
                "render_packing",
            },
            id="blast-radius-render",
        ),
    ],
)
def test_profiled_outputs_include_expected_phase_entries(
    tmp_path: Path,
    builder: Builder,
    expected_phases: set[str],
) -> None:
    project = _build_project(tmp_path)
    collector = repo_map._ProfileCollector()

    payload = builder(project, collector)
    phases = _phase_lookup(payload)

    _assert_profile_structure(payload)
    assert expected_phases <= set(phases)
    assert all(float(phase["elapsed_s"]) >= 0.0 for phase in phases.values())
    assert all(int(phase["calls"]) >= 1 for phase in phases.values())


def test_build_repo_map_file_parse_call_count_matches_scanned_files(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    collector = repo_map._ProfileCollector()

    payload = repo_map.build_repo_map(project, _profiling_collector=collector)
    phases = _phase_lookup(payload)

    expected_files = len(payload["files"]) + len(payload["tests"])
    assert phases["file_parse"]["calls"] == expected_files


def test_context_render_source_rendering_call_count_matches_rendered_sources(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    collector = repo_map._ProfileCollector()

    payload = repo_map.build_context_render_from_map(
        repo_map.build_repo_map(project),
        "create invoice",
        _profiling_collector=collector,
    )
    phases = _phase_lookup(payload)

    assert phases["source_rendering"]["calls"] == len(payload["sources"])


@pytest.mark.parametrize(
    "builder",
    [
        pytest.param(_build_repo_map_payload, id="repo-map"),
        pytest.param(_build_context_pack_payload, id="context-pack"),
        pytest.param(_build_symbol_source_payload, id="symbol-source"),
        pytest.param(_build_symbol_callers_payload, id="symbol-callers"),
        pytest.param(_build_context_render_payload, id="context-render"),
        pytest.param(_build_blast_radius_render_payload, id="blast-radius-render"),
    ],
)
def test_profiled_outputs_preserve_existing_fields(
    tmp_path: Path,
    builder: Builder,
) -> None:
    project = _build_project(tmp_path)

    baseline = builder(project, None)
    profiled = builder(project, repo_map._ProfileCollector())

    assert "_profiling" not in baseline
    assert _without_profiling(profiled) == baseline


def test_profiling_breakdown_percentages_sum_close_to_one_hundred(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    collector = repo_map._ProfileCollector()

    payload = repo_map.build_context_render_from_map(
        repo_map.build_repo_map(project),
        "create invoice",
        _profiling_collector=collector,
    )
    total_breakdown = sum(float(value) for value in payload["_profiling"]["breakdown_pct"].values())

    assert 95.0 <= total_breakdown <= 105.0


def test_profiled_context_render_reports_structured_breakdown(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    collector = repo_map._ProfileCollector()

    payload = repo_map.build_context_render_from_map(
        repo_map.build_repo_map(project),
        "create invoice",
        _profiling_collector=collector,
    )
    profiling = payload["_profiling"]

    assert profiling["phases"]
    assert set(profiling["breakdown_pct"]) == {phase["name"] for phase in profiling["phases"]}
    assert profiling["total_elapsed_s"] >= 0.0
