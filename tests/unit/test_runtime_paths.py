import importlib.metadata
import json
import os
import shutil
import subprocess
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
    runtime_paths._expected_tg_version.cache_clear()
    yield
    resolve_native_tg_binary.cache_clear()
    resolve_ripgrep_binary.cache_clear()
    runtime_paths._expected_tg_version.cache_clear()


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


def test_inspect_native_tg_binary_honors_provided_version_text_without_subprocess_spawn(
    monkeypatch, tmp_path
):
    """NIT-1 (#172): the doctor path (main.py's _build_doctor_payload) already computes a
    binary's version via its own `tg --version` subprocess call (_doctor_rust_binary_version)
    before calling inspect_native_tg_binary, which -- until this fix -- unconditionally spawned
    a SECOND `tg --version` subprocess for the identical binary via its own internal
    _native_tg_version call (added by #595). When the caller already has the text, passing it as
    version_text must skip that internal spawn entirely."""
    repo_root = tmp_path / "repo"
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    native_binary = repo_root / "rust_core" / "target" / "release" / binary_name
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("current\n", encoding="utf-8")

    call_count = 0

    def _counting_native_tg_version(_candidate):
        nonlocal call_count
        call_count += 1
        return "tg 1.12.4"

    monkeypatch.setattr(runtime_paths, "_native_tg_version", _counting_native_tg_version)

    inspected = runtime_paths.inspect_native_tg_binary(
        native_binary,
        repo_root=repo_root,
        expected_version="1.12.4",
        version_text="tg 1.12.4",
    )

    assert call_count == 0, (
        "inspect_native_tg_binary must not call _native_tg_version when version_text is provided"
    )
    assert inspected["version"] == "tg 1.12.4"
    assert inspected["version_status"] == "matches"
    assert inspected["kind"] == "in-tree-release"


def test_inspect_native_tg_binary_falls_back_to_native_tg_version_when_version_text_omitted(
    monkeypatch, tmp_path
):
    """Baseline: omitting version_text (the pre-existing call shape) preserves the internal
    _native_tg_version subprocess path -- this NIT must not remove that fallback."""
    repo_root = tmp_path / "repo"
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    native_binary = repo_root / "rust_core" / "target" / "release" / binary_name
    native_binary.parent.mkdir(parents=True, exist_ok=True)
    native_binary.write_text("current\n", encoding="utf-8")

    call_count = 0

    def _counting_native_tg_version(_candidate):
        nonlocal call_count
        call_count += 1
        return "tg 1.12.4"

    monkeypatch.setattr(runtime_paths, "_native_tg_version", _counting_native_tg_version)

    inspected = runtime_paths.inspect_native_tg_binary(
        native_binary,
        repo_root=repo_root,
        expected_version="1.12.4",
    )

    assert call_count == 1
    assert inspected["version"] == "tg 1.12.4"


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


# ---------------------------------------------------------------------------
# GPU-P0-1 (#171): WSL native-binary path-domain bridging
# ---------------------------------------------------------------------------


class TestNativeBinaryTargetsWindows:
    def test_exe_suffix_is_windows_target(self):
        assert runtime_paths.native_binary_targets_windows("/mnt/c/Users/x/tg.exe") is True
        assert runtime_paths.native_binary_targets_windows(Path("/some/path/tg.exe")) is True
        assert runtime_paths.native_binary_targets_windows("/some/path/TG.EXE") is True

    def test_mnt_drive_mount_without_exe_is_not_windows_target(self):
        # Opus MF-1: a `/mnt/<drive>/` location is NOT itself a Windows signal. A Linux ELF built
        # in-place on a Windows-drive checkout lives here (the default resolver returns `tg`, not
        # `tg.exe`, on Linux) and is same-domain -- flagging it would break a working WSL config.
        assert runtime_paths.native_binary_targets_windows("/mnt/c/tg") is False
        assert runtime_paths.native_binary_targets_windows("/mnt/d/tools/tg") is False
        assert (
            runtime_paths.native_binary_targets_windows(
                "/mnt/c/dev/tensor-grep/rust_core/target/release/tg"
            )
            is False
        )

    def test_plain_linux_path_is_not_windows_target(self):
        assert runtime_paths.native_binary_targets_windows("/usr/local/bin/tg") is False
        assert (
            runtime_paths.native_binary_targets_windows(
                "/home/user/repo/rust_core/target/release/tg"
            )
            is False
        )


