import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tensor_grep.cli import runtime_paths
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


def test_resolve_native_tg_binary_can_be_explicitly_disabled(tmp_path):
    binary_path = tmp_path / ("tg.exe" if sys.platform.startswith("win") else "tg")
    binary_path.touch()

    with patch.dict(
        os.environ,
        {"TG_DISABLE_NATIVE_TG": "1", "TG_NATIVE_TG_BINARY": str(binary_path)},
        clear=False,
    ):
        assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_ignores_legacy_benchmark_binary(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    legacy_name = "tg_rust.exe" if sys.platform.startswith("win") else "tg"
    legacy_binary = repo_root / "benchmarks" / legacy_name
    legacy_binary.parent.mkdir(parents=True, exist_ok=True)
    legacy_binary.write_text("legacy\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: None)
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_ignores_current_python_launcher(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    venv_dir = tmp_path / "venv" / ("Scripts" if sys.platform.startswith("win") else "bin")
    venv_dir.mkdir(parents=True, exist_ok=True)
    python_path = venv_dir / ("python.exe" if sys.platform.startswith("win") else "python")
    python_path.write_text("python\n", encoding="utf-8")
    tg_path = venv_dir / ("tg.exe" if sys.platform.startswith("win") else "tg")
    tg_path.write_text("launcher\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(python_path))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(tg_path) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_ignores_current_python_launcher_when_python_resolves_elsewhere(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    venv_dir = tmp_path / "venv" / ("Scripts" if sys.platform.startswith("win") else "bin")
    venv_dir.mkdir(parents=True, exist_ok=True)
    python_path = venv_dir / ("python.exe" if sys.platform.startswith("win") else "python")
    python_path.write_text("python\n", encoding="utf-8")
    tg_path = venv_dir / ("tg.exe" if sys.platform.startswith("win") else "tg")
    tg_path.write_text("launcher\n", encoding="utf-8")

    resolved_python_path = tmp_path / "host-python" / python_path.name
    resolved_python_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_python_path.write_text("python\n", encoding="utf-8")

    original_resolve = runtime_paths.Path.resolve

    def fake_resolve(path_obj, *args, **kwargs):
        if path_obj == python_path:
            return resolved_python_path
        return original_resolve(path_obj, *args, **kwargs)

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(python_path))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setattr(runtime_paths.Path, "resolve", fake_resolve)
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(tg_path) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows launcher layout")
def test_resolve_native_tg_binary_ignores_foreign_python_install_scripts_launcher(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    current_venv_dir = tmp_path / "current-venv" / "Scripts"
    current_venv_dir.mkdir(parents=True, exist_ok=True)
    current_python = current_venv_dir / "python.exe"
    current_python.write_text("python\n", encoding="utf-8")

    foreign_python_root = tmp_path / "Python314"
    foreign_scripts_dir = foreign_python_root / "Scripts"
    foreign_scripts_dir.mkdir(parents=True, exist_ok=True)
    (foreign_python_root / "python.exe").write_text("python\n", encoding="utf-8")
    foreign_tg = foreign_scripts_dir / "tg.exe"
    foreign_tg.write_text("launcher\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(current_python))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(foreign_tg) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


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
        text=True,
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
