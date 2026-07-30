"""Backlog #22: an EXPLICIT `--gpu-device-ids` request that cannot be honoured must exit 2.

RULING (already decided by an adversarial audit, not relitigated here): exit 2 ONLY when GPU
was explicitly requested via `--gpu-device-ids` AND could not be honoured. Exit 0 when CPU
merely served the query with no GPU request -- a CPU fallback returning correct, complete
results IS complete.

The JSON-envelope payload side of this contract already exists and is already tested
(`_gpu_proof_payload` in `src/tensor_grep/cli/formatters/json_fmt.py`,
`tests/unit/test_formatters.py`). This file tests the NEW pieces:

* `gpu_request_unhonoured()` (the shared predicate `search`'s exit-code decision delegates to,
  so the decision and the JSON envelope's `native_gpu_unavailable` field can never drift apart).
* The `tg search` CLI wiring that reads that predicate and forces exit 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from typer.testing import CliRunner

from tensor_grep.cli.formatters.json_fmt import gpu_request_unhonoured
from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult

# ---------------------------------------------------------------------------
# Unit tests: the shared predicate in isolation (fast, precise, no CLI plumbing).
# ---------------------------------------------------------------------------


def test_gpu_request_unhonoured_false_when_no_gpu_requested():
    """Control arm (a): no GPU requested at all -- a CPU search that merely served the query
    is complete, not incomplete. This is the arm that stops ruling (a) creeping in and
    promoting every CPU search to exit 2."""
    result = SearchResult(
        matches=[],
        total_files=1,
        total_matches=1,
        requested_gpu_device_ids=[],
        routing_backend="NativeCpuBackend",
        sidecar_used=False,
    )
    assert gpu_request_unhonoured(result) is False


def test_gpu_request_unhonoured_false_when_native_gpu_backend_used_without_sidecar():
    """Control arm (b): GPU explicitly requested AND honoured (NativeGpuBackend,
    sidecar_used=False) -- not unhonoured."""
    result = SearchResult(
        matches=[],
        total_files=1,
        total_matches=1,
        requested_gpu_device_ids=[0],
        routing_backend="NativeGpuBackend",
        sidecar_used=False,
    )
    assert gpu_request_unhonoured(result) is False


def test_gpu_request_unhonoured_true_when_sidecar_routed():
    """Control arm (c): sidecar-routed GPU (NativeGpuBackend + sidecar_used=True) counts as
    UNHONOURED even though routing_backend says "NativeGpuBackend"."""
    result = SearchResult(
        matches=[],
        total_files=1,
        total_matches=1,
        requested_gpu_device_ids=[0],
        routing_backend="NativeGpuBackend",
        sidecar_used=True,
    )
    assert gpu_request_unhonoured(result) is True


def test_gpu_request_unhonoured_true_when_cpu_fallback_used():
    """GPU explicitly requested but the run actually executed on a CPU backend."""
    result = SearchResult(
        matches=[],
        total_files=1,
        total_matches=1,
        requested_gpu_device_ids=[0],
        routing_backend="NativeCpuBackend",
        sidecar_used=False,
    )
    assert gpu_request_unhonoured(result) is True


# ---------------------------------------------------------------------------
# CLI wiring tests: `tg search --gpu-device-ids ...` exit codes end to end.
# ---------------------------------------------------------------------------


@dataclass
class _FakeBackend:
    results_by_file: dict[str, SearchResult] = field(default_factory=dict)

    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        return self.results_by_file.get(
            file_path, SearchResult(matches=[], total_files=0, total_matches=0)
        )


class _FakePipeline:
    """Mirrors `tests/unit/test_cli_modes.py::_FakePipeline` -- a Pipeline stand-in whose
    `selected_backend_name` never matches "RipgrepBackend", so `search_command` always takes
    the per-file native loop and reads its routing decision straight off each
    `SearchResult.routing_backend`/`sidecar_used` the fake backend returns."""

    def __init__(self, force_cpu=False, config=None):
        self.backend = _FAKE_BACKEND
        self.selected_backend_name = "FakeBackend"
        self.selected_backend_reason = "unit_test_fake_pipeline"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = []

    def get_backend(self):
        return self.backend


class _FakeScanner:
    def __init__(self, config=None):
        self.scan_truncated = False
        self.scan_truncation_cause = None
        self.unreadable_path_count = 0
        self.unreadable_path_sample: list[str] = []
        self.max_scan_entries = 200_000

    def walk(self, path):
        yield from _FAKE_WALK.get(path, [])


_FAKE_BACKEND = _FakeBackend(results_by_file={})
_FAKE_WALK: dict[str, list[str]] = {}


def _patch_cli_dependencies(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )


def _set_single_file_result(result: SearchResult) -> None:
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={"a.log": result})


def test_control_no_gpu_device_ids_cpu_search_exits_zero(monkeypatch):
    """MUST-HAVE control arm: a search with NO --gpu-device-ids and a CPU backend still exits
    0. This is the arm that stops ruling (a) creeping in and promoting every CPU search to
    exit 2."""
    _set_single_file_result(
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
            total_files=1,
            total_matches=1,
            routing_backend="NativeCpuBackend",
            sidecar_used=False,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "ERROR", "."])

    assert result.exit_code == 0, result.output


def test_control_explicit_gpu_request_honoured_exits_zero(monkeypatch):
    """MUST-HAVE control arm: a search WITH --gpu-device-ids that IS honoured
    (NativeGpuBackend, sidecar_used=False) exits 0."""
    _set_single_file_result(
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
            total_files=1,
            total_matches=1,
            routing_backend="NativeGpuBackend",
            sidecar_used=False,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(
        app, ["search", "ERROR", ".", "--gpu-device-ids", "0", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["gpu_evidence_status"] == "native"
    assert payload["gpu_proof"] is True
    assert payload["native_gpu_unavailable"] is False


def test_control_sidecar_routed_gpu_counts_as_unhonoured_exits_two(monkeypatch):
    """MUST-HAVE control arm: sidecar-routed GPU (NativeGpuBackend + sidecar_used=True) counts
    as UNHONOURED and exits 2."""
    _set_single_file_result(
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
            total_files=1,
            total_matches=1,
            routing_backend="NativeGpuBackend",
            sidecar_used=True,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(
        app, ["search", "ERROR", ".", "--gpu-device-ids", "0", "--format", "json"]
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["native_gpu_unavailable"] is True


def test_explicit_gpu_request_cpu_fallback_exits_two(monkeypatch):
    """The common real-world case this backlog item exists for: --gpu-device-ids was
    requested, but the run actually executed on a CPU backend (no CUDA/GPU backend available).
    Previously exited 0 (a silent downgrade the caller could not detect from the exit code
    alone); must now exit 2."""
    _set_single_file_result(
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
            total_files=1,
            total_matches=1,
            routing_backend="NativeCpuBackend",
            sidecar_used=False,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "ERROR", ".", "--gpu-device-ids", "0"])

    assert result.exit_code == 2, result.output


def test_explicit_gpu_request_unhonoured_with_no_matches_exits_two_not_one(monkeypatch):
    """The unhonoured-GPU signal must win over the ordinary "no matches" exit 1 -- a search
    that both found nothing AND could not honour an explicit GPU request is still reporting
    that the caller's explicit ask failed, so exit 2 (not 1) is the correct signal."""
    _set_single_file_result(
        SearchResult(
            matches=[],
            total_files=0,
            total_matches=0,
            routing_backend="NativeCpuBackend",
            sidecar_used=False,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "NOMATCH", ".", "--gpu-device-ids", "0"])

    assert result.exit_code == 2, result.output


def test_control_no_matches_no_gpu_request_still_exits_one(monkeypatch):
    """Companion control to the no-matches case above: with no GPU requested at all, an
    ordinary no-match search keeps its normal exit 1 -- the new GPU check must not leak into
    the plain not-found path."""
    _set_single_file_result(
        SearchResult(
            matches=[],
            total_files=0,
            total_matches=0,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "NOMATCH", "."])

    assert result.exit_code == 1, result.output


def test_explicit_gpu_request_unhonoured_with_quiet_flag_exits_two_not_zero(monkeypatch):
    """`--quiet` suppresses output but must not suppress the unhonoured-GPU exit signal."""
    _set_single_file_result(
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
            total_files=1,
            total_matches=1,
            routing_backend="NativeCpuBackend",
            sidecar_used=False,
        )
    )
    _patch_cli_dependencies(monkeypatch)

    result = CliRunner().invoke(app, ["search", "ERROR", ".", "--gpu-device-ids", "0", "--quiet"])

    assert result.exit_code == 2, result.output
