"""Regression tests for confirmed MED/LOW dogfood bugs in cli/main.py.

Covered fixes:
- M7  session open no longer emits the "capped" warning when the repo map is not
      truncated (possibly_truncated == False).
- M8  session show --json exposes file_count / symbol_count (parity with
      session open --json and session list --json).
- M9  session show auto-corrects reversed `<PATH> <SESSION_ID>` args with a hint,
      matching session context.
- M10 doctor surfaces an honest gpu.search_ready boolean, and downgrades an
      over-claimed lsp_proof when the provider stderr names a workspace/fetch
      error (adding a workspace_warning and un-suppressing the stderr tail).
- M15 an unbounded scan of a folder-of-projects workspace root is refused.
- L10 calibrate exits 1 (runtime/unsupported), not 2, when it cannot run.

The tests use CliRunner / direct function calls so they stay import-light and do
not require the Rust core, a GPU, or a live language server.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import (
    _doctor_apply_lsp_workspace_warnings,
    _doctor_downgrade_lsp_workspace_proof,
    _should_refuse_unbounded_workspace_root_scan,
    app,
)


def _git_init(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)


def _open_session(runner: CliRunner, project: Path) -> str:
    result = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert result.exit_code == 0, result.stdout
    return str(json.loads(result.stdout)["session_id"])


# ---------------------------------------------------------------------------
# M7 - no spurious "capped" warning on a tiny, non-truncated session repo map
# ---------------------------------------------------------------------------


def test_m7_session_open_no_capped_warning_when_not_truncated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a() -> None:\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("def b() -> None:\n    pass\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["session", "open", str(project)])
    assert result.exit_code == 0, result.stdout
    assert "capped" not in result.stdout.lower()

    # The scan_limit is still recorded, but possibly_truncated must be False here.
    json_result = runner.invoke(app, ["session", "open", str(project), "--json"])
    payload = json.loads(json_result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is False


def test_m7_session_open_warns_only_when_truncated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for index in range(6):
        (project / f"mod{index}.py").write_text(f"def f{index}() -> None:\n    pass\n", "utf-8")

    runner = CliRunner()
    # max_repo_files=1 forces possibly_truncated=True.
    result = runner.invoke(app, ["session", "open", str(project), "--max-repo-files", "1"])
    assert result.exit_code == 0, result.stdout
    assert "capped" in result.stdout.lower()
    # The remediation must point at a flag the command actually accepts.
    assert "--max-repo-files" in result.stdout
    # session refresh does not accept --max-repo-files, so the old "refresh
    # without --max-repo-files" wording must be gone.
    assert "refresh without --max-repo-files" not in result.stdout


# ---------------------------------------------------------------------------
# M8 - session show --json parity for file_count / symbol_count
# ---------------------------------------------------------------------------


def test_m8_session_show_json_exposes_counts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "payments.py").write_text("def invoice() -> int:\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    open_result = runner.invoke(app, ["session", "open", str(project), "--json"])
    opened = json.loads(open_result.stdout)
    session_id = opened["session_id"]

    show_result = runner.invoke(app, ["session", "show", session_id, str(project), "--json"])
    assert show_result.exit_code == 0, show_result.stdout
    shown = json.loads(show_result.stdout)

    assert "file_count" in shown
    assert "symbol_count" in shown
    assert shown["file_count"] == opened["file_count"]
    assert shown["symbol_count"] == opened["symbol_count"]


# ---------------------------------------------------------------------------
# M9 - session show reversed-arg auto-correction + hint
# ---------------------------------------------------------------------------


def test_m9_session_show_autocorrects_reversed_args(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a() -> None:\n    pass\n", encoding="utf-8")

    runner = CliRunner()
    session_id = _open_session(runner, project)

    # Reversed order: <PATH> <SESSION_ID>. Must auto-correct (exit 0) and emit a hint.
    reversed_result = runner.invoke(app, ["session", "show", str(project), session_id])
    assert reversed_result.exit_code == 0, reversed_result.stdout
    assert "Session not found" not in (reversed_result.stderr + reversed_result.stdout)
    assert "interpreting as" in reversed_result.stderr
    assert session_id in reversed_result.stdout


# ---------------------------------------------------------------------------
# M10 - doctor honesty (gpu.search_ready + workspace-blind lsp_proof)
# ---------------------------------------------------------------------------


def test_m10_doctor_gpu_search_ready_present(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    gpu = payload["gpu"]
    assert "search_ready" in gpu
    assert isinstance(gpu["search_ready"], bool)
    # search_ready must agree with the runtime probe, never just gpu.available.
    probe_status = gpu.get("search_runtime_probe", {}).get("status")
    assert gpu["search_ready"] == (probe_status == "supported")


def test_m10_lsp_proof_downgraded_on_workspace_error() -> None:
    over_claiming = {
        "language": "rust",
        "available": True,
        "health_status": "ready",
        "lsp_proof": True,
        "provider_recent_stderr": [
            "[ERROR rust_analyzer] FetchWorkspaceError: failed to load Cargo.toml",
        ],
        "stderr_tail": [],
        "stderr_tail_suppressed": True,
    }
    out = _doctor_downgrade_lsp_workspace_proof(over_claiming)
    assert out["lsp_proof"] is False
    assert out["workspace_warning"]
    assert out["not_lsp_proof_reason"]
    # The suppressed evidence is restored and no longer hidden.
    assert out["stderr_tail_suppressed"] is False
    assert any("FetchWorkspaceError" in line for line in out["stderr_tail"])
    # Original dict is not mutated.
    assert over_claiming["lsp_proof"] is True


def test_m10_lsp_proof_preserved_without_workspace_error() -> None:
    clean = {
        "language": "python",
        "available": True,
        "health_status": "ready",
        "lsp_proof": True,
        "provider_recent_stderr": None,
        "stderr_tail": [],
    }
    proof_false = {
        "language": "go",
        "lsp_proof": False,
        "provider_recent_stderr": ["FetchWorkspaceError: nope"],
    }
    result = _doctor_apply_lsp_workspace_warnings([clean, proof_false])
    assert result[0]["lsp_proof"] is True
    assert "workspace_warning" not in result[0]
    # A provider that was never lsp_proof stays untouched.
    assert result[1]["lsp_proof"] is False
    assert "workspace_warning" not in result[1]


# ---------------------------------------------------------------------------
# M15 - refuse an unbounded scan of a folder-of-projects workspace root
# ---------------------------------------------------------------------------


def test_m15_files_scan_refuses_folder_of_projects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("proj1", "proj2", "proj3"):
        child = workspace / name
        child.mkdir()
        _git_init(child)
        (child / "file.txt").write_text("x\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", str(workspace)])
    assert result.exit_code == 2, result.stdout
    assert "multi-project" in result.stderr
    assert "proj1" in result.stderr


def test_m15_single_project_still_scans(tmp_path: Path) -> None:
    project = tmp_path / "solo"
    project.mkdir()
    _git_init(project)
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", str(project)])
    assert result.exit_code == 0, result.stderr
    assert "multi-project" not in result.stderr


def test_m15_detector_keys_on_child_project_markers(tmp_path: Path) -> None:
    from tensor_grep.core.config import SearchConfig

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a").mkdir()
    (workspace / "a" / "pyproject.toml").write_text("[tool]\n", encoding="utf-8")
    (workspace / "b").mkdir()
    (workspace / "b" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (workspace / "c").mkdir()
    (workspace / "c" / "go.mod").write_text("module x\n", encoding="utf-8")

    refuse, dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )
    assert refuse is True
    assert dirs == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# L10 - calibrate exits 1, not 2, when it cannot run
# ---------------------------------------------------------------------------


def test_l10_calibrate_exits_one_when_unsupported() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["calibrate"])
    # On a box without the native binary / CUDA, calibrate is a runtime/unsupported
    # error: tg's convention is exit 1, not the usage-error exit 2.
    assert result.exit_code == 1, result.stdout
