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


def test_passthrough_should_forward_count_flag_and_exit_code():
    backend = RipgrepBackend()
    config = SearchConfig(count=True, no_ignore=True)

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        exit_code = backend.search_passthrough(["bench_data"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "-c" in cmd
    assert "--no-ignore" in cmd
    assert exit_code == 0


def test_search_should_emit_runtime_routing_metadata():
    backend = RipgrepBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = (
        '{"type":"match","data":{"path":{"text":"a.log"},"lines":{"text":"ERROR one\\n"},'
        '"line_number":2}}\n'
    )

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch("tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search("a.log", "ERROR", config=SearchConfig())

    assert result.total_matches == 1
    assert result.routing_backend == "RipgrepBackend"
    assert result.routing_reason == "rg_json"
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_search_should_keep_line_numbers_in_json_mode():
    backend = RipgrepBackend()
    config = SearchConfig(line_number=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = (
        '{"type":"match","data":{"path":{"text":"a.log"},"lines":{"text":"ERROR one\\n"},'
        '"line_number":2}}\n'
    )

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search("a.log", "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--json" in cmd
    assert "--no-line-number" not in cmd
    assert result.matches[0].line_number == 2


def test_search_should_parse_plain_count_output_without_json():
    backend = RipgrepBackend()
    config = SearchConfig(count=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "a.log:2\nb.log:1\n"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search(["a.log", "b.log"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--json" not in cmd
    assert "-c" in cmd
    assert result.total_matches == 3
    assert result.total_files == 2
    assert result.matched_file_paths == ["a.log", "b.log"]
    assert result.match_counts_by_file == {"a.log": 2, "b.log": 1}
    assert result.routing_backend == "RipgrepBackend"
    assert result.routing_reason == "rg_count"


def test_search_should_parse_plain_count_matches_output_without_json():
    backend = RipgrepBackend()
    config = SearchConfig(count_matches=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "4\n"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search("a.log", "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--json" not in cmd
    assert "--count-matches" in cmd
    assert result.total_matches == 4
    assert result.total_files == 1
    assert result.matched_file_paths == ["a.log"]
    assert result.match_counts_by_file == {"a.log": 4}
    assert result.routing_backend == "RipgrepBackend"
    assert result.routing_reason == "rg_count_matches"