class TestIsWslHost:
    def test_true_when_wsl_distro_name_set(self, monkeypatch):
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        assert runtime_paths.is_wsl_host() is True

    def test_true_when_wsl_interop_set(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setenv("WSL_INTEROP", "/run/WSL/1_interop")
        assert runtime_paths.is_wsl_host() is True

    def test_true_when_run_wsl_exists_without_env_signal(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        original_exists = os.path.exists
        monkeypatch.setattr(
            runtime_paths.os.path,
            "exists",
            lambda p: True if p == "/run/WSL" else original_exists(p),
        )
        assert runtime_paths.is_wsl_host() is True

    def test_false_on_plain_linux_without_any_wsl_signal(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        original_exists = os.path.exists
        monkeypatch.setattr(
            runtime_paths.os.path,
            "exists",
            lambda p: False if p == "/run/WSL" else original_exists(p),
        )
        assert runtime_paths.is_wsl_host() is False


class TestIsCrossDomainNativeBinary:
    def test_false_when_binary_is_none(self):
        assert runtime_paths.is_cross_domain_native_binary(None) is False

    def test_false_on_non_linux_host_even_with_windows_shaped_path(self, monkeypatch):
        monkeypatch.setattr(runtime_paths.sys, "platform", "win32")
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        assert runtime_paths.is_cross_domain_native_binary("/mnt/c/tg.exe") is False

    def test_false_on_bare_linux_ci_runner_without_wsl_signal(self, monkeypatch, tmp_path):
        """Regression guard: a Linux CI fixture that happens to name a binary `tg.exe` must NOT
        be misread as a WSL cross-domain binary just because of the host platform -- only a
        genuine WSL signal (env var or /run/WSL) may trigger cross-domain handling."""
        monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        original_exists = os.path.exists
        monkeypatch.setattr(
            runtime_paths.os.path,
            "exists",
            lambda p: False if p == "/run/WSL" else original_exists(p),
        )
        native_tg = tmp_path / "tg.exe"
        assert runtime_paths.is_cross_domain_native_binary(native_tg) is False

    def test_true_on_wsl_host_with_windows_target_binary(self, monkeypatch):
        monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        binary = Path("/mnt/c/Users/x/.tensor-grep/bin/tg.exe")
        assert runtime_paths.is_cross_domain_native_binary(binary) is True

    def test_false_on_wsl_host_with_native_linux_binary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        native_tg = tmp_path / "rust_core" / "target" / "release" / "tg"
        assert runtime_paths.is_cross_domain_native_binary(native_tg) is False

    def test_false_on_wsl_host_with_linux_elf_on_windows_drive_mount(self, monkeypatch):
        """Opus MF-1 regression: the DEFAULT resolver on WSL looks for `tg` (not `tg.exe`), so a
        repo checked out + built in-place on a Windows drive yields a genuine Linux ELF at
        `/mnt/c/.../rust_core/target/release/tg`. That binary is same-domain -- it opens a `/tmp`
        sentinel fine -- so it must NOT be flagged cross-domain (which would translate its `/tmp`
        path to a UNC path the Linux ELF cannot open, breaking a config that worked pre-PR). CI
        was green without this test; the earlier `/mnt`-disjunct implementation returned True
        here."""
        monkeypatch.setattr(runtime_paths.sys, "platform", "linux")
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        linux_elf_on_mount = Path("/mnt/c/dev/tensor-grep/rust_core/target/release/tg")
        assert runtime_paths.is_cross_domain_native_binary(linux_elf_on_mount) is False


class TestTranslatePathForWindowsBinary:
    def test_returns_translated_path_when_wslpath_succeeds(self, monkeypatch, tmp_path):
        probe_file = tmp_path / "probe.log"
        probe_file.write_text("sentinel\n", encoding="utf-8")
        monkeypatch.setattr(
            runtime_paths.shutil,
            "which",
            lambda name: "/usr/bin/wslpath" if name == "wslpath" else None,
        )

        def fake_run(command, **_kwargs):
            assert command[0] == "/usr/bin/wslpath"
            assert command[1] == "-w"
            return subprocess.CompletedProcess(
                command, 0, "C:\\Users\\x\\AppData\\Local\\Temp\\probe.log\r\n", ""
            )

        monkeypatch.setattr(runtime_paths.subprocess, "run", fake_run)

        translated = runtime_paths.translate_path_for_windows_binary(probe_file)
        assert translated == "C:\\Users\\x\\AppData\\Local\\Temp\\probe.log"

    def test_returns_none_when_wslpath_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime_paths.shutil, "which", lambda _name: None)
        assert runtime_paths.translate_path_for_windows_binary(tmp_path / "probe.log") is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            runtime_paths.shutil,
            "which",
            lambda name: "/usr/bin/wslpath" if name == "wslpath" else None,
        )
        monkeypatch.setattr(
            runtime_paths.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "no such path"),
        )
        assert runtime_paths.translate_path_for_windows_binary(tmp_path / "probe.log") is None

    def test_returns_none_on_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            runtime_paths.shutil,
            "which",
            lambda name: "/usr/bin/wslpath" if name == "wslpath" else None,
        )

        def raise_timeout(command, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=2.0)

        monkeypatch.setattr(runtime_paths.subprocess, "run", raise_timeout)
        assert runtime_paths.translate_path_for_windows_binary(tmp_path / "probe.log") is None

    def test_returns_none_on_os_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            runtime_paths.shutil,
            "which",
            lambda name: "/usr/bin/wslpath" if name == "wslpath" else None,
        )

        def raise_os_error(command, **_kwargs):
            raise OSError("exec format error")

        monkeypatch.setattr(runtime_paths.subprocess, "run", raise_os_error)
        assert runtime_paths.translate_path_for_windows_binary(tmp_path / "probe.log") is None


