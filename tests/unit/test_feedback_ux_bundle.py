"""Round-5 (real-AI-use feedback) discoverability/UX bundle: blast-radius command help
disambiguation (an AI burned 3.5 min on `blast-radius-render` — a prose bundle — when the
machine-readable caller graph it wanted is one flag away at `blast-radius --json`), plus doctor
remediation hints for a cold AST cache and the GPU `search_ready=False` explainer.

These assert help/output CONTRACTS (substring, so wording can still evolve), not exact text.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import _doctor_ast_cache_status, _render_doctor_payload, app

runner = CliRunner()


def _strip(text: str) -> str:
    # Typer/rich may wrap help across lines; collapse whitespace for robust substring checks.
    return " ".join(text.split())


def _minimal_doctor_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": "1.0.0",
        "platform": "linux",
        "python_executable": "/usr/bin/python",
        "python_version": "3.12.0",
        "invoked_as": "tg",
        "root": "/repo",
        "session_daemon": {},
    }
    payload.update(overrides)
    return payload


# --- TG-1 / TG-3: blast-radius* help disambiguation ---


def test_blast_radius_render_help_routes_to_the_json_graph_command() -> None:
    result = runner.invoke(app, ["blast-radius-render", "--help"])
    assert result.exit_code == 0
    text = _strip(result.stdout)
    # The prose-render command must tell the reader the machine-readable graph is elsewhere.
    assert "tg blast-radius" in text
    assert "--json" in text


def test_blast_radius_help_advertises_the_graph_keys_it_returns() -> None:
    result = runner.invoke(app, ["blast-radius", "--help"])
    assert result.exit_code == 0
    text = _strip(result.stdout)
    # Agents should learn from --help that the caller graph is already there under --json.
    assert "caller_tree" in text
    assert "blast_radius_score" in text


def test_three_blast_radius_commands_have_distinguishing_when_to_use() -> None:
    render = _strip(runner.invoke(app, ["blast-radius-render", "--help"]).stdout)
    plan = _strip(runner.invoke(app, ["blast-radius-plan", "--help"]).stdout)
    # -render is prose-for-a-prompt; -plan is a machine plan without source text.
    assert "prose" in render.lower()
    assert "plan" in plan.lower()


# --- TG-2: doctor warms a cold AST cache ---


def test_doctor_ast_cache_status_includes_remediation_when_cold(tmp_path: Path) -> None:
    status = _doctor_ast_cache_status(str(tmp_path), str(tmp_path / "sgconfig.yml"))
    assert status["exists"] is False
    assert "remediation" in status
    assert "tg map" in str(status["remediation"])


def test_doctor_render_warns_to_warm_cold_ast_cache() -> None:
    out = _render_doctor_payload(_minimal_doctor_payload(ast_cache={"exists": False}))
    assert "tg map" in out
    # The negative: a warm cache must NOT emit the warm hint.
    warm = _render_doctor_payload(
        _minimal_doctor_payload(
            ast_cache={"exists": True, "size_bytes": 10, "mtime": 1.0, "stale": False}
        )
    )
    assert "warm" not in warm.lower()


# --- TG-13: doctor explains GPU available=True / search_ready=False ---


def test_doctor_render_explains_gpu_search_ready_false() -> None:
    out = _render_doctor_payload(
        _minimal_doctor_payload(gpu={"available": True, "search_ready": False})
    )
    assert "experimental" in out.lower()
    # The negative: when search actually routes on GPU, no "experimental" caveat.
    ready = _render_doctor_payload(
        _minimal_doctor_payload(gpu={"available": True, "search_ready": True})
    )
    assert "search_ready=False is expected" not in ready
