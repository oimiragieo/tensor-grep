from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers import rg_parity  # noqa: E402

from tensor_grep.cli import runtime_paths  # noqa: E402


def test_explicit_native_selection_precedes_checkout_build(monkeypatch, tmp_path):
    selected = tmp_path / "selected.exe"
    selected.write_bytes(b"selected")
    checkout = tmp_path / "checkout"
    built = (
        checkout
        / "rust_core"
        / "target"
        / "release"
        / ("tg.exe" if sys.platform == "win32" else "tg")
    )
    built.parent.mkdir(parents=True)
    built.write_bytes(b"stale")
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(selected))
    monkeypatch.setattr(rg_parity, "_candidate_repo_roots", lambda: (checkout,))
    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda p: "tg 1.125.0")
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.125.0")
    assert rg_parity.resolve_native_tg_binary() == selected.resolve()


def test_stale_explicit_native_selection_refuses_before_tests(monkeypatch, tmp_path):
    selected = tmp_path / "stale.exe"
    selected.write_bytes(b"stale")
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(selected))
    monkeypatch.setattr(rg_parity, "_candidate_repo_roots", lambda: ())
    monkeypatch.setattr(runtime_paths, "_native_tg_version", lambda p: "tg 1.124.0")
    monkeypatch.setattr(runtime_paths, "_expected_tg_version", lambda: "1.125.0")
    with pytest.raises(RuntimeError, match="stale"):
        rg_parity.resolve_native_tg_binary()


def test_missing_explicit_native_selection_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(tmp_path / "missing.exe"))
    monkeypatch.setattr(rg_parity, "_candidate_repo_roots", lambda: ())
    with pytest.raises(FileNotFoundError, match="Configured"):
        rg_parity.resolve_native_tg_binary()


@pytest.mark.parametrize(
    "version, sidecar_version, accepted",
    [
        ("1.125.0", "1.124.0", False),
        ("1.126.0rc1", "1.126.0rc1", True),
        ("1.126.0.dev1", "1.126.0.dev1", True),
    ],
)
def test_harness_checks_explicit_sidecar_version(
    monkeypatch, tmp_path, version, sidecar_version, accepted
):
    from tensor_grep.cli import dogfood_features as harness

    binary = tmp_path / "tg.exe"
    binary.write_bytes(b"native")
    monkeypatch.setattr(harness, "TG", str(binary))
    monkeypatch.setattr(harness, "_run", lambda args: (0, f"tg {version}", ""))
    monkeypatch.setattr(harness.importlib.metadata, "version", lambda name: version)
    monkeypatch.setenv("TG_SIDECAR_PYTHON", "selected-sidecar-python")
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_MCP_TG_BINARY", raising=False)

    def probe(command, **kwargs):
        assert command[0] == "selected-sidecar-python"
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({
                "package_origin": "sidecar/tensor_grep/__init__.py",
                "extension_origin": "sidecar/tensor_grep/rust_core.pyd",
                "package_version": sidecar_version,
            }),
            "",
        )

    monkeypatch.setattr(harness.subprocess, "run", probe)
    if accepted:
        assert harness._artifact_identity()["version"] == sidecar_version
    else:
        with pytest.raises(RuntimeError, match="sidecar"):
            harness._artifact_identity()
