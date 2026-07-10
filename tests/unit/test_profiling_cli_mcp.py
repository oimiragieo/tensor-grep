from __future__ import annotations

import importlib.util
import inspect
import json
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import session_store
from tensor_grep.cli.main import app


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
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    _write(
        tests_dir / "test_service.py",
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
    )
    return project


def _without_profiling(payload: dict[str, object]) -> dict[str, object]:
    cleaned = dict(payload)
    cleaned.pop("_profiling", None)
    cleaned.pop("profile", None)
    cleaned.pop("serve_response_cache", None)
    cleaned.pop("daemon_response_cache", None)
    cleaned.pop("session_timing", None)
    return cleaned


def _load_script_module(module_name: str, relative_path: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["context-render", "--query", "create invoice"], id="context-render"),
        pytest.param(["edit-plan", "--query", "create invoice"], id="edit-plan"),
        pytest.param(
            ["blast-radius-render", "--symbol", "create_invoice", "--max-depth", "1"],
            id="blast-radius-render",
        ),
    ],
)
def test_cli_profile_flag_includes_profiling_without_changing_output(
    tmp_path: Path,
    args: list[str],
) -> None:
    project = _build_project(tmp_path)
    runner = CliRunner()

    baseline = runner.invoke(app, [*args, "--json", str(project)])
    profiled = runner.invoke(app, [*args, "--profile", "--json", str(project)])

    assert baseline.exit_code == 0
    assert profiled.exit_code == 0

    baseline_payload = json.loads(baseline.stdout)
    profiled_payload = json.loads(profiled.stdout)

    assert "_profiling" not in baseline_payload
    assert profiled_payload["_profiling"]["phases"]
    assert _without_profiling(profiled_payload) == baseline_payload


def test_session_serve_profile_requests_include_profiling_without_changing_output(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    session_id = session_store.open_session(str(project)).session_id

    def serve_once(request: dict[str, object]) -> dict[str, object]:
        stdout = StringIO()
        served = session_store.serve_session_stream(
            session_id,
            str(project),
            input_stream=StringIO(json.dumps(request) + "\n"),
            output_stream=stdout,
        )
        assert served == 1
        responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        assert len(responses) == 1
        return responses[0]

    baseline_context = serve_once({"command": "context_render", "query": "create invoice"})
    profiled_context = serve_once({
        "command": "context_render",
        "query": "create invoice",
        "profile": True,
    })
    baseline_blast = serve_once({
        "command": "blast_radius_render",
        "symbol": "create_invoice",
        "max_depth": 1,
    })
    profiled_blast = serve_once({
        "command": "blast_radius_render",
        "symbol": "create_invoice",
        "max_depth": 1,
        "profile": True,
    })

    assert "_profiling" not in baseline_context
    assert profiled_context["_profiling"]["phases"]
    assert _without_profiling(profiled_context) == _without_profiling(baseline_context)

    assert "_profiling" not in baseline_blast
    assert profiled_blast["_profiling"]["phases"]
    assert _without_profiling(profiled_blast) == _without_profiling(baseline_blast)


def test_run_editor_profiling_writes_standard_json_with_phase_breakdown_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module(
        "run_editor_profiling_rows",
        "benchmarks/run_editor_profiling.py",
    )
    output_path = tmp_path / "bench_editor_profiling.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_editor_profiling.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_editor_plane_bench_dir", lambda: tmp_path / "editor_plane")
    monkeypatch.setattr(
        module,
        "ensure_editor_plane_fixture_set",
        lambda bench_dir: {
            "small": {
                "root": tmp_path / "small",
                "file_count": 12,
                "target_symbol": "create_invoice",
            },
            "medium": {
                "root": tmp_path / "medium",
                "file_count": 48,
                "target_symbol": "create_invoice",
            },
            "large": {
                "root": tmp_path / "large",
                "file_count": 128,
                "target_symbol": "create_invoice",
            },
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_context_render_fixture",
        lambda fixture, *, repeats: {
            "fixture": fixture["name"],
            "mode": "context-render",
            "file_count": fixture["file_count"],
            "samples_s": [0.11, 0.1, 0.12],
            "median_s": 0.11,
            "profiling_total_elapsed_s": 0.09,
            "profiling_breakdown_pct": {"context_scoring": 60.0, "render_packing": 40.0},
            "profiling_phases": [
                {"name": "context_scoring", "elapsed_s": 0.054, "calls": 1},
                {"name": "render_packing", "elapsed_s": 0.036, "calls": 1},
            ],
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_blast_radius_fixture",
        lambda fixture, *, repeats, provider="native": {
            "fixture": fixture["name"],
            "mode": "blast-radius-render",
            "file_count": fixture["file_count"],
            "symbol": "create_invoice",
            "max_depth": 3,
            "semantic_provider": provider,
            "samples_s": [0.2, 0.19, 0.21],
            "median_s": 0.2,
            "profiling_total_elapsed_s": 0.16,
            "profiling_breakdown_pct": {"caller_scan": 75.0, "render_packing": 25.0},
            "profiling_phases": [
                {"name": "caller_scan", "elapsed_s": 0.12, "calls": 1},
                {"name": "render_packing", "elapsed_s": 0.04, "calls": 1},
            ],
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_editor_profiling"
    assert payload["suite"] == "run_editor_profiling"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["repeats"] == 3
    assert [(row["fixture"], row["mode"]) for row in payload["rows"]] == [
        ("small", "context-render"),
        ("small", "blast-radius-render"),
        ("medium", "context-render"),
        ("medium", "blast-radius-render"),
        ("large", "context-render"),
        ("large", "blast-radius-render"),
    ]
    assert all("profiling_total_elapsed_s" in row for row in payload["rows"])
    assert all("profiling_breakdown_pct" in row for row in payload["rows"])
    assert all("profiling_phases" in row for row in payload["rows"])


def test_mcp_context_render_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_context_render)
    assert signature.parameters["max_repo_files"].default == (
        mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    )

    captured: dict[str, object] = {}

    def fake_build_context_render(
        query: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        captured["query"] = query
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "build_context_render", fake_build_context_render)

    payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            ".",
            max_repo_files=17,
        )
    )

    assert payload["ok"] is True
    assert captured["query"] == "create invoice"
    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path before
    # being forwarded to the builder.
    assert captured["path"] == str(Path.cwd().resolve())
    assert captured["max_repo_files"] == 17


def test_mcp_session_edit_plan_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_session_edit_plan)
    assert signature.parameters["max_repo_files"].default == (
        mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    )

    captured: dict[str, object] = {}

    def fake_session_context_edit_plan(
        session_id: str,
        query: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        captured["session_id"] = session_id
        captured["query"] = query
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        session_store,
        "session_context_edit_plan",
        fake_session_context_edit_plan,
    )

    payload = json.loads(
        mcp_server.tg_session_edit_plan(
            "session-1",
            "create invoice",
            ".",
            max_repo_files=23,
        )
    )

    assert payload == {"ok": True}
    assert captured["session_id"] == "session-1"
    assert captured["query"] == "create invoice"
    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path before
    # being forwarded to the builder.
    assert captured["path"] == str(Path.cwd().resolve())
    assert captured["max_repo_files"] == 23
