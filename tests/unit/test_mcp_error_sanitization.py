"""q11-mcp-traceback-leak: MCP tool error responses must not leak raw
exception text (absolute filesystem paths, internal module structure, or a
stack trace) to the client. Failures must still be signaled -- never
swallowed -- but the wire response gets a stable, sanitized message while
the full detail goes to stderr for server-side debugging.
"""

import json
from unittest.mock import MagicMock, patch

from tensor_grep.core.result import SearchResult

# A message shaped like a real Python exception: an absolute filesystem path
# plus internal module structure that must never reach the MCP client.
_SECRET_PATH = r"C:\Users\oimir\secret_project\internal\credentials_loader.py"
_LEAKY_MESSAGE = f"boom while reading {_SECRET_PATH} in module tensor_grep.internal.cache"


def _raise_leaky_error(*_args, **_kwargs):
    raise RuntimeError(_LEAKY_MESSAGE)


def test_sanitized_tool_error_helper_strips_raw_exception_text(capsys):
    """Unit-level: the new sanitization helper never echoes the raw message,
    but does log full detail (incl. traceback) to stderr.
    """
    from tensor_grep.cli import mcp_server

    try:
        _raise_leaky_error()
    except RuntimeError as exc:
        payload = mcp_server._sanitized_tool_error("tg_probe", exc)

    assert payload["code"] == "internal_error"
    assert payload["retryable"] is False
    assert _SECRET_PATH not in payload["message"]
    assert "Traceback" not in payload["message"]
    assert "credentials_loader" not in payload["message"]

    captured = capsys.readouterr()
    # Full detail (path + traceback) is preserved server-side on stderr...
    assert _SECRET_PATH in captured.err
    assert "Traceback" in captured.err
    # ...and never printed to stdout (the MCP JSON-RPC channel).
    assert _SECRET_PATH not in captured.out


def test_sanitized_tool_error_text_helper_strips_raw_exception_text(capsys):
    from tensor_grep.cli import mcp_server

    try:
        _raise_leaky_error()
    except RuntimeError as exc:
        text = mcp_server._sanitized_tool_error_text("tg_probe", exc)

    assert _SECRET_PATH not in text
    assert "Traceback" not in text
    # Still clearly signals failure to the caller.
    assert "failed" in text.lower()

    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err


def test_tg_search_exception_path_does_not_leak_path_or_traceback(capsys):
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "CuDFBackend"
        pipeline.selected_backend_reason = "gpu_explicit_ids_cudf"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.side_effect = _raise_leaky_error

        out = mcp_server.tg_search("ERROR", ".")

    # Failure is still signaled to the caller (fail-closed, never swallowed).
    assert "failed" in out.lower()
    # But the raw path / module structure / traceback never crosses the wire.
    assert _SECRET_PATH not in out
    assert "credentials_loader" not in out
    assert "Traceback" not in out
    assert "internal.cache" not in out

    # Full detail landed server-side on stderr instead.
    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err


def test_tg_ast_search_exception_path_sanitizes_structured_json_error(capsys):
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.side_effect = _raise_leaky_error

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "AstGrepWrapperBackend"
        pipeline.selected_backend_reason = "ast_grep_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.py"]

        out = mcp_server.tg_ast_search("def $A():", "python", ".", structured_json=True)

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    # The structured error still exists (contract preserved) but is sanitized.
    assert _SECRET_PATH not in json.dumps(payload)
    assert "Traceback" not in json.dumps(payload)
    assert "detail" not in payload["error"]

    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err
    assert "Traceback" in captured.err


def test_tg_ast_search_exception_path_sanitizes_plain_text(capsys):
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.side_effect = _raise_leaky_error

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "AstGrepWrapperBackend"
        pipeline.selected_backend_reason = "ast_grep_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.py"]

        out = mcp_server.tg_ast_search("def $A():", "python", ".", structured_json=False)

    assert "failed" in out.lower()
    assert _SECRET_PATH not in out
    assert "Traceback" not in out

    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err


def test_tg_classify_logs_exception_path_does_not_leak_path_or_traceback(tmp_path, capsys):
    from tensor_grep.cli import mcp_server

    log_path = tmp_path / "app.log"
    log_path.write_text("INFO startup ok\nERROR database failed\n", encoding="utf-8")

    with patch(
        "tensor_grep.sidecar._classify_lines_with_metadata",
        side_effect=_raise_leaky_error,
    ):
        out = mcp_server.tg_classify_logs(str(log_path), structured_json=True)

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert _SECRET_PATH not in json.dumps(payload)
    assert "Traceback" not in json.dumps(payload)
    assert "detail" not in payload["error"]

    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err


def test_tg_classify_logs_exception_path_sanitizes_plain_text(tmp_path, capsys):
    from tensor_grep.cli import mcp_server

    log_path = tmp_path / "app.log"
    log_path.write_text("INFO startup ok\nERROR database failed\n", encoding="utf-8")

    with patch(
        "tensor_grep.sidecar._classify_lines_with_metadata",
        side_effect=_raise_leaky_error,
    ):
        out = mcp_server.tg_classify_logs(str(log_path), structured_json=False)

    assert "failed" in out.lower()
    assert _SECRET_PATH not in out
    assert "Traceback" not in out

    captured = capsys.readouterr()
    assert _SECRET_PATH in captured.err


# Sanity: unrelated code paths (e.g. a normal SearchResult) are unaffected by
# the sanitization helpers -- this guards against a fix that accidentally
# swallows success responses.
def test_tg_search_success_path_is_unaffected_by_sanitization(capsys):
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(matches=[], total_files=0, total_matches=0)

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "CuDFBackend"
        pipeline.selected_backend_reason = "gpu_explicit_ids_cudf"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = []

        out = mcp_server.tg_search("ERROR", ".")

    payload = json.loads(out)
    assert payload["total_matches"] == 0
    captured = capsys.readouterr()
    assert captured.err == ""
