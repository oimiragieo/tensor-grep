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


def test_should_forward_advertised_ignore_and_config_flags():
    backend = RipgrepBackend()
    config = SearchConfig(
        ignore_file=[".custom-ignore", ".repo-ignore"],
        ignore_file_case_insensitive=True,
        max_depth=2,
        no_ignore_dot=True,
        no_ignore_exclude=True,
        no_ignore_files=True,
        no_ignore_global=True,
        no_ignore_parent=True,
        no_ignore_vcs=True,
        no_require_git=True,
        one_file_system=True,
        type_not=["python"],
        type_add=["logs:*.log"],
        type_clear="web",
        no_config=True,
    )

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
    for flag in (
        "--ignore-file",
        "--ignore-file-case-insensitive",
        "--max-depth",
        "--no-ignore-dot",
        "--no-ignore-exclude",
        "--no-ignore-files",
        "--no-ignore-global",
        "--no-ignore-parent",
        "--no-ignore-vcs",
        "--no-require-git",
        "--one-file-system",
        "-T",
        "--type-add",
        "--type-clear",
        "--no-config",
    ):
        assert flag in cmd
    assert cmd.count("--ignore-file") == 2
    assert ["--max-depth", "2"] == cmd[cmd.index("--max-depth") :][:2]
    assert ["-T", "python"] == cmd[cmd.index("-T") :][:2]
    assert ["--type-add", "logs:*.log"] == cmd[cmd.index("--type-add") :][:2]
    assert ["--type-clear", "web"] == cmd[cmd.index("--type-clear") :][:2]


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


def test_passthrough_should_forward_editor_output_flags():
    backend = RipgrepBackend()
    config = SearchConfig(
        block_buffered=True,
        byte_offset=True,
        column=True,
        colors=["match:fg:red"],
        context_separator="@@",
        field_context_separator="~",
        field_match_separator="|",
        heading=False,
        include_zero=True,
        line_buffered=True,
        max_columns=120,
        max_columns_preview=True,
        path_separator="/",
        sort_files=True,
        trim=True,
        vimgrep=True,
    )

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        exit_code = backend.search_passthrough(["src"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--block-buffered" in cmd
    assert "-b" in cmd
    assert "--column" in cmd
    assert ["--colors", "match:fg:red"] == cmd[cmd.index("--colors") :][:2]
    assert ["--context-separator", "@@"] == cmd[cmd.index("--context-separator") :][:2]
    assert ["--field-context-separator", "~"] == cmd[cmd.index("--field-context-separator") :][:2]
    assert ["--field-match-separator", "|"] == cmd[cmd.index("--field-match-separator") :][:2]
    assert "--no-heading" in cmd
    assert "--include-zero" in cmd
    assert "--line-buffered" in cmd
    assert ["--max-columns", "120"] == cmd[cmd.index("--max-columns") :][:2]
    assert "--max-columns-preview" in cmd
    assert ["--path-separator", "/"] == cmd[cmd.index("--path-separator") :][:2]
    assert "--sort-files" in cmd
    assert "--trim" in cmd
    assert "--vimgrep" in cmd
    assert exit_code == 0


def test_passthrough_should_forward_multiline_flags():
    backend = RipgrepBackend()
    config = SearchConfig(multiline=True, multiline_dotall=True)

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        exit_code = backend.search_passthrough(
            ["src"], r"create_invoice[\s\S]*return", config=config
        )

    cmd = run.call_args[0][0]
    assert "--multiline" in cmd
    assert "--multiline-dotall" in cmd
    assert exit_code == 0


def test_passthrough_should_forward_advertised_regex_mode_flags():
    backend = RipgrepBackend()
    config = SearchConfig(auto_hybrid_regex=True, unicode=True)

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        exit_code = backend.search_passthrough(["src"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--auto-hybrid-regex" in cmd
    assert "--unicode" in cmd
    assert exit_code == 0


def test_passthrough_should_forward_passthru_flag():
    backend = RipgrepBackend()
    config = SearchConfig(passthru=True)

    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        exit_code = backend.search_passthrough(["src"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--passthru" in cmd
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


def test_search_should_parse_nul_count_output_without_json():
    backend = RipgrepBackend()
    config = SearchConfig(count=True, null=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "C:\\repo\\a.log\x004\nC:\\repo\\b.log\x000\n"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search(["C:\\repo\\a.log", "C:\\repo\\b.log"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "-0" in cmd
    assert "-c" in cmd
    assert result.matched_file_paths == ["C:\\repo\\a.log"]
    assert result.match_counts_by_file == {"C:\\repo\\a.log": 4}
    assert result.total_files == 1
    assert result.total_matches == 4


def test_search_should_parse_files_with_matches_without_json():
    backend = RipgrepBackend()
    config = SearchConfig(files_with_matches=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "a.log\nb.log\n"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search(["a.log", "b.log"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "--json" not in cmd
    assert "--files-with-matches" in cmd
    assert result.matches == []
    assert result.matched_file_paths == ["a.log", "b.log"]
    assert result.total_files == 2
    assert result.total_matches == 2
    assert result.routing_backend == "RipgrepBackend"
    assert result.routing_reason == "rg_files_with_matches"


def test_search_should_parse_nul_files_with_matches_without_json():
    backend = RipgrepBackend()
    config = SearchConfig(files_with_matches=True, null=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "a.log\0b.log\0"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search(["a.log", "b.log"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "-0" in cmd
    assert "--files-with-matches" in cmd
    assert result.matched_file_paths == ["a.log", "b.log"]
    assert result.match_counts_by_file == {"a.log": 1, "b.log": 1}


def test_search_should_parse_files_with_matches_from_count_without_rg_list_flag():
    backend = RipgrepBackend()
    config = SearchConfig(count=True, files_with_matches=True)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    mock_result.stdout = "a.log:4\nb.log:0\n"

    with (
        patch.object(backend, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search(["a.log", "b.log"], "ERROR", config=config)

    cmd = run.call_args[0][0]
    assert "-c" in cmd
    assert "--files-with-matches" not in cmd
    assert result.matched_file_paths == ["a.log"]
    assert result.match_counts_by_file == {"a.log": 4}
    assert result.total_files == 1
    assert result.total_matches == 4
