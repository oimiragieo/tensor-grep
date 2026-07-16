"""`tg find` (Wave 2b/2c, #189) real-binary contract tests.

Dogfood-the-shipped-artifact / house trap: `CliRunner` bypasses `bootstrap.main_entry` (the real
front door), so this suite invokes the REAL entry points instead -- `python -m tensor_grep` (the
"python-m" launcher, always available, no compiled Rust extension required) and, only when a
native `tg` binary is already built, the native launcher too. The native-launcher test SKIPS
locally without the binary (this build does not run cargo/maturin, per the CPU-safe constraint);
CI's native-build jobs are the authoritative gate for it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.runtime_paths import resolve_native_tg_binary

_SUBPROCESS_TIMEOUT_S = 60


def _run_python_m(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_S,
    )


def _get_native_binary() -> str | None:
    try:
        resolve_native_tg_binary.cache_clear()
        native_binary = resolve_native_tg_binary()
    except FileNotFoundError:
        return None
    return str(native_binary) if native_binary is not None else None


def _write_tiny_repo(root: Path) -> None:
    (root / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n"
        '    """Build an invoice record for the given id."""\n'
        "    invoice = invoice_id\n"
        "    return invoice\n",
        encoding="utf-8",
    )
    (root / "unrelated.py").write_text(
        "def totally_unrelated():\n    return 42\n", encoding="utf-8"
    )


def test_find_help_contract(tmp_path: Path) -> None:
    result = _run_python_m(["find", "--help"], cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "find" in result.stdout.lower()
    assert "Usage" in result.stdout or "usage" in result.stdout
    assert "Traceback" not in result.stderr


def test_find_tiny_repo_end_to_end_json(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tiny_repo(repo)

    result = _run_python_m(["find", "invoice", str(repo), "--json"], cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["total_matches"] >= 1
    assert any(str(match["file"]).endswith("invoice.py") for match in payload["matches"])
    assert result.stdout.isascii()


def test_find_tiny_repo_end_to_end_text(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tiny_repo(repo)

    result = _run_python_m(["find", "invoice", str(repo)], cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "invoice.py" in result.stdout
    assert result.stdout.isascii()


def test_find_no_results_exits_1_via_real_entry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_tiny_repo(repo)

    result = _run_python_m(
        ["find", "zzqzxvvvqqqnonexistentgibberish", str(repo), "--json"], cwd=tmp_path
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0


def test_find_help_native_launcher_matches_python(tmp_path: Path) -> None:
    native_binary = _get_native_binary()
    if native_binary is None:
        pytest.skip(
            "Native binary not built in this environment (CPU-safe build constraint); CI's "
            "native-build jobs are the authoritative gate for this parity check"
        )

    native_result = subprocess.run(
        [native_binary, "find", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_S,
    )
    python_result = _run_python_m(["find", "--help"], cwd=tmp_path)

    assert native_result.returncode == 0, native_result.stdout + native_result.stderr
    assert python_result.returncode == 0, python_result.stdout + python_result.stderr
    assert "find" in native_result.stdout.lower()
