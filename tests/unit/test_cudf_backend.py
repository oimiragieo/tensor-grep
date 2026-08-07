"""H6 audit: CuDFBackend was the only backend with ZERO BackendExecutionError raises --
GPU OOM / driver / regex faults escaped raw, so main's per-file CPU-fallback retry
(`except BackendExecutionError`, cli/main.py:8300-8308) never fired and a GPU fault
raised an uncaught traceback instead of degrading to CPU. The Backend Fail-Closed
Contract (AGENTS.md) requires every backend to raise BackendExecutionError on a real
engine failure. RED first: each test targets a pre-fix failure.
"""

from __future__ import annotations

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.core.config import SearchConfig


def test_engine_failure_becomes_backend_execution_error(monkeypatch) -> None:
    """A raw engine failure (e.g. cuDF regex fault / OOM) must surface as
    BackendExecutionError (so the CPU-fallback retry can fire), never escape raw."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("cudf CUDA out of memory")

    monkeypatch.setattr(CuDFBackend, "_search_uncapped", _boom)
    backend = CuDFBackend()
    with pytest.raises(BackendExecutionError) as excinfo:
        backend.search("a.log", "foo")
    assert "cudf" in str(excinfo.value).lower() or "memory" in str(excinfo.value).lower()


def test_normal_result_untouched(monkeypatch) -> None:
    from tensor_grep.core.result import SearchResult

    def _ok(*_args, **_kwargs):
        return SearchResult(matches=[], total_files=0, total_matches=0)

    monkeypatch.setattr(CuDFBackend, "_search_uncapped", _ok)
    result = CuDFBackend().search("a.log", "foo", SearchConfig(max_count=5))
    assert result.total_matches == 0  # still wrapped through _cap_to_max_count


def test_backend_execution_error_re_raised_verbatim(monkeypatch) -> None:
    """An already-normalized BackendExecutionError from the engine must pass through
    unchanged (not double-wrapped in a new message)."""

    def _raise(*_args, **_kwargs):
        raise BackendExecutionError("engine-specific")

    monkeypatch.setattr(CuDFBackend, "_search_uncapped", _raise)
    backend = CuDFBackend()
    with pytest.raises(BackendExecutionError) as excinfo:
        backend.search("a.log", "foo")
    assert str(excinfo.value) == "engine-specific"
