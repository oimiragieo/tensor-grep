"""A runtime backend failure must surface as an error, never a false no-match (B2/I1).

RustCoreBackend previously returned ``SearchResult(total_matches=0,
routing_reason="rust_exception")`` for ANY non-regex failure, making a native panic /
IO / version-skew error indistinguishable from a genuine no-match. It must now raise
``BackendExecutionError``, and the CLI must retry on the CPU backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import InvalidRegexError
from tensor_grep.backends.rust_backend import RustCoreBackend
from tensor_grep.core.config import SearchConfig


class _RaisingInner:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, _name):  # count_matches / search / execute_ripgrep
        def _raise(*_args, **_kwargs):
            raise self._exc

        return _raise


def _make_file(tmp_path: Path) -> str:
    f = tmp_path / "data.txt"
    f.write_text("alpha\nbeta\nERROR here\n", encoding="utf-8")
    return str(f)


def test_runtime_failure_raises_backend_execution_error(tmp_path: Path) -> None:
    backend = RustCoreBackend()
    backend.inner = _RaisingInner(RuntimeError("native panic: kaboom"))

    with pytest.raises(BackendExecutionError):
        backend.search(_make_file(tmp_path), "ERROR", config=SearchConfig())


def test_runtime_failure_does_not_return_empty_success(tmp_path: Path) -> None:
    backend = RustCoreBackend()
    backend.inner = _RaisingInner(RuntimeError("io error"))

    # The bug was returning a 0-match SearchResult; assert it raises instead.
    try:
        result = backend.search(_make_file(tmp_path), "ERROR", config=SearchConfig())
    except BackendExecutionError:
        return
    pytest.fail(f"expected BackendExecutionError, got a result: {result!r}")


def test_regex_error_still_raises_invalid_regex(tmp_path: Path) -> None:
    backend = RustCoreBackend()
    backend.inner = _RaisingInner(RuntimeError("regex parse error: unclosed group"))

    with pytest.raises(InvalidRegexError):
        backend.search(_make_file(tmp_path), "(", config=SearchConfig())


def test_cli_cpu_fallback_returns_real_matches(tmp_path: Path) -> None:
    from tensor_grep.cli.main import _search_with_cpu_fallback

    f = tmp_path / "data.txt"
    f.write_text("alpha\nERROR here\nbeta\n", encoding="utf-8")

    result = _search_with_cpu_fallback(
        str(f), "ERROR", SearchConfig(), BackendExecutionError("boom")
    )
    assert result.total_matches >= 1
    assert any("ERROR" in m.text for m in result.matches)
