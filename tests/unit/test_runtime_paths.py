import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tensor_grep.cli.runtime_paths import resolve_native_tg_binary, resolve_ripgrep_binary


@pytest.fixture(autouse=True)
def clear_caches():
    resolve_native_tg_binary.cache_clear()
    resolve_ripgrep_binary.cache_clear()


def test_resolve_native_tg_binary_env_override(tmp_path):
    binary_path = tmp_path / ("tg.exe" if sys.platform.startswith("win") else "tg")
    binary_path.touch()

    with patch.dict(os.environ, {"TG_NATIVE_TG_BINARY": str(binary_path)}):
        resolved = resolve_native_tg_binary()
        assert resolved == binary_path.resolve()


def test_resolve_native_tg_binary_mcp_env_override(tmp_path):
    binary_path = tmp_path / ("tg.exe" if sys.platform.startswith("win") else "tg")
    binary_path.touch()

    with patch.dict(os.environ, {"TG_MCP_TG_BINARY": str(binary_path)}):
        resolved = resolve_native_tg_binary()
        assert resolved == binary_path.resolve()


def test_resolve_native_tg_binary_missing_override(tmp_path):
    missing_path = tmp_path / "missing_binary"

    with patch.dict(os.environ, {"TG_NATIVE_TG_BINARY": str(missing_path)}):
        with pytest.raises(FileNotFoundError, match="Configured binary"):
            resolve_native_tg_binary()


def test_resolve_ripgrep_binary_env_override(tmp_path):
    rg_path = tmp_path / ("rg.exe" if sys.platform.startswith("win") else "rg")
    rg_path.touch()

    with patch.dict(os.environ, {"TG_RG_PATH": str(rg_path)}):
        resolved = resolve_ripgrep_binary()
        assert resolved == rg_path.resolve()


def test_resolve_ripgrep_binary_cwd_independence(tmp_path, monkeypatch):
    """Ensure that native passthrough and binary discovery do not depend on current working directory."""
    with patch.dict(os.environ, clear=True):
        # Override the repo_root inside the function to be a controlled path
        # Actually, let's just make sure that calling it from a different cwd doesn't change the outcome
        original_resolved = resolve_ripgrep_binary()

        monkeypatch.chdir(tmp_path)
        resolve_ripgrep_binary.cache_clear()

        new_resolved = resolve_ripgrep_binary()
        assert original_resolved == new_resolved


def test_bootstrap_resolution_parity(tmp_path):
    """Verify that bootstrap.py and main.py use the exact same binary resolution logic."""
    import subprocess

    missing_path = tmp_path / "missing_binary"
    bootstrap_script = Path("src/tensor_grep/cli/bootstrap.py").resolve()

    # We want to trigger a search to make it try resolving
    env = os.environ.copy()
    env["TG_NATIVE_TG_BINARY"] = str(missing_path)
    env["TG_RUST_FIRST_SEARCH"] = "1"  # Forces native fallback in bootstrap

    result = subprocess.run(
        [sys.executable, str(bootstrap_script), "search", "foo"],
        env=env,
        capture_output=True,
        text=True
    )

    # Because it uses the shared helper, it should fail with a FileNotFoundError,
    # NOT silently fall back and successfully run a search.
    assert result.returncode != 0
    assert "FileNotFoundError" in result.stderr
    assert "Configured binary" in result.stderr


def test_mcp_sidecar_env_propagation():
    """Verify that the MCP sidecar correctly propagates TG_SIDECAR_PYTHON to subprocesses."""
    from tensor_grep.cli.mcp_server import _run_rewrite_subprocess

    with patch("subprocess.run") as mock_run:
        _run_rewrite_subprocess(["dummy", "cmd"])
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert "env" in kwargs
        assert "TG_SIDECAR_PYTHON" in kwargs["env"]
        assert kwargs["env"]["TG_SIDECAR_PYTHON"] == sys.executable
