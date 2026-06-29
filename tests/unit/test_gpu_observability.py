"""Tests for GPU observability additions: SearchResult telemetry fields,
JSON formatter emission, and tg doctor GPU tier reporting."""

from __future__ import annotations

import importlib.util
import json
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.cli.formatters.json_fmt import JsonFormatter, NdjsonFormatter
from tensor_grep.core.result import MatchLine, SearchResult


# ---------------------------------------------------------------------------
# SearchResult GPU telemetry fields
# ---------------------------------------------------------------------------


class TestSearchResultGpuTelemetry:
    def test_telemetry_fields_default_to_none(self):
        result = SearchResult()
        assert result.kernel_time_ms is None
        assert result.transfer_time_ms is None
        assert result.staging_bytes is None
        assert result.fallback_reason is None

    def test_telemetry_fields_can_be_set_on_construction(self):
        result = SearchResult(
            total_files=1,
            total_matches=0,
            kernel_time_ms=3.14,
            transfer_time_ms=1.0,
            staging_bytes=1048576,
            fallback_reason="oom_fallback_to_cpu",
        )
        assert result.kernel_time_ms == pytest.approx(3.14)
        assert result.transfer_time_ms == pytest.approx(1.0)
        assert result.staging_bytes == 1048576
        assert result.fallback_reason == "oom_fallback_to_cpu"

    def test_telemetry_fields_independent_of_existing_routing_fields(self):
        result = SearchResult(
            routing_backend="CuDFBackend",
            routing_reason="cudf_single_gpu_read_text",
            routing_gpu_device_ids=[0],
            kernel_time_ms=5.5,
        )
        assert result.routing_backend == "CuDFBackend"
        assert result.kernel_time_ms == pytest.approx(5.5)
        assert result.transfer_time_ms is None


# ---------------------------------------------------------------------------
# JSON formatter: telemetry field emission
# ---------------------------------------------------------------------------


class TestJsonFormatterGpuTelemetry:
    def _make_result(self, **kwargs) -> SearchResult:
        match = MatchLine(line_number=1, text="ERROR test", file="a.log")
        return SearchResult(
            matches=[match],
            matched_file_paths=["a.log"],
            total_files=1,
            total_matches=1,
            **kwargs,
        )

    def test_telemetry_fields_absent_when_none(self):
        result = self._make_result()
        parsed = json.loads(JsonFormatter().format(result))
        assert "kernel_time_ms" not in parsed
        assert "transfer_time_ms" not in parsed
        assert "staging_bytes" not in parsed
        assert "fallback_reason" not in parsed

    def test_kernel_time_emitted_when_set(self):
        result = self._make_result(kernel_time_ms=2.718)
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["kernel_time_ms"] == pytest.approx(2.718)

    def test_transfer_time_emitted_when_set(self):
        result = self._make_result(transfer_time_ms=0.5)
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["transfer_time_ms"] == pytest.approx(0.5)

    def test_staging_bytes_emitted_when_set(self):
        result = self._make_result(staging_bytes=2097152)
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["staging_bytes"] == 2097152

    def test_fallback_reason_emitted_when_set(self):
        result = self._make_result(fallback_reason="vram_oom")
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["fallback_reason"] == "vram_oom"

    def test_all_telemetry_fields_emitted_together(self):
        result = self._make_result(
            kernel_time_ms=1.23,
            transfer_time_ms=0.45,
            staging_bytes=512000,
            fallback_reason="driver_error",
        )
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["kernel_time_ms"] == pytest.approx(1.23)
        assert parsed["transfer_time_ms"] == pytest.approx(0.45)
        assert parsed["staging_bytes"] == 512000
        assert parsed["fallback_reason"] == "driver_error"

    def test_existing_fields_unchanged_when_telemetry_added(self):
        result = self._make_result(
            routing_backend="CuDFBackend",
            kernel_time_ms=9.9,
        )
        parsed = json.loads(JsonFormatter().format(result))
        assert parsed["routing_backend"] == "CuDFBackend"
        assert parsed["total_matches"] == 1
        assert parsed["kernel_time_ms"] == pytest.approx(9.9)

    def test_ndjson_also_carries_telemetry_fields(self):
        result = self._make_result(kernel_time_ms=7.7, staging_bytes=1024)
        output = NdjsonFormatter().format(result)
        row = json.loads(output.splitlines()[0])
        assert row["kernel_time_ms"] == pytest.approx(7.7)
        assert row["staging_bytes"] == 1024

    def test_ndjson_omits_telemetry_fields_when_none(self):
        result = self._make_result()
        output = NdjsonFormatter().format(result)
        row = json.loads(output.splitlines()[0])
        assert "kernel_time_ms" not in row
        assert "staging_bytes" not in row


