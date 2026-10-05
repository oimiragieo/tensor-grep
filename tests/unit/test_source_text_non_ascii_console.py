"""`tg source` text mode must not crash on a legacy-console encoding when the body is non-ASCII.

The body is the user's own source, printed verbatim (as `tg search` does); only tg-authored
labels are ASCII. A strict cp1252 stdout cannot encode some characters, so the write goes through
the same `_safe_stdout_line` writer the other content-printing commands use.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

_SRC = str(Path(__file__).resolve().parents[2] / "src")
_BODY = "def greet():\n    return 'café λ 日本'\n"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text(_BODY, encoding="utf-8")
    return project


def _run(project: Path, *extra: str, encoding: str) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_SRC, env.get("PYTHONPATH", "")) if p)
    code = "import sys; from tensor_grep.cli.main import app; app()"
    return subprocess.run(
        [sys.executable, "-c", code, "source", str(project), "greet", *extra],
        env=env,
        capture_output=True,
        timeout=120,
    )


def test_text_mode_does_not_crash_on_a_strict_cp1252_stdout_and_carries_the_body(
    tmp_path: Path,
) -> None:
    result = _run(_project(tmp_path), encoding="cp1252:strict")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"UnicodeEncodeError" not in result.stderr
    out = result.stdout.decode("utf-8", errors="replace")
    assert "café" in out and "λ" in out and "日本" in out


def test_json_mode_carries_the_body_on_a_strict_cp1252_stdout(tmp_path: Path) -> None:
    result = _run(_project(tmp_path), "--json", encoding="cp1252:strict")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    payload = json.loads(result.stdout.decode("ascii"))  # JSON output stays ASCII-escaped
    assert "café λ 日本" in payload["sources"][0]["source"]


def test_tg_authored_labels_stay_ascii(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["source", str(_project(tmp_path)), "greet"])
    assert result.exit_code == 0, result.output
    header = [ln for ln in result.stdout.splitlines() if ln.startswith(("Source for", "sources="))]
    assert header and all(ln.isascii() for ln in header)
    assert "café" in result.stdout  # the user's body is printed verbatim


@pytest.mark.parametrize("encoding", ["utf-8"])
def test_utf8_console_control_prints_the_body(tmp_path: Path, encoding: str) -> None:
    # CONTROL: on a UTF-8 console the same invocation carries the body unchanged.
    result = _run(_project(tmp_path), encoding=encoding)
    assert result.returncode == 0
    assert "café λ 日本" in result.stdout.decode("utf-8")
