from unittest.mock import MagicMock, patch

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.core.config import SearchConfig


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
