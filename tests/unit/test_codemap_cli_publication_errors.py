"""Generation reports expected publication failures without hiding programming errors."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import codemap as cm
from tensor_grep.cli import codemap_artifacts as artifacts
from tensor_grep.cli.codemap_publication import LOCK_NAME, publication_lock
from tensor_grep.cli.main import app


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    return repo, repo / "maps", repo / "index.md"


def _args(project, *extra, index=None):
    repo, output, default_index = project
    return [
        "codemap",
        str(repo),
        "--out",
        str(output),
        "--index",
        str(default_index if index is None else index),
        "--no-deadline",
        *extra,
    ]


def _invoke(project, *extra, **kwargs):
    return CliRunner().invoke(app, _args(project, *extra, **kwargs))


def _subprocess(project, *extra, **kwargs):
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", *_args(project, *extra, **kwargs)],
        cwd=project[0],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(cm.__file__).resolve().parents[2]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


def _snapshot(root, *, ignore_scan_cache=False):
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
        and not (ignore_scan_cache and path.relative_to(root).parts[0] == ".tg_cache")
    }


def _refused(result, message):
    if hasattr(result, "exit_code"):
        assert result.exit_code == 1
        output = result.output
    else:
        assert result.returncode == 1
        output = result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("tg codemap:")
    assert message in output
    assert "Traceback" not in output


def test_json_generation_and_source_freshness_contract(project):
    generated = _invoke(project, "--json")
    assert generated.exit_code == 0, generated.output
    payload = json.loads(generated.output)
    assert payload["partial"] is False
    assert "artifact_manifest" not in payload
    checked = _invoke(project, "--check", "--json")
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["fresh"] is True
    (project[0] / "alpha.py").write_text("def changed():\n    return 12345\n", encoding="utf-8")
    checked = _invoke(project, "--check", "--json")
    assert checked.exit_code == 1
    assert json.loads(checked.output)["fresh"] is False


def test_partial_generation_keeps_leading_warning_and_exit_two(project):
    (project[0] / "beta.py").write_text("value = 2\n", encoding="utf-8")
    result = _invoke(project, "--max-repo-files", "1")
    assert result.exit_code == 2, result.output
    assert result.output.startswith("PARTIAL:")
    assert json.loads((project[1] / cm._COVERAGE_FILENAME).read_bytes())["partial"] is True
    result = _invoke(project, "--max-repo-files", "1", "--json")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["partial"] is True
    assert result.stderr == ""


@pytest.mark.parametrize("destination", [LOCK_NAME, "_coverage.json", "directory", "output"])
def test_static_destination_errors_precede_output_creation(project, destination):
    repo, output, _ = project
    index = output / destination
    message = "reserved metadata"
    if destination in {"directory", "output"}:
        index = repo / "existing" if destination == "directory" else output
        if destination == "directory":
            index.mkdir()
        message = "file destination"
    before = _snapshot(repo)
    _refused(_invoke(project, index=index), message)
    assert not output.exists()
    assert _snapshot(repo) == before


@pytest.mark.parametrize("collision", ["casefold", "index-page"])
def test_dynamic_collision_leaves_only_locks(project, monkeypatch, collision):
    repo, output, index = project
    if collision == "casefold":
        (repo / "other").mkdir()
        (repo / "other" / "beta.py").write_text("value = 2\n", encoding="utf-8")
        monkeypatch.setattr(cm, "_folder_slug", lambda folder: "Page" if folder else "page")
    else:
        index = output / "_root.md"
    before = _snapshot(repo)
    _refused(_invoke(project, index=index), "filenames collide")
    assert {path.name for path in output.iterdir()} == {LOCK_NAME}
    assert not index.exists()
    # Dynamic validation follows scanning, which may populate the normal symbol cache.
    assert {
        p: state
        for p, state in _snapshot(repo).items()
        if p.name != LOCK_NAME and p.parts[0] != ".tg_cache"
    } == before


@pytest.mark.parametrize(
    "limit", ["MAX_PAGES", "MAX_ARTIFACT_BYTES", "MAX_TOTAL_BYTES", "MAX_STAMP_BYTES"]
)
def test_size_limits_report_errors_and_preserve_applicable_artifacts(project, monkeypatch, limit):
    assert _invoke(project).exit_code == 0
    before = _snapshot(project[0], ignore_scan_cache=True)
    stamp = project[1] / cm._COVERAGE_FILENAME
    with monkeypatch.context() as patch:
        patch.setattr(artifacts, limit, 0 if limit == "MAX_PAGES" else 1)
        _refused(_invoke(project), "verification")
    after = _snapshot(project[0], ignore_scan_cache=True)
    if limit == "MAX_PAGES":
        assert after == before
    else:
        assert stamp.read_bytes() == b'{"partial": true}\n'
        if limit != "MAX_STAMP_BYTES":
            stamp_key = stamp.relative_to(project[0])
            assert {p: state for p, state in after.items() if p != stamp_key} == {
                p: state for p, state in before.items() if p != stamp_key
            }
    assert _invoke(project).exit_code == 0
    assert json.loads(stamp.read_bytes())["partial"] is False


@pytest.mark.parametrize("error_type", [ValueError, TypeError])
def test_unexpected_programming_errors_propagate(project, monkeypatch, error_type):
    def fail(*args, **kwargs):
        raise error_type("unexpected implementation failure")

    monkeypatch.setattr(cm, "build_codemap", fail)
    before = _snapshot(project[0])
    with pytest.raises(error_type, match="unexpected implementation failure"):
        CliRunner().invoke(app, _args(project), catch_exceptions=False)
    assert _snapshot(project[0]) == before


def test_actual_entrypoint_reserved_and_contended_publication(project):
    generated = _subprocess(project, "--json")
    assert generated.returncode == 0, generated.stderr
    assert json.loads(generated.stdout)["partial"] is False
    before = _snapshot(project[0])
    _refused(_subprocess(project, index=project[1] / LOCK_NAME), "reserved metadata")
    assert _snapshot(project[0]) == before
    with publication_lock(project[1], project[2], create=False):
        _refused(_subprocess(project), "publication lock unavailable")
    assert _snapshot(project[0]) == before
    rebuilt = _subprocess(project, "--json")
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert json.loads(rebuilt.stdout)["partial"] is False
