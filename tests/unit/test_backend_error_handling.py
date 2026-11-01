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
    assert result.fallback_reason and "retried on the CPU" in result.fallback_reason


def test_streamed_failure_refuses_cpu_retry(tmp_path, monkeypatch, capsys):
    from tensor_grep.backends.base import PassthroughExecutionError
    from tensor_grep.backends.cpu_backend import CPUBackend
    from tensor_grep.cli.backend_fallback import search_with_cpu_fallback

    monkeypatch.setattr(CPUBackend, "search", lambda *a, **k: pytest.fail("CPU retry"))
    error = PassthroughExecutionError("exit status 2")
    with pytest.raises(PassthroughExecutionError) as caught:
        search_with_cpu_fallback(_make_file(tmp_path), "ERROR", SearchConfig(), error)
    assert caught.value is error
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_streamed_failure_is_an_error(tmp_path, monkeypatch, json_mode):
    import json

    from typer.testing import CliRunner

    from tensor_grep.backends.base import PassthroughExecutionError
    from tensor_grep.cli.main import app

    calls = []

    class FailedBackend:
        def search(self, *args, **kwargs):
            calls.append(True)
            raise PassthroughExecutionError("native ripgrep exit status 2")

    class Pipeline:
        selected_backend_name = "RustCoreBackend"
        selected_backend_reason = "test_route"
        selected_gpu_device_ids = ()

        def __init__(self, **kwargs):
            pass

        def get_backend(self):
            return FailedBackend()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", Pipeline)
    monkeypatch.setenv("TG_DISABLE_NATIVE_TG", "1")
    args = ["search", "ERROR", _make_file(tmp_path)] + (["--json"] if json_mode else ["--stats"])
    result = CliRunner().invoke(app, args)
    assert calls == [True]
    assert result.exit_code == 2
    assert "retried on the CPU" not in result.output
    if json_mode:
        payload = json.loads(result.stdout)
        assert payload["error"] == "backend_error"
        assert "exit status 2" in payload["detail"]
        assert "total_matches" not in payload
    else:
        assert "exit status 2" in result.output


@pytest.mark.parametrize("structured", [False, True])
def test_mcp_streamed_failure_is_not_a_no_match(tmp_path, monkeypatch, structured):
    from unittest.mock import MagicMock

    from tensor_grep.backends.base import PassthroughExecutionError
    from tensor_grep.backends.cpu_backend import CPUBackend
    from tensor_grep.cli import mcp_server

    source = _make_file(tmp_path)
    pipeline = MagicMock()
    pipeline.selected_backend_name = "RustCoreBackend"
    pipeline.selected_backend_reason = "test_route"
    pipeline.selected_gpu_device_ids = []
    pipeline.selected_gpu_chunk_plan_mb = []
    pipeline.get_backend.return_value.search.side_effect = PassthroughExecutionError("private path")
    monkeypatch.setattr(mcp_server, "Pipeline", lambda **kwargs: pipeline)
    monkeypatch.setattr(CPUBackend, "search", lambda *a, **k: pytest.fail("CPU retry"))
    monkeypatch.chdir(tmp_path)
    output = mcp_server.tg_search("ERROR", source, structured_json=structured)
    pipeline.get_backend.return_value.search.assert_called_once()
    assert output.startswith("tg_search failed: internal error (")
    assert "private path" not in output
    assert "total_matches" not in output and "No matches" not in output
