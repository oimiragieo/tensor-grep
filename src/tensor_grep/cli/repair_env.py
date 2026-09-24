"""`tg repair-env`: re-sync editable package metadata with pyproject.toml.

Lives outside ``main.py`` (file-size ratchet); registered there as a thin command. Runtime-path
helpers are looked up on the ``runtime_paths`` module at call time so tests that monkeypatch
``tensor_grep.cli.runtime_paths.<name>`` reach this code.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

import typer

from tensor_grep.cli import runtime_paths


def editable_install_points_at(repo_root: Path) -> tuple[bool, str]:
    """Return ``(is_editable_install_of_repo_root, installed_version)``.

    Fail-closed: any missing/malformed metadata answers ``False``. Only a local ``file:`` URL
    (netloc empty or ``localhost``) whose path resolves to ``repo_root`` counts as editable.
    """
    try:
        dist = importlib.metadata.Distribution.from_name("tensor-grep")
    except importlib.metadata.PackageNotFoundError:
        return False, "unknown"
    version = str(getattr(dist, "version", "unknown"))
    try:
        direct_url_text = dist.read_text("direct_url.json")
        direct_data = json.loads(direct_url_text) if direct_url_text else {}
    except (OSError, ValueError):
        return False, version
    if (
        not isinstance(direct_data, dict)
        or direct_data.get("dir_info", {}).get("editable") is not True
    ):
        return False, version
    dist_url = direct_data.get("url")
    if not isinstance(dist_url, str):
        return False, version
    parsed = urllib.parse.urlparse(dist_url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return False, version
    try:
        local_path = Path(urllib.request.url2pathname(parsed.path)).resolve()
    except (OSError, ValueError):
        return False, version
    return local_path == repo_root.resolve(), version


def _fail(json_output: bool, start: float, error: str, **extra: Any) -> NoReturn:
    if json_output:
        payload = {"status": "failed", "error": error, **extra}
        payload["duration_seconds"] = round(time.perf_counter() - start, 3)
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(error, err=True)
    raise typer.Exit(code=1)


def _not_applicable(json_output: bool, start: float, reason: str, message: str) -> NoReturn:
    """A wheel/PyPI install has nothing to repair. This is a normal state, not a failure."""
    if json_output:
        payload = {
            "status": "not_applicable",
            "reason": reason,
            "message": message,
            "duration_seconds": round(time.perf_counter() - start, 3),
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(message)
    raise typer.Exit(code=0)


def repair_env_command(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Repair editable package metadata desync between pyproject.toml and the installed distribution."""
    start = time.perf_counter()
    repo_root = runtime_paths._repo_root()
    if not (repo_root / "pyproject.toml").exists():
        # The normal case for a PyPI/wheel install: there is no source checkout to re-sync.
        _not_applicable(
            json_output,
            start,
            "no_source_checkout",
            "Nothing to repair: tg repair-env re-syncs an EDITABLE source checkout "
            "(`uv pip install -e .`), and this tensor-grep is not one (no pyproject.toml at "
            f"{repo_root}). For a PyPI install, upgrade with `tg upgrade` instead.",
        )

    is_editable, previous_version = editable_install_points_at(repo_root)
    if not is_editable:
        _fail(
            json_output,
            start,
            "Cannot repair environment: 'tensor-grep' is not installed in editable mode "
            f"pointing to {repo_root}. Non-editable or mismatched installs are rejected fail-closed.",
            previous_version=previous_version,
        )

    uv_bin = shutil.which("uv")
    if uv_bin:
        cmd = [
            uv_bin,
            "pip",
            "install",
            "-e",
            str(repo_root),
            "--no-deps",
            "--python",
            sys.executable,
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(json_output, start, f"Repair failed: {exc}", previous_version=previous_version)
    if proc.returncode != 0:
        detail = (
            proc.stderr.strip() or proc.stdout.strip() or f"Process exited with {proc.returncode}"
        )
        _fail(json_output, start, f"Repair failed: {detail}", previous_version=previous_version)

    expected_version = runtime_paths._read_project_version_fallback()
    importlib.invalidate_caches()
    try:
        installed_version: str | None = importlib.metadata.Distribution.from_name(
            "tensor-grep"
        ).version
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    if installed_version != expected_version:
        _fail(
            json_output,
            start,
            f"Verification failed: installed distribution metadata version is {installed_version!r}, "
            f"expected {expected_version!r} from pyproject.toml",
            previous_version=previous_version,
            installed_version=installed_version,
            expected_version=expected_version,
        )

    duration = round(time.perf_counter() - start, 3)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "status": "success",
                    "previous_version": previous_version,
                    "new_version": installed_version,
                    "duration_seconds": duration,
                },
                indent=2,
            )
        )
    else:
        typer.echo(
            "Successfully repaired editable environment: "
            f"{previous_version} -> {installed_version} in {duration}s"
        )
