from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig


def test_should_include_before_and_after_context_flags():
    backend = RipgrepBackend()
    config = SearchConfig(before_context=2, after_context=3)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = ""

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search("test.log", "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "-B" in cmd and "2" in cmd
    assert "-A" in cmd and "3" in cmd


def test_should_forward_no_ignore_flag():
    backend = RipgrepBackend()
    config = SearchConfig(no_ignore=True)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = ""

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search("test.log", "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--no-ignore" in cmd


def test_should_forward_glob_flags():
    backend = RipgrepBackend()
    config = SearchConfig(glob=["*.log", "!*.tmp"])

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = ""

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search("test.log", "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert cmd.count("-g") == 2
    assert "*.log" in cmd
    assert "!*.tmp" in cmd


def test_should_raise_on_rg_fatal_error():
    backend = RipgrepBackend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "regex parse error"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch("tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result),
    ):
        with pytest.raises(RuntimeError, match="exit code 2"):
            backend.search("test.log", "(")
