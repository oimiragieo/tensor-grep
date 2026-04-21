from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]


def _default_tg_binary() -> Path:
    return (
        ROOT_DIR
        / "rust_core"
        / "target"
        / "release"
        / ("tg.exe" if __import__("os").name == "nt" else "tg")
    )


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

    def test_dispatch_request_applies_globs_to_search_config(self, tmp_path, monkeypatch):
        from tensor_grep import sidecar as sidecar_mod
        from tensor_grep.core.result import MatchLine, SearchResult

        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        (corpus_dir / "keep.txt").write_text("ERROR keep me\n", encoding="utf-8")
        (corpus_dir / "drop.md").write_text("ERROR drop me\n", encoding="utf-8")

        captured_configs: list[Any] = []

        class FakeBackend:
            def search(self, current_file: str, current_pattern: str, config=None):
                return SearchResult(
                    matches=[
                        MatchLine(
                            line_number=1,
                            text="ERROR keep me",
                            file=current_file,
                        )
                    ],
                    total_files=1,
                    total_matches=1,
                )

        class FakePipeline:
            def __init__(self, config):
                captured_configs.append(config)
                self.selected_backend_name = "FakeGpuBackend"
                self.selected_backend_reason = "gpu-device-ids-explicit"
                self.selected_gpu_device_ids = [0]
                self._backend = FakeBackend()

            def get_backend(self):
                return self._backend

        monkeypatch.setattr(sidecar_mod, "_detect_available_gpu_device_ids", lambda: [0])
        monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", FakePipeline)

        request: dict[str, Any] = {
            "command": "gpu_search",
            "args": [],
            "payload": {
                "pattern": "ERROR",
                "path": str(corpus_dir),
                "gpu_device_ids": [0],
                "globs": ["*.txt"],
                "ignore_case": False,
                "fixed_strings": False,
                "invert_match": False,
                "count": False,
                "word_regexp": False,
                "no_ignore": False,
                "json": True,
            },
        }

        stdout, stderr, exit_code = sidecar_mod._dispatch_request(request)

        assert exit_code == 0, stderr
        payload = json.loads(stdout)
        assert payload["total_files"] == 1
        assert payload["total_matches"] == 1
        assert payload["matches"][0]["file"].endswith("keep.txt")
        assert captured_configs[0].glob == ["*.txt"]
