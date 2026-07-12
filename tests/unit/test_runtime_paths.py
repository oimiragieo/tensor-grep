import importlib.metadata
import json
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
    yield
    resolve_native_tg_binary.cache_clear()
    resolve_ripgrep_binary.cache_clear()


# Task #94 PR-1: env_flag_disabled is the mirror of env_flag_enabled for a default-ON, opt-out
# flag (TG_SESSION_DAEMON_AUTOSTART) -- "explicitly turned off", not "is it on".
def test_env_flag_disabled_recognizes_falsy_tokens(monkeypatch):
    for token in ("0", "false", "no", "off", "FALSE", "No", "OFF", "  off  "):
        monkeypatch.setenv("TG_TEST_FLAG_DISABLED_PROBE", token)
        assert runtime_paths.env_flag_disabled("TG_TEST_FLAG_DISABLED_PROBE") is True, token


def test_env_flag_disabled_false_when_unset_or_other_value(monkeypatch):
    monkeypatch.delenv("TG_TEST_FLAG_DISABLED_PROBE", raising=False)
    assert runtime_paths.env_flag_disabled("TG_TEST_FLAG_DISABLED_PROBE") is False
    monkeypatch.setenv("TG_TEST_FLAG_DISABLED_PROBE", "1")
    assert runtime_paths.env_flag_disabled("TG_TEST_FLAG_DISABLED_PROBE") is False
    monkeypatch.setenv("TG_TEST_FLAG_DISABLED_PROBE", "true")
    assert runtime_paths.env_flag_disabled("TG_TEST_FLAG_DISABLED_PROBE") is False
    monkeypatch.setenv("TG_TEST_FLAG_DISABLED_PROBE", "banana")
    assert runtime_paths.env_flag_disabled("TG_TEST_FLAG_DISABLED_PROBE") is False


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
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: None)
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_ignores_stale_in_tree_binary_without_explicit_override(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    stale_binary = repo_root / "rust_core" / "target" / "debug" / binary_name
    stale_binary.parent.mkdir(parents=True, exist_ok=True)
    stale_binary.write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: None)
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.8.21", raising=False)
    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda _: "tg 1.8.14", raising=False)
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_uses_matching_in_tree_binary(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    native_binary = repo_root / "rust_core" / "target" / "release" / binary_name
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("current\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(runtime_paths.shutil, "which", lambda _: None)
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.8.21", raising=False)
    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda _: "tg 1.8.21", raising=False)
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() == native_binary.resolve()


def test_inspect_native_tg_binary_reports_stale_in_tree_binary(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    native_binary = repo_root / "rust_core" / "target" / "release" / binary_name
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda _: "tg 1.12.0")

    inspected = runtime_paths.inspect_native_tg_binary(
        native_binary,
        repo_root=repo_root,
        expected_version="1.12.4",
    )

    assert inspected == {
        "path": str(native_binary.resolve()),
        "kind": "in-tree-release",
        "version": "tg 1.12.0",
        "expected_version": "1.12.4",
        "version_status": "stale",
    }


def test_inspect_native_tg_binary_reports_matching_in_tree_binary(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    native_binary = repo_root / "rust_core" / "target" / "debug" / binary_name
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("current\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda _: "tensor-grep 1.12.4")

    inspected = runtime_paths.inspect_native_tg_binary(
        native_binary,
        repo_root=repo_root,
        expected_version="1.12.4",
    )

    assert inspected["kind"] == "in-tree-debug"
    assert inspected["version_status"] == "matches"
    assert inspected["version"] == "tensor-grep 1.12.4"


def test_expected_tg_version_prefers_repo_source_when_editable_metadata_is_stale(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda package_name: "1.12.39" if package_name == "tensor-grep" else "0.0.0",
    )
    monkeypatch.setattr(runtime_paths, "_read_project_version_fallback", lambda: "1.12.40")

    assert runtime_paths._expected_tg_version() == "1.12.40"


def test_inspect_native_tg_binary_reads_managed_frontdoor_metadata(monkeypatch, tmp_path):
    native_binary = (
        tmp_path
        / ".tensor-grep"
        / "bin"
        / ("tg.exe" if sys.platform.startswith("win") else "tg-native")
    )
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("current\n", encoding="utf-8")
    metadata_path = native_binary.with_name("tg-native-metadata.json")
    metadata_path.write_text(
        json.dumps({
            "artifact": "tensor_grep_native_frontdoor_metadata",
            "asset_flavor": "nvidia",
            "requested_asset_flavor": "nvidia",
            "asset_name": "tg-windows-amd64-nvidia.exe",
            "version": "1.12.34",
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda _: "tg 1.12.34")

    inspected = runtime_paths.inspect_native_tg_binary(
        native_binary,
        expected_version="1.12.34",
    )

    assert inspected["kind"] == "managed-native"
    assert inspected["version_status"] == "matches"
    assert inspected["native_frontdoor_flavor"] == "nvidia"
    assert inspected["native_frontdoor_requested_flavor"] == "nvidia"
    assert inspected["native_frontdoor_asset_name"] == "tg-windows-amd64-nvidia.exe"
    assert inspected["native_frontdoor_metadata_status"] == "present"


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
    monkeypatch.setenv("PATH", str(venv_dir))
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
    monkeypatch.setenv("PATH", str(venv_dir))
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(tg_path) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_native_tg_binary_skips_python_launcher_and_uses_later_matching_path_candidate(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    python_name = "python.exe" if sys.platform.startswith("win") else "python"
    current_venv_dir = (
        tmp_path / "repo" / ".venv" / ("Scripts" if sys.platform.startswith("win") else "bin")
    )
    current_venv_dir.mkdir(parents=True, exist_ok=True)
    current_python = current_venv_dir / python_name
    current_python.write_text("python\n", encoding="utf-8")
    python_launcher = current_venv_dir / binary_name
    python_launcher.write_text("python console entrypoint\n", encoding="utf-8")

    managed_dir = tmp_path / ".tensor-grep" / "bin"
    managed_dir.mkdir(parents=True, exist_ok=True)
    managed_native = managed_dir / binary_name
    managed_native.write_text("native tg\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(current_python))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(current_venv_dir), str(managed_dir)]))
    monkeypatch.setattr(runtime_paths, "_in_tree_native_tg_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.12.24")
    monkeypatch.setattr(
        runtime_paths,
        "_native_candidate_matches_current_package",
        lambda candidate, *, expected_version: (
            Path(candidate).resolve() == managed_native.resolve()
        ),
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() == managed_native.resolve()


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
    monkeypatch.setenv("PATH", str(foreign_scripts_dir))
    monkeypatch.setattr(
        runtime_paths.shutil,
        "which",
        lambda name: str(foreign_tg) if name in {"tg", "tg.exe"} else None,
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows launcher layout")
def test_resolve_native_tg_binary_ignores_venv_scripts_launcher_when_python_is_adjacent(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    host_python_dir = tmp_path / "host-python"
    host_python_dir.mkdir(parents=True, exist_ok=True)
    host_python = host_python_dir / "python.exe"
    host_python.write_text("python\n", encoding="utf-8")

    cached_venv_root = tmp_path / "uv-cache" / "archive-v0" / "tool-env"
    scripts_dir = cached_venv_root / "Scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (cached_venv_root / "pyvenv.cfg").write_text("home = C:/Python312\n", encoding="utf-8")
    (scripts_dir / "python.exe").write_text("python\n", encoding="utf-8")
    cached_launcher = scripts_dir / "tg.exe"
    cached_launcher.write_text("python console entrypoint\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.setattr(runtime_paths.sys, "executable", str(host_python))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)
    monkeypatch.setenv("PATH", str(scripts_dir))
    monkeypatch.setattr(runtime_paths, "_in_tree_native_tg_candidates", lambda **_kwargs: [])
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.13.2")
    monkeypatch.setattr(
        runtime_paths,
        "_native_candidate_matches_current_package",
        lambda candidate, *, expected_version: (
            Path(candidate).resolve() == cached_launcher.resolve()
        ),
    )
    resolve_native_tg_binary.cache_clear()

    assert resolve_native_tg_binary() is None


def test_resolve_ripgrep_binary_env_override(tmp_path):
    rg_path = tmp_path / ("rg.exe" if sys.platform.startswith("win") else "rg")
    rg_path.touch()

    with patch.dict(os.environ, {"TG_RG_PATH": str(rg_path)}):
        resolved = resolve_ripgrep_binary()
        assert resolved == rg_path.resolve()


def test_resolve_ripgrep_binary_prefers_path_rg_before_bundled(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    runtime_file = repo_root / "src" / "tensor_grep" / "cli" / "runtime_paths.py"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("# stub\n", encoding="utf-8")

    binary_name = "rg.exe" if sys.platform.startswith("win") else "rg"
    bundled = (
        repo_root
        / "benchmarks"
        / (
            "ripgrep-14.1.0-x86_64-pc-windows-msvc"
            if sys.platform.startswith("win")
            else "ripgrep-14.1.0-x86_64-apple-darwin"
            if sys.platform.startswith("darwin")
            else "ripgrep-14.1.0-x86_64-unknown-linux-musl"
        )
        / binary_name
    )
    bundled.parent.mkdir(parents=True)
    bundled.write_text("bundled\n", encoding="utf-8")
    path_rg = tmp_path / "path" / binary_name
    path_rg.parent.mkdir()
    path_rg.write_text("path\n", encoding="utf-8")

    monkeypatch.setattr(runtime_paths, "__file__", str(runtime_file))
    monkeypatch.delenv("TG_RG_PATH", raising=False)
    monkeypatch.setattr(
        runtime_paths.shutil, "which", lambda name: str(path_rg) if name == binary_name else None
    )
    resolve_ripgrep_binary.cache_clear()

    assert resolve_ripgrep_binary() == path_rg.resolve()


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
