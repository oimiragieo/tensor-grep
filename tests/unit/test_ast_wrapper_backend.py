import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig


def test_raise_for_nonzero_raises_on_truncated_json_from_killed_subprocess():
    """Audit HIGH: a killed/OOM'd sg subprocess emits TRUNCATED JSON that still starts
    with '[' with empty stderr. The old waiver (``stdout.startswith('[')``) masked that as
    a clean 0-match scan; the later json.loads then failed and was swallowed downstream. A
    FULL parse must be required before waiving the nonzero exit."""
    backend = AstGrepWrapperBackend()
    result = subprocess.CompletedProcess(
        args=["sg", "scan"],
        returncode=137,  # 128 + SIGKILL(9): OOM-killer / container memory limit
        stdout='[{"file": "a.py", "range"',  # truncated, invalid JSON
        stderr="",
    )
    with pytest.raises(BackendExecutionError):
        backend._raise_for_nonzero(result)


def test_raise_for_nonzero_waives_complete_json_with_nonzero_exit():
    """A COMPLETE JSON payload with a nonzero exit and no stderr is a real (if quirky)
    result and must still be waived — the fix must not over-raise on valid output."""
    backend = AstGrepWrapperBackend()
    result = subprocess.CompletedProcess(
        args=["sg", "scan"],
        returncode=1,
        stdout="[]",
        stderr="",
    )
    backend._raise_for_nonzero(result)  # must not raise


def test_ast_wrapper_backend_should_use_resolved_binary_path():
    backend = AstGrepWrapperBackend()

    with patch("shutil.which") as which:
        which.side_effect = lambda name: {
            "ast-grep": r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD",
            "ast-grep.exe": None,
            "sg": None,
        }.get(name)

        assert backend._get_binary_name() == r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD"


def test_ast_wrapper_backend_should_ignore_linux_group_sg_binary():
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.stdout = "sg from util-linux 2.39\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": None,
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": "/usr/bin/sg",
        }.get(name)

        assert backend.is_available() is False
        assert backend._get_binary_name() == "ast-grep"


def test_ast_wrapper_backend_should_accept_verified_sg_alias():
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.stdout = "ast-grep 0.39.5\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": None,
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": "/opt/bin/sg",
        }.get(name)

        assert backend.is_available() is True
        assert backend._get_binary_name() == "/opt/bin/sg"


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


def test_ast_wrapper_backend_should_forward_stdin_to_ast_grep_run(monkeypatch):
    backend = AstGrepWrapperBackend()
    monkeypatch.delenv("TG_AST_GREP_TIMEOUT_SECONDS", raising=False)

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
    assert run.call_args.kwargs["timeout"] == 60.0


def test_ast_wrapper_backend_should_allow_timeout_env_override(monkeypatch):
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""
    monkeypatch.setenv("TG_AST_GREP_TIMEOUT_SECONDS", "2.5")

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
            config=SearchConfig(ast=True, lang="python"),
        )

    assert run.call_args.kwargs["timeout"] == 2.5


def test_ast_wrapper_backend_should_surface_ast_grep_timeout():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["sg"], timeout=60),
        ),
        pytest.raises(RuntimeError, match="ast-grep command timed out after 60s"),
    ):
        backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )


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


def test_ast_wrapper_backend_tolerates_per_path_access_warnings_with_findings(capsys):
    # Regression: a single permission-denied / unreadable path in the scan tree
    # makes ast-grep exit nonzero with a warning on stderr while still emitting
    # findings on stdout. tensor-grep must keep the findings (not abort) and
    # forward the warning to stderr, the way ripgrep does.
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = json.dumps([
        {"file": "example.py", "text": "print(x)", "range": {"start": {"line": 0}}}
    ])
    mock_result.stderr = (
        "ERROR: C:\\Users\\me\\AppData\\Local\\Temp\\WinSAT: Access is denied. (os error 5)"
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search_many(
            ["example.py"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert "skipped unreadable paths" in capsys.readouterr().err
