from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.core.config import SearchConfig


def test_ast_wrapper_backend_should_use_resolved_binary_path():
    backend = AstGrepWrapperBackend()

    with patch("shutil.which") as which:
        which.side_effect = lambda name: {
            "ast-grep": r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD",
            "ast-grep.exe": None,
            "sg": None,
        }.get(name)

        assert backend._get_binary_name() == r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD"


def test_ast_wrapper_backend_should_emit_runtime_routing_metadata():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","range":{"start":{"line":0}}},'
        '{"text":"def world():","range":{"start":{"line":4}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 2
    assert result.routing_backend == "AstGrepWrapperBackend"
    assert result.routing_reason == "ast_grep_json"
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_ast_wrapper_backend_should_batch_many_files():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"a.py","range":{"start":{"line":0}}},'
        '{"text":"class World:","file":"b.py","range":{"start":{"line":4}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(
            backend,
            "_get_binary_name",
            return_value=r"C:\\Users\\oimir\\AppData\\Roaming\\npm\\ast-grep.CMD",
        ),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search_many(
            ["a.py", "b.py"],
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert run.call_args.args[0][-2:] == ["a.py", "b.py"]
    assert result.total_matches == 2
    assert result.total_files == 2
    assert result.matched_file_paths == ["a.py", "b.py"]


def test_ast_wrapper_backend_should_forward_ast_grep_run_semantic_options():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_selector="call",
                ast_strictness="relaxed",
                glob=["*.py", "!generated/**"],
            ),
        )

    assert run.call_args.args[0] == [
        "sg",
        "run",
        "--json",
        "-p",
        "print($A)",
        "--lang",
        "python",
        "--selector",
        "call",
        "--strictness",
        "relaxed",
        "--globs",
        "*.py",
        "--globs",
        "!generated/**",
        "src",
    ]


def test_ast_wrapper_backend_should_forward_stdin_to_ast_grep_run():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            [],
            "print($A)",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_stdin=True,
                ast_stdin_input="print('hello')\n",
            ),
        )

    assert run.call_args.args[0] == [
        "sg",
        "run",
        "--json",
        "-p",
        "print($A)",
        "--lang",
        "python",
        "--stdin",
    ]
    assert run.call_args.kwargs["input"] == "print('hello')\n"


def test_ast_wrapper_backend_should_treat_empty_json_nonzero_as_no_match():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 0


def test_ast_wrapper_backend_should_reject_multiline_semantic_run_options():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        pytest.raises(RuntimeError, match="multiline"),
    ):
        backend.search_many(
            ["src"],
            "def $NAME():\n    $$$BODY",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_selector="function_definition",
            ),
        )


def test_ast_wrapper_backend_should_use_rule_file_for_multiline_patterns():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = "[]"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search(
            "example.py",
            "def $FUNC():\n    $$$BODY",
            config=SearchConfig(ast=True, lang="python"),
        )

    cmd = run.call_args.args[0]
    assert cmd[:4] == ["sg", "scan", "--json", "--rule"]
    assert cmd[-1] == "example.py"


def test_ast_wrapper_backend_should_preserve_range_and_meta_variables():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello(name):",'
        '"file":"example.py",'
        '"range":{"byteOffset":{"start":0,"end":16},'
        '"start":{"line":0,"column":0},'
        '"end":{"line":0,"column":16}},'
        '"metaVariables":{"single":{"F":{"text":"hello"}},"multi":{"ARGS":[{"text":"name"}]}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "def $F($$$ARGS):",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert result.matches[0].range == {
        "byteOffset": {"start": 0, "end": 16},
        "start": {"line": 0, "column": 0},
        "end": {"line": 0, "column": 16},
    }
    assert result.matches[0].meta_variables == {
        "single": {"F": {"text": "hello"}},
        "multi": {"ARGS": [{"text": "name"}]},
    }


def test_ast_wrapper_backend_should_tolerate_non_mapping_range_payload():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"example.py","range":null,'
        '"metaVariables":{"single":{"F":{"text":"hello"}}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "def $F():",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert result.matches[0].line_number == 1
    assert result.matches[0].range is None


def test_ast_wrapper_backend_should_group_project_scan_results_by_rule_id():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"a.py","ruleId":"rule-a","range":{"start":{"line":0}}},'
        '{"text":"def world():","file":"b.py","ruleId":"rule-b","range":{"start":{"line":4}}},'
        '{"text":"def again():","file":"a.py","ruleId":"rule-a","range":{"start":{"line":8}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        results = backend.search_project("project", "sgconfig.yml")

    assert run.call_args.args[0] == ["sg", "scan", "--json", "--config", "sgconfig.yml", "project"]
    assert results["rule-a"].total_matches == 2
    assert results["rule-a"].matched_file_paths == ["a.py"]
    assert results["rule-b"].total_matches == 1
    assert results["rule-b"].matched_file_paths == ["b.py"]


def test_ast_wrapper_backend_should_surface_nonzero_search_many_errors():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "invalid rule config"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="invalid rule config"),
    ):
        backend.search_many(
            ["example.py"],
            "def $FUNC($$$ARGS):",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_surface_nonzero_project_scan_errors():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = "[]"
    mock_result.stderr = "failed to parse config"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="failed to parse config"),
    ):
        backend.search_project("project", "sgconfig.yml")
