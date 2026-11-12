"""CPU subclasses retain the CPU refusal-to-retry contract."""

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.mcp_arg_validation import probe_backend
from tensor_grep.core.config import SearchConfig


def test_cpu_subclass_failure_is_not_retried(tmp_path, monkeypatch):
    class InstrumentedCPUBackend(CPUBackend):
        pass

    calls = []
    error = BackendExecutionError("instrumented CPU failed")

    def failed(self, *args, **kwargs):
        calls.append(type(self))
        raise error

    monkeypatch.setattr(CPUBackend, "search", failed)
    with pytest.raises(BackendExecutionError) as caught:
        probe_backend(InstrumentedCPUBackend(), "ERROR", SearchConfig())
    assert calls == [InstrumentedCPUBackend]
    assert caught.value is error
