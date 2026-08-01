"""M9: merge_runtime_routing must not misreport a heterogeneous per-file scan.

`merge_runtime_routing` is called once per (file, pattern) by the sidecar/CLI/MCP paths, and
`routing_backend` is last-write-wins -- so on a scan where some files ran on one engine and others
fell back to another (e.g. Torch -> CPU for an unsupported regex path), the aggregate used to report
only whichever file was processed last, silently hiding the mixed routing. The additive
`routing_backends_seen` / `is_mixed_routing` fields carry the truth.
"""

from __future__ import annotations

from tensor_grep.core.result import SearchResult, merge_runtime_routing


def _result(backend: str | None) -> SearchResult:
    return SearchResult(routing_backend=backend)


def test_merge_tracks_mixed_backends() -> None:
    aggregate = _result("TorchBackend")
    merge_runtime_routing(aggregate, _result("CPUBackend"))

    assert aggregate.is_mixed_routing is True
    assert aggregate.routing_backends_seen == ["TorchBackend", "CPUBackend"]
    # routing_backend stays last-write-wins for back-compat; the mix is surfaced additively.
    assert aggregate.routing_backend == "CPUBackend"


def test_merge_single_backend_is_not_mixed() -> None:
    aggregate = _result("CPUBackend")
    merge_runtime_routing(aggregate, _result("CPUBackend"))

    assert aggregate.is_mixed_routing is False
    assert aggregate.routing_backends_seen == ["CPUBackend"]


def test_merge_three_way_mix_dedups_and_preserves_order() -> None:
    aggregate = _result("CPUBackend")
    merge_runtime_routing(aggregate, _result("TorchBackend"))
    merge_runtime_routing(aggregate, _result("CPUBackend"))  # a repeat must not double-count
    merge_runtime_routing(aggregate, _result("StringZillaBackend"))

    assert aggregate.is_mixed_routing is True
    assert aggregate.routing_backends_seen == ["CPUBackend", "TorchBackend", "StringZillaBackend"]


def test_merge_ignores_empty_backend() -> None:
    aggregate = _result("CPUBackend")
    merge_runtime_routing(aggregate, _result(None))  # a sub-result with no routing metadata

    assert aggregate.is_mixed_routing is False
    assert aggregate.routing_backends_seen == ["CPUBackend"]


# Backlog #22: `sidecar_used` must be monotonic like `result_incomplete`, so the `tg search`
# exit-code decision (`gpu_request_unhonoured()`, json_fmt.py) and the `--json` envelope's own
# `sidecar_used` field both see a per-file sidecar signal that a LATER natively-routed file
# cannot silently erase.


def test_merge_sidecar_used_stays_true_once_any_result_reports_it() -> None:
    aggregate = SearchResult(routing_backend="NativeGpuBackend", sidecar_used=False)
    merge_runtime_routing(
        aggregate, SearchResult(routing_backend="NativeGpuBackend", sidecar_used=True)
    )

    assert aggregate.sidecar_used is True


def test_merge_sidecar_used_is_not_reset_by_a_later_non_sidecar_result() -> None:
    """A later per-file result that ran WITHOUT the sidecar must not flip the aggregate back to
    False -- sidecar_used describes "did the sidecar contribute to this aggregate at all", not
    "did the LAST file use the sidecar"."""
    aggregate = SearchResult(routing_backend="NativeGpuBackend", sidecar_used=True)
    merge_runtime_routing(
        aggregate, SearchResult(routing_backend="NativeGpuBackend", sidecar_used=False)
    )

    assert aggregate.sidecar_used is True


def test_merge_sidecar_used_stays_false_when_no_result_reports_it() -> None:
    aggregate = SearchResult(routing_backend="NativeCpuBackend", sidecar_used=False)
    merge_runtime_routing(
        aggregate, SearchResult(routing_backend="NativeCpuBackend", sidecar_used=False)
    )

    assert aggregate.sidecar_used is False
