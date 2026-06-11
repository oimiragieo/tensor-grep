"""Regression tests for M2: BackendExecutionError (not bare RuntimeError/traceback)
is raised by AstGrepWrapperBackend when --selector/--strictness causes ast-grep to
exit with code 8 or when semantic run options are combined with multiline patterns.
"""

from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig


def _patched_backend() -> AstGrepWrapperBackend:
    backend = AstGrepWrapperBackend()
    return backend


def test_exit8_selector_mismatch_raises_backend_execution_error():
    """ast-grep exit 8 (bad selector/pattern combo) must raise BackendExecutionError,
    not a bare RuntimeError, so callers can produce a structured JSON error envelope."""
    backend = _patched_backend()

    mock_result = MagicMock()
    mock_result.returncode = 8
    mock_result.stdout = ""
    mock_result.stderr = (
        "Error: Cannot parse query as a valid pattern.\n"
        "selector `function_definition` matches no node in the context `foo()`."
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=mock_result,
        ),
        pytest.raises(BackendExecutionError, match="ast-grep rejected the query \\(exit 8\\)"),
    ):
        backend.search_many(
            ["src"],
            "foo($A)",
            config=SearchConfig(ast=True, lang="python", ast_selector="function_definition"),
        )


def test_exit8_error_is_subclass_of_runtime_error():
    """BackendExecutionError is-a RuntimeError so existing except RuntimeError catches
    in main.py scan still work after this change."""
    backend = _patched_backend()

    mock_result = MagicMock()
    mock_result.returncode = 8
    mock_result.stdout = ""
    mock_result.stderr = "Error: Cannot parse query as a valid pattern."

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=mock_result,
        ),
        pytest.raises(RuntimeError),
    ):
        backend.search_many(
            ["src"],
            "foo($A)",
            config=SearchConfig(ast=True, lang="python", ast_selector="call"),
        )


def test_exit8_error_message_contains_selector_hint():
    """The error message must contain a hint about --selector so the user understands
    the cause without reading a raw traceback."""
    backend = _patched_backend()

    mock_result = MagicMock()
    mock_result.returncode = 8
    mock_result.stdout = ""
    mock_result.stderr = "Error: Cannot parse query."

    exc_info: list[BackendExecutionError] = []

    def capturing_search() -> None:
        try:
            backend.search_many(
                ["src"],
                "bar($X)",
                config=SearchConfig(ast=True, lang="python", ast_selector="bad_kind"),
            )
        except BackendExecutionError as exc:
            exc_info.append(exc)
            raise

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=mock_result,
        ),
        pytest.raises(BackendExecutionError),
    ):
        capturing_search()

    assert exc_info, "exception was not captured"
    msg = str(exc_info[0])
    assert "--selector" in msg or "selector" in msg.lower()


def test_multiline_with_selector_raises_backend_execution_error():
    """Multiline pattern + --selector must raise BackendExecutionError (not bare
    RuntimeError) so callers can surface a structured error."""
    backend = _patched_backend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        pytest.raises(BackendExecutionError, match="multiline"),
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


def test_multiline_with_strictness_raises_backend_execution_error():
    """Multiline pattern + --strictness also must raise BackendExecutionError."""
    backend = _patched_backend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        pytest.raises(BackendExecutionError, match="multiline"),
    ):
        backend.search_many(
            ["src"],
            "class $C:\n    pass",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_strictness="strict",
            ),
        )


def test_strictness_exit8_search_single_file():
    """--strictness causing exit 8 on search() (single-file path) also raises
    BackendExecutionError, not a bare RuntimeError."""
    backend = _patched_backend()

    mock_result = MagicMock()
    mock_result.returncode = 8
    mock_result.stdout = ""
    mock_result.stderr = "Error: Cannot parse query as a valid pattern."

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=mock_result,
        ),
        pytest.raises(BackendExecutionError, match="exit 8"),
    ):
        backend.search(
            "example.py",
            "foo($A)",
            config=SearchConfig(ast=True, lang="python", ast_strictness="strict"),
        )


def test_other_nonzero_exit_still_raises_backend_execution_error():
    """Non-8 exit codes also raise BackendExecutionError (not bare RuntimeError)."""
    backend = _patched_backend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "invalid rule config"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=mock_result,
        ),
        pytest.raises(BackendExecutionError, match="exit code 2"),
    ):
        backend.search_many(
            ["src"],
            "def $F():",
            config=SearchConfig(ast=True, lang="python"),
        )
