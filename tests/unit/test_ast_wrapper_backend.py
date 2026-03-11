from unittest.mock import MagicMock, patch

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.core.config import SearchConfig


def test_ast_wrapper_backend_should_use_resolved_binary_path():
    backend = AstGrepWrapperBackend()
    AstGrepWrapperBackend._cached_binary_name = None
    AstGrepWrapperBackend._binary_name_resolved = False

    with patch("shutil.which") as which:
        which.side_effect = lambda name: {
            "ast-grep": r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD",
            "ast-grep.exe": None,
            "sg": None,
        }.get(name)

        assert backend._get_binary_name() == r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD"


def test_ast_wrapper_backend_should_cache_binary_resolution():
    first = AstGrepWrapperBackend()
    second = AstGrepWrapperBackend()
    AstGrepWrapperBackend._cached_binary_name = None
    AstGrepWrapperBackend._binary_name_resolved = False

    with patch("shutil.which") as which:
        which.side_effect = lambda name: {
            "ast-grep": None,
            "ast-grep.exe": None,
            "sg": "sg",
        }.get(name)

        assert first._get_binary_name() == "sg"
        assert second._get_binary_name() == "sg"

    assert which.call_count == 3


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
