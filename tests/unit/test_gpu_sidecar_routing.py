from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]


def _default_tg_binary() -> Path:
    return ROOT_DIR / "rust_core" / "target" / "release" / ("tg.exe" if __import__("os").name == "nt" else "tg")


class TestGpuSidecarRouting:
    """Contract tests for explicit --gpu-device-ids routing metadata."""

    def test_tg_search_accepts_gpu_device_ids_flag(self):
        binary = _default_tg_binary()
        if not binary.exists():
            pytest.skip(f"tg binary not found: {binary}")

        result = subprocess.run(
            [str(binary), "search", "--gpu-device-ids", "0", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "gpu-device-ids" in result.stdout

    def test_tg_positional_accepts_gpu_device_ids_flag(self):
        binary = _default_tg_binary()
        if not binary.exists():
            pytest.skip(f"tg binary not found: {binary}")

        result = subprocess.run(
            [str(binary), "--gpu-device-ids", "0", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0

    def test_tg_gpu_device_ids_emits_explicit_routing_metadata_with_verbose(self):
        binary = _default_tg_binary()
        if not binary.exists():
            pytest.skip(f"tg binary not found: {binary}")

        result = subprocess.run(
            [str(binary), "--gpu-device-ids", "0", "--verbose", "ERROR", "."],
            capture_output=True,
            text=True,
            check=False,
        )
        if "routing_backend=NativeGpuBackend" in result.stderr:
            assert "gpu-device-ids-explicit-native" in result.stderr
            assert "sidecar_used=false" in result.stderr
        else:
            assert "GpuSidecar" in result.stderr
            assert "gpu-device-ids-explicit" in result.stderr
            assert "sidecar_used=true" in result.stderr


class TestSidecarGpuSearchDispatch:
    """Contract tests for the Python sidecar gpu_search command handler."""

    def test_dispatch_request_routes_gpu_search(self):
        from tensor_grep.sidecar import _dispatch_request

        request: dict[str, Any] = {
            "command": "gpu_search",
            "args": [],
            "payload": {
                "pattern": "ERROR",
                "path": ".",
                "gpu_device_ids": [0],
                "ignore_case": False,
                "fixed_strings": False,
                "invert_match": False,
                "count": False,
                "word_regexp": False,
                "no_ignore": False,
            },
        }
        _stdout, _stderr, exit_code = _dispatch_request(request)
        # On a machine without GPU, Pipeline will raise ConfigurationError.
        # The sidecar should propagate that as a non-zero exit code.
        # We just verify it didn't crash with an unhandled exception type.
        assert isinstance(exit_code, int)

    def test_dispatch_request_rejects_gpu_search_without_payload(self):
        from tensor_grep.sidecar import _dispatch_request

        request: dict[str, Any] = {
            "command": "gpu_search",
            "args": [],
            "payload": None,
        }
        _stdout, stderr, exit_code = _dispatch_request(request)
        assert exit_code == 1
        assert "requires a JSON payload" in stderr

    def test_dispatch_request_rejects_gpu_search_without_device_ids(self):
        from tensor_grep.sidecar import _dispatch_request

        request: dict[str, Any] = {
            "command": "gpu_search",
            "args": [],
            "payload": {
                "pattern": "ERROR",
                "path": ".",
                "gpu_device_ids": [],
            },
        }
        _stdout, stderr, exit_code = _dispatch_request(request)
        assert exit_code == 1
        assert "non-empty gpu_device_ids" in stderr