class TestGpuProbeTimeoutS:
    def test_default_when_not_cross_domain_and_no_override(self, monkeypatch):
        monkeypatch.delenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", raising=False)
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=False) == pytest.approx(2.0)

    def test_cross_domain_raises_floor_above_small_default(self, monkeypatch):
        monkeypatch.delenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", raising=False)
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=True) == pytest.approx(
            runtime_paths.CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S
        )

    def test_cross_domain_keeps_a_larger_explicit_default(self, monkeypatch):
        monkeypatch.delenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", raising=False)
        assert runtime_paths.gpu_probe_timeout_s(
            cross_domain=True, default_s=30.0
        ) == pytest.approx(30.0)

    def test_env_override_honored_regardless_of_cross_domain(self, monkeypatch):
        monkeypatch.setenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "9.5")
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=False) == pytest.approx(9.5)
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=True) == pytest.approx(9.5)

    def test_env_override_invalid_value_falls_back_to_default_logic(self, monkeypatch):
        monkeypatch.setenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "banana")
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=False) == pytest.approx(2.0)

    def test_env_override_non_positive_value_falls_back_to_default_logic(self, monkeypatch):
        monkeypatch.setenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "0")
        assert runtime_paths.gpu_probe_timeout_s(cross_domain=False) == pytest.approx(2.0)


@pytest.mark.skipif(
    shutil.which("wslpath") is None, reason="requires a real WSL host with wslpath on PATH"
)
def test_translate_path_for_windows_binary_real_wslpath_smoke(tmp_path):
    """Integration smoke test against the REAL wslpath binary -- only runs on an actual WSL
    host; every other test above is fully monkeypatched and platform-independent."""
    probe_file = tmp_path / "probe.log"
    probe_file.write_text("sentinel\n", encoding="utf-8")
    translated = runtime_paths.translate_path_for_windows_binary(probe_file)
    assert translated is not None
    assert translated.strip() != ""