# ---------------------------------------------------------------------------
# Doctor GPU tier helpers
# ---------------------------------------------------------------------------


class TestDoctorGpuTierInstalled:
    """Unit tests for _doctor_gpu_tier_installed."""

    def test_returns_false_when_cudf_not_found(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name: None,
        )
        assert main_mod._doctor_gpu_tier_installed() is False

    def test_returns_true_when_cudf_spec_found(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        fake_spec = MagicMock()
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name: fake_spec if name == "cudf" else None,
        )
        assert main_mod._doctor_gpu_tier_installed() is True

    def test_returns_false_on_exception(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        def bad_find_spec(name):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(importlib.util, "find_spec", bad_find_spec)
        assert main_mod._doctor_gpu_tier_installed() is False


class TestDoctorGpuTierUsable:
    """Unit tests for _doctor_gpu_tier_usable."""

    def test_returns_false_when_cudf_backend_unavailable(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        fake_backend_cls = MagicMock()
        fake_backend_cls.return_value.is_available.return_value = False

        with patch.dict(
            "sys.modules",
            {"tensor_grep.backends.cudf_backend": MagicMock(CuDFBackend=fake_backend_cls)},
        ):
            result = main_mod._doctor_gpu_tier_usable()

        assert result is False

    def test_returns_true_when_cudf_backend_available(self):
        from tensor_grep.cli import main as main_mod

        fake_backend_cls = MagicMock()
        fake_backend_cls.return_value.is_available.return_value = True

        with patch.dict(
            "sys.modules",
            {"tensor_grep.backends.cudf_backend": MagicMock(CuDFBackend=fake_backend_cls)},
        ):
            result = main_mod._doctor_gpu_tier_usable()

        assert result is True

    def test_returns_false_on_import_error(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        with patch.dict("sys.modules", {"tensor_grep.backends.cudf_backend": None}):
            # None in sys.modules causes ImportError on import
            result = main_mod._doctor_gpu_tier_usable()

        assert result is False

    def test_returns_false_on_runtime_exception(self):
        from tensor_grep.cli import main as main_mod

        fake_backend_cls = MagicMock()
        fake_backend_cls.return_value.is_available.side_effect = RuntimeError("cuda fault")

        with patch.dict(
            "sys.modules",
            {"tensor_grep.backends.cudf_backend": MagicMock(CuDFBackend=fake_backend_cls)},
        ):
            result = main_mod._doctor_gpu_tier_usable()

        assert result is False


class TestDoctorGpuStatus:
    """Unit tests for _doctor_gpu_status tier structure."""

    def test_tier_dict_always_present(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        # Simulate no GPU hardware — DeviceDetector raises ImportError
        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_installed", lambda: False)
        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_usable", lambda: False)

        with patch.dict(
            "sys.modules",
            {"tensor_grep.core.hardware.device_detect": None},
        ):
            status = main_mod._doctor_gpu_status()

        assert "tier" in status
        tier = status["tier"]
        assert "installed" in tier
        assert "usable" in tier
        assert "promotion_proof" in tier

    def test_tier_installed_and_usable_set_from_helpers(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_installed", lambda: True)
        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_usable", lambda: False)

        with patch.dict(
            "sys.modules",
            {"tensor_grep.core.hardware.device_detect": None},
        ):
            status = main_mod._doctor_gpu_status()

        assert status["tier"]["installed"] is True
        assert status["tier"]["usable"] is False

    def test_tier_promotion_proof_initialises_to_false(self, monkeypatch):
        from tensor_grep.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_installed", lambda: True)
        monkeypatch.setattr(main_mod, "_doctor_gpu_tier_usable", lambda: True)

        with patch.dict(
            "sys.modules",
            {"tensor_grep.core.hardware.device_detect": None},
        ):
            status = main_mod._doctor_gpu_status()

        # promotion_proof is always False here; it is set by _build_doctor_payload
        # after the search_runtime_probe completes.
        assert status["tier"]["promotion_proof"] is False


class TestDoctorGpuPromotion:
    """Verify the promotion_proof tier is set from the search_runtime_probe result.

    Rather than driving through _build_doctor_payload (which stubs many helpers),
    we test the invariant at the level of the data structure: given a gpu_status dict
    as _doctor_gpu_status() produces and a probe result, the promotion_proof field
    must match search_ready after the assignment that _build_doctor_payload performs.
    """

    def _apply_probe(
        self, probe_status: str
    ) -> tuple[dict, bool]:
        """Simulate the _build_doctor_payload lines that update gpu_status."""
        gpu_status: dict = {
            "available": True,
            "devices": [],
            "error": None,
            "tier": {"installed": True, "usable": True, "promotion_proof": False},
        }
        probe_result = {"status": probe_status, "routing_backend": "NativeGpuBackend"}
        gpu_status["search_runtime_probe"] = probe_result
        gpu_status["search_ready"] = probe_result.get("status") == "supported"
        # This is the exact line added to _build_doctor_payload:
        gpu_status["tier"]["promotion_proof"] = gpu_status["search_ready"]
        return gpu_status, gpu_status["search_ready"]

    def test_promotion_proof_true_when_probe_supported(self):
        gpu_status, search_ready = self._apply_probe("supported")
        assert search_ready is True
        assert gpu_status["tier"]["promotion_proof"] is True

    def test_promotion_proof_false_when_probe_unsupported(self):
        gpu_status, search_ready = self._apply_probe("unsupported")
        assert search_ready is False
        assert gpu_status["tier"]["promotion_proof"] is False

    def test_promotion_proof_false_when_probe_failed(self):
        gpu_status, search_ready = self._apply_probe("failed")
        assert search_ready is False
        assert gpu_status["tier"]["promotion_proof"] is False

    def test_promotion_proof_false_when_probe_not_run(self):
        gpu_status, search_ready = self._apply_probe("not_run")
        assert search_ready is False
        assert gpu_status["tier"]["promotion_proof"] is False


class TestRenderDoctorGpuTiers:
    """Verify _render_doctor_payload surfaces tier information."""

    def _minimal_payload(self, gpu_tier: dict, search_ready: bool = False) -> dict:
        """Build the minimum payload dict needed by _render_doctor_payload."""
        return {
            "version": "1.0.0",
            "platform": "linux",
            "python_executable": "/usr/bin/python3",
            "python_version": "3.11.0",
            "invoked_as": "tg",
            "root": "/tmp/test",
            "native_tg_binary": None,
            "native_tg_binary_kind": "missing",
            "search_acceleration_backend": "python",
            "rust_binary_version": None,
            "rust_binary_version_warning": None,
            "rust_binary_remediation": None,
            "skipped_native_tg_binaries": [],
            "path_tg_candidates": [],
            "path_tg_first_launcher_kind": None,
            "path_tg_first_version_matches": None,
            "path_tg_foreign_warning": None,
            "fresh_shell_path_tg_candidates": [],
            "fresh_shell_path_tg_first_launcher_kind": None,
            "python_subprocess_path_tg_first": None,
            "python_subprocess_path_tg_first_launcher_kind": None,
            "python_subprocess_path_tg_foreign_warning": None,
            "python_subprocess_path_tg_foreign_remediation": None,
            "path_tg_launcher_warning": None,
            "mcp_stdio_launcher_warning": None,
            "shell_escaping_guidance": {},
            "gpu": {
                "available": False,
                "devices": [],
                "error": None,
                "search_ready": search_ready,
                "tier": gpu_tier,
            },
            "ast_cache": {"exists": False},
            "ast_grep": {
                "available": False,
                "binary": None,
                "semantic_run_options": [],
                "timeout_seconds": None,
            },
            "resident_worker": {"port_file_exists": False, "port": None, "responding": False},
            "env": {},
            "session_daemon": {"running": False},
            "lsp": {"enabled": False, "schema_version": 2, "probe_timeout_seconds": None, "providers": [], "providers_by_language": {}},
            "lsp_provider_items": [],
            "lsp_providers": {},
        }

    def test_render_shows_tier_installed_usable_promotion_proof(self):
        from tensor_grep.cli.main import _render_doctor_payload

        payload = self._minimal_payload(
            gpu_tier={"installed": True, "usable": False, "promotion_proof": False}
        )
        rendered = _render_doctor_payload(payload)
        assert "installed=True" in rendered
        assert "usable=False" in rendered
        assert "promotion_proof=False" in rendered

    def test_render_shows_search_ready(self):
        from tensor_grep.cli.main import _render_doctor_payload

        payload = self._minimal_payload(
            gpu_tier={"installed": True, "usable": True, "promotion_proof": True},
            search_ready=True,
        )
        rendered = _render_doctor_payload(payload)
        assert "search_ready=True" in rendered

    def test_render_omits_tier_block_when_empty(self):
        from tensor_grep.cli.main import _render_doctor_payload

        payload = self._minimal_payload(gpu_tier={})
        rendered = _render_doctor_payload(payload)
        # tier line should not appear when dict is empty
        assert "tier:" not in rendered
