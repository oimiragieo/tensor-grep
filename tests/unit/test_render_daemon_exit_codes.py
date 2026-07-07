"""TDD for Cluster B (cursor audit, 2026-07-06; unifies backlog #54).

The daemon AND cold fast-paths of `tg map` / `tg context-render` / `tg edit-plan` /
`tg blast-radius-render` echoed their JSON/text and returned exit 0 even when the payload was
SCAN-truncated (a `--deadline partial`, a `scan_limit.possibly_truncated` /
`caller_scan_limit.possibly_truncated` cap, or the caller-scan ceiling's `caller_scan_truncated`) --
violating the three-state agent exit-code contract (docs/CONTRACTS.md: exit 2 = incomplete
"regardless of whether results were found"). An agent on the warm-daemon path got a misleading
exit 0. This file pins the fix: every fast-path now runs the shared `_scan_incomplete(payload)`
gate -- output the full payload FIRST, then exit 2 if the SCAN was truncated. An OUTPUT-only cap
(`output_limit.possibly_truncated`, e.g. `map --max-files 1`) is a COMPLETE analysis capped only
for display and must stay exit 0.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import _scan_incomplete, app

runner = CliRunner()


def _flat_repo(root: Path, count: int) -> Path:
    """A project with `count` trivial .py files in one directory, so a small --max-repo-files
    genuinely drops project files (possibly_truncated=True), not just vendor/cache files."""
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index:03d}.py").write_text(
            f"def helper_{index}():\n    return {index}\n", encoding="utf-8"
        )
    return project


# --------------------------------------------------------------------------------------------
# _scan_incomplete unit battery -- the 6 shapes production emits.
# --------------------------------------------------------------------------------------------


def test_scan_incomplete_true_on_scan_limit_possibly_truncated() -> None:
    assert _scan_incomplete({"scan_limit": {"possibly_truncated": True}}) is True


def test_scan_incomplete_true_on_caller_scan_limit_possibly_truncated() -> None:
    assert _scan_incomplete({"caller_scan_limit": {"possibly_truncated": True}}) is True


def test_scan_incomplete_true_on_partial() -> None:
    assert _scan_incomplete({"partial": True}) is True


def test_scan_incomplete_true_on_caller_scan_truncated() -> None:
    assert _scan_incomplete({"caller_scan_truncated": True}) is True


def test_scan_incomplete_false_on_output_limit_only() -> None:
    # An OUTPUT cap is a COMPLETE analysis capped only for display -- must NOT trip the gate,
    # even though it sets result_incomplete=True via _annotate_result_completeness (never checked
    # here -- that is the whole point of the shared gate existing).
    assert (
        _scan_incomplete(
            {
                "result_incomplete": True,
                "output_limit": {"possibly_truncated": True, "callers_truncated": True},
            }
        )
        is False
    )


def test_scan_incomplete_false_on_clean_payload() -> None:
    assert (
        _scan_incomplete(
            {
                "scan_limit": {"possibly_truncated": False},
                "files": ["a.py"],
            }
        )
        is False
    )


# --------------------------------------------------------------------------------------------
# map -- cold path (json + text)
# --------------------------------------------------------------------------------------------


def test_map_json_scan_truncated_exits_2_with_full_payload(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", "--json", "--max-repo-files", "1", str(project)])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    # full payload still printed, not swallowed by the exit
    assert "files" in payload and "symbols" in payload


def test_map_json_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", "--json", str(project)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is False


def test_map_json_output_cap_only_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", "--json", "--max-files", "1", str(project)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["output_limit"]["possibly_truncated"] is True


def test_map_text_scan_truncated_exits_2_with_full_output(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", "--max-repo-files", "1", str(project)])
    assert result.exit_code == 2, result.output
    assert "Repository map for" in result.output
    assert "files=" in result.output


def test_map_text_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", str(project)])
    assert result.exit_code == 0, result.output


def test_map_text_output_cap_only_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["map", "--max-files", "1", str(project)])
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------------
# context-render -- cold path (json + text)
# --------------------------------------------------------------------------------------------


def test_context_render_json_scan_truncated_exits_2_with_full_payload(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app,
        [
            "context-render",
            str(project),
            "helper",
            "--json",
            "--max-repo-files",
            "1",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "rendered_context" in payload


def test_context_render_json_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["context-render", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output


def test_context_render_text_scan_truncated_exits_2_with_full_output(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app,
        ["context-render", str(project), "helper", "--max-repo-files", "1"],
    )
    assert result.exit_code == 2, result.output
    assert result.output  # rendered_context text still printed, not swallowed


def test_context_render_text_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["context-render", str(project), "helper"])
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------------
# context-render -- daemon fast-path (json + text)
# --------------------------------------------------------------------------------------------


def test_context_render_daemon_json_scan_truncated_exits_2_with_full_payload(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
            "scan_limit": {
                "max_repo_files": 5,
                "scanned_files": 5,
                "possibly_truncated": True,
                "truncation_cause": "project-files",
            },
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(
        app, ["context-render", str(project), "create invoice", "--json"]
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["rendered_context"] == "from daemon"


def test_context_render_daemon_text_scan_truncated_exits_2_with_full_output(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
            "partial": True,
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(app, ["context-render", str(project), "create invoice"])

    assert result.exit_code == 2, result.output
    assert "from daemon" in result.output


def test_context_render_daemon_complete_stays_exit_0(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(
        app, ["context-render", str(project), "create invoice", "--json"]
    )
    assert result.exit_code == 0, result.output


def test_context_render_daemon_output_cap_only_stays_exit_0(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
            "result_incomplete": True,
            "output_limit": {"possibly_truncated": True, "callers_truncated": True},
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(
        app, ["context-render", str(project), "create invoice", "--json"]
    )
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------------
# edit-plan -- cold path (json + text)
# --------------------------------------------------------------------------------------------


def test_edit_plan_json_scan_truncated_exits_2_with_full_payload(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app,
        ["edit-plan", str(project), "helper", "--json", "--max-repo-files", "1"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "files" in payload


def test_edit_plan_json_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["edit-plan", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output


def test_edit_plan_text_scan_truncated_exits_2_with_full_output(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app, ["edit-plan", str(project), "helper", "--max-repo-files", "1"]
    )
    assert result.exit_code == 2, result.output
    assert "Edit plan for" in result.output


def test_edit_plan_text_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["edit-plan", str(project), "helper"])
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------------
# edit-plan -- daemon fast-path (json + text)
# --------------------------------------------------------------------------------------------


def test_edit_plan_daemon_json_scan_truncated_exits_2_with_full_payload(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "path": str(project),
            "files": [str(project / "payments.py")],
            "tests": [],
            "symbols": [],
            "caller_scan_truncated": True,
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(app, ["edit-plan", str(project), "create invoice", "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == "create invoice"


def test_edit_plan_daemon_text_scan_truncated_exits_2_with_full_output(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "path": str(project),
            "files": [str(project / "payments.py")],
            "tests": [],
            "symbols": [],
            "partial": True,
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(app, ["edit-plan", str(project), "create invoice"])

    assert result.exit_code == 2, result.output
    assert "Edit plan for" in result.output


def test_edit_plan_daemon_complete_stays_exit_0(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "path": str(project),
            "files": [str(project / "payments.py")],
            "tests": [],
            "symbols": [],
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(app, ["edit-plan", str(project), "create invoice", "--json"])
    assert result.exit_code == 0, result.output


def test_edit_plan_daemon_output_cap_only_stays_exit_0(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "path": str(project),
            "files": [str(project / "payments.py")],
            "tests": [],
            "symbols": [],
            "result_incomplete": True,
            "output_limit": {"possibly_truncated": True},
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(app, ["edit-plan", str(project), "create invoice", "--json"])
    assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------------------------
# blast-radius-render -- cold path only (json + text); no daemon fast-path exists for this command.
# --------------------------------------------------------------------------------------------


def test_blast_radius_render_json_scan_truncated_exits_2_with_full_payload(
    tmp_path: Path,
) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app,
        [
            "blast-radius-render",
            str(project),
            "helper_0",
            "--json",
            "--max-repo-files",
            "1",
        ],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "rendered_context" in payload


def test_blast_radius_render_json_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["blast-radius-render", str(project), "helper_0", "--json"])
    assert result.exit_code == 0, result.output


def test_blast_radius_render_text_scan_truncated_exits_2_with_full_output(
    tmp_path: Path,
) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(
        app,
        ["blast-radius-render", str(project), "helper_0", "--max-repo-files", "1"],
    )
    assert result.exit_code == 2, result.output
    assert result.output  # rendered_context text still printed, not swallowed


def test_blast_radius_render_text_complete_stays_exit_0(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 8)
    result = runner.invoke(app, ["blast-radius-render", str(project), "helper_0"])
    assert result.exit_code == 0, result.output
