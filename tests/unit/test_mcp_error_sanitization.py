"""q11-mcp-traceback-leak: MCP tool error responses must not leak raw
exception text (absolute filesystem paths, internal module structure, or a
stack trace) to the client. Failures must still be signaled -- never
swallowed -- but the wire response gets a stable, sanitized message while
the full detail goes to stderr for server-side debugging.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

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


def test_tg_classify_logs_exception_path_does_not_leak_path_or_traceback(
    tmp_path, capsys, monkeypatch
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #81 #1)
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


def test_tg_classify_logs_exception_path_sanitizes_plain_text(tmp_path, capsys, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #81 #1)
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


def test_broad_mcp_handlers_never_echo_raw_str_exc_ast_ratchet():
    """SEC-007: every broad ``except Exception`` / bare ``except`` arm must not
    echo exception contents (via str(exc), repr(exc), format(exc), f"{exc}", exc.args, etc.)
    on the wire. Zero exceptions permitted across all 3 tool modules.
    """
    import ast
    from pathlib import Path

    target_files = [
        "mcp_server.py",
        "mcp_symbol_tools.py",
        "mcp_audit_tools.py",
        "mcp_rewrite_tools.py",
    ]
    cli_dir = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli"

    def is_stderr_call(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Call):
                for kw in curr.keywords:
                    if kw.arg == "file":
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "stderr":
                            return True
                        if isinstance(kw.value, ast.Name) and kw.value.id == "stderr":
                            return True
            curr = getattr(curr, "parent", None)
        return False

    def is_sanitized_helper_call(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Call):
                if isinstance(curr.func, ast.Name) and curr.func.id in {
                    "_sanitized_tool_error",
                    "_sanitized_tool_error_text",
                    "_log_tool_exception",
                    "_classify_native_rewrite_failure",
                    "_safe_exception_class_name",
                }:
                    return True
            curr = getattr(curr, "parent", None)
        return False

    def is_raise_cause(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Raise) and curr.cause == node:
                return True
            curr = getattr(curr, "parent", None)
        return False

    def is_safe_class_name_attr(node, var_name):
        # Allow _safe_exception_class_name(exc) call
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_safe_exception_class_name"
        ):
            if any(isinstance(a, ast.Name) and a.id == var_name for a in node.args):
                return True
        return False

    def find_broad_offenders(ast_tree, source_lines):
        offenders: list[tuple[int, str, str]] = []
        for node in ast.walk(ast_tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            is_broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception"
            )
            if not is_broad:
                continue

            name = node.name
            if not name:
                continue

            for child in ast.walk(node):
                lineno = getattr(child, "lineno", None)
                if lineno is None:
                    continue
                line = source_lines[lineno - 1].strip() if source_lines else ""

                if (
                    is_stderr_call(child)
                    or is_sanitized_helper_call(child)
                    or is_raise_cause(child)
                ):
                    continue

                # 1. str(exc), repr(exc), format(exc)
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id in {
                        "str",
                        "repr",
                        "format",
                    }:
                        if any(isinstance(a, ast.Name) and a.id == name for a in child.args):
                            offenders.append((lineno, "call", line))
                    # "...{}".format(exc)
                    elif isinstance(child.func, ast.Attribute) and child.func.attr == "format":
                        if any(isinstance(a, ast.Name) and a.id == name for a in child.args):
                            offenders.append((lineno, "format_call", line))

                # 2. JoinedStr f"...{exc}..."
                elif isinstance(child, ast.JoinedStr):
                    for val in child.values:
                        if isinstance(val, ast.FormattedValue):
                            if is_safe_class_name_attr(val.value, name):
                                continue
                            for sub in ast.walk(val.value):
                                if isinstance(sub, ast.Name) and sub.id == name:
                                    offenders.append((lineno, "f-string", line))
                                    break

                # 3. Attribute exc.args
                elif isinstance(child, ast.Attribute) and child.attr == "args":
                    if isinstance(child.value, ast.Name) and child.value.id == name:
                        offenders.append((lineno, "args", line))

        return offenders

    for filename in target_files:
        src_path = cli_dir / filename
        source = src_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()

        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent

        real_offenders = find_broad_offenders(tree, lines)
        assert real_offenders == [], (
            f"broad except Exception/bare-except arms in {filename} must not echo exception formatting "
            f"on the MCP wire (SEC-007); offenders: {real_offenders}"
        )

    # Negative controls: assert that all 6 banned exception stringification forms are detected
    test_snippets = [
        "try:\n    pass\nexcept Exception as exc:\n    return str(exc)",
        'try:\n    pass\nexcept Exception as exc:\n    return f"fail: {exc}"',
        "try:\n    pass\nexcept Exception as exc:\n    return repr(exc)",
        "try:\n    pass\nexcept Exception as exc:\n    return format(exc)",
        'try:\n    pass\nexcept Exception as exc:\n    return "fail: {}".format(exc)',
        "try:\n    pass\nexcept Exception as exc:\n    return exc.args[0]",
    ]
    for i, snippet in enumerate(test_snippets):
        dummy_tree = ast.parse(snippet)
        for p in ast.walk(dummy_tree):
            for c in ast.iter_child_nodes(p):
                c.parent = p
        dummy_lines = snippet.splitlines()
        found = find_broad_offenders(dummy_tree, dummy_lines)
        assert len(found) > 0, f"Ratchet failed to catch negative control {i}: {snippet}"


def test_narrow_mcp_handlers_never_echo_raw_exception_formatting_ast_ratchet():
    """SEC-007: Narrow exception handlers across src/tensor_grep/cli/mcp_server.py
    must never format or interpolate the exception instance (via f"{exc}", str(exc),
    repr(exc), format(exc), exc.args, etc.) onto the wire.
    PathConfinementError handlers are authorized because PathConfinementError emits a
    constant, non-leaking message without candidate paths or tracebacks.
    """
    import ast
    from pathlib import Path

    target_files = [
        "mcp_server.py",
        "mcp_symbol_tools.py",
        "mcp_audit_tools.py",
        "mcp_rewrite_tools.py",
    ]
    cli_dir = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli"

    def is_stderr_call(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Call):
                for kw in curr.keywords:
                    if kw.arg == "file":
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "stderr":
                            return True
                        if isinstance(kw.value, ast.Name) and kw.value.id == "stderr":
                            return True
                if isinstance(curr.func, ast.Attribute) and curr.func.attr == "write":
                    if (
                        isinstance(curr.func.value, ast.Attribute)
                        and curr.func.value.attr == "stderr"
                    ):
                        return True
            curr = getattr(curr, "parent", None)
        return False

    def is_sanitized_helper_call(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Call):
                if isinstance(curr.func, ast.Name) and curr.func.id in {
                    "_sanitized_tool_error",
                    "_sanitized_tool_error_text",
                    "_log_tool_exception",
                    "_classify_native_rewrite_failure",
                    "_sanitize_policy_validation_details",
                    "_safe_exception_class_name",
                }:
                    return True
            curr = getattr(curr, "parent", None)
        return False

    def is_raise_cause(node):
        curr = node
        while curr:
            if isinstance(curr, ast.Raise) and curr.cause == node:
                return True
            curr = getattr(curr, "parent", None)
        return False

    def is_safe_class_name_attr(node, var_name):
        # Allow _safe_exception_class_name(exc) call
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_safe_exception_class_name"
        ):
            if any(isinstance(a, ast.Name) and a.id == var_name for a in node.args):
                return True
        return False

    def find_narrow_offenders(ast_tree, source_lines):
        offenders: list[tuple[int, str, str, str]] = []
        for node in ast.walk(ast_tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            is_broad = node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception"
            )
            if is_broad:
                continue
            h_type = (
                node.type.id
                if isinstance(node.type, ast.Name)
                else (
                    node.type.attr
                    if isinstance(node.type, ast.Attribute)
                    else type(node.type).__name__
                )
            )
            if h_type == "PathConfinementError":
                continue

            name = node.name
            lineno = getattr(node, "lineno", 0)
            line = source_lines[lineno - 1].strip() if source_lines and lineno > 0 else ""

            calls_error = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and (n.func.id.endswith("_error") or n.func.id.startswith("_sanitized_"))
                for n in ast.walk(node)
            )

            if not name:
                if calls_error:
                    offenders.append((lineno, h_type, "unbound_narrow_handler", line))
                continue

            has_log = any(
                (
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name)
                    and c.func.id
                    in {
                        "_log_tool_exception",
                        "_sanitized_tool_error",
                        "_sanitized_tool_error_text",
                    }
                )
                or is_stderr_call(c)
                for c in ast.walk(node)
            )
            if calls_error and not has_log:
                offenders.append((lineno, h_type, "missing_stderr_log", line))

            for child in ast.walk(node):
                c_lineno = getattr(child, "lineno", lineno)
                c_line = source_lines[c_lineno - 1].strip() if source_lines and c_lineno > 0 else ""

                if (
                    is_stderr_call(child)
                    or is_sanitized_helper_call(child)
                    or is_raise_cause(child)
                ):
                    continue

                # 1. str(exc), repr(exc), format(exc)
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id in {
                        "str",
                        "repr",
                        "format",
                    }:
                        if any(isinstance(a, ast.Name) and a.id == name for a in child.args):
                            offenders.append((c_lineno, h_type, "call", c_line))
                    elif isinstance(child.func, ast.Attribute) and child.func.attr == "format":
                        if any(isinstance(a, ast.Name) and a.id == name for a in child.args):
                            offenders.append((c_lineno, h_type, "format_call", c_line))

                # 2. JoinedStr f"...{exc}..."
                elif isinstance(child, ast.JoinedStr):
                    for val in child.values:
                        if isinstance(val, ast.FormattedValue):
                            if is_safe_class_name_attr(val.value, name):
                                continue
                            for sub in ast.walk(val.value):
                                if isinstance(sub, ast.Name) and sub.id == name:
                                    offenders.append((c_lineno, h_type, "f-string", c_line))
                                    break

                # 3. Attribute exc.args, exc.details, exc.message
                elif isinstance(child, ast.Attribute) and child.attr in {
                    "args",
                    "details",
                    "message",
                }:
                    if isinstance(child.value, ast.Name) and child.value.id == name:
                        offenders.append((c_lineno, h_type, child.attr, c_line))

        return offenders

    for filename in target_files:
        src_path = cli_dir / filename
        source = src_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()

        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent

        real_offenders = find_narrow_offenders(tree, lines)
        assert real_offenders == [], (
            f"narrow except arms in {filename} must not echo exception formatting "
            f"on the MCP wire (SEC-007); offenders: {real_offenders}"
        )

    # Negative controls: assert that all 9 banned exception leak/omission forms in narrow handlers are detected
    narrow_test_snippets = [
        'try:\n    pass\nexcept ConfigurationError as exc:\n    return f"fail: {exc}"',
        'try:\n    pass\nexcept ValueError as err:\n    return f"bad: {err}"',
        "try:\n    pass\nexcept RuntimeError as e:\n    return str(e)",
        "try:\n    pass\nexcept KeyError as k:\n    return repr(k)",
        'try:\n    pass\nexcept IOError as io_err:\n    return "failed: {}".format(io_err)',
        "try:\n    pass\nexcept TypeError as t:\n    return t.args[0]",
        'try:\n    pass\nexcept PolicyValidationError as exc:\n    return json.dumps({"details": exc.details})',
        'try:\n    pass\nexcept ValueError:\n    return _rewrite_error("fail")',
        'try:\n    pass\nexcept ValueError as exc:\n    return _rewrite_error("fail")',
    ]
    for i, snippet in enumerate(narrow_test_snippets):
        dummy_tree = ast.parse(snippet)
        for p in ast.walk(dummy_tree):
            for c in ast.iter_child_nodes(p):
                c.parent = p
        dummy_lines = snippet.splitlines()
        found = find_narrow_offenders(dummy_tree, dummy_lines)
        assert len(found) > 0, f"Narrow ratchet failed to catch negative control {i}: {snippet}"


def test_all_class_a_tool_errors_do_not_leak_poison_trace_or_path(tmp_path, monkeypatch, capsys):
    """SEC-007: All 16 Class A MCP tool error arms sanitize poison exception text."""
    from tensor_grep.cli import mcp_server

    poison = r"secret_key_sk_12345 leaked in C:\private\db.py"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "marker.py").write_text("x = 1\n", encoding="utf-8")

    def _raise_poison(*_args, **_kwargs):
        raise RuntimeError(poison)

    cases = (
        (
            "tg_repo_map",
            "tensor_grep.cli.mcp_server.build_repo_map",
            lambda: mcp_server.tg_repo_map(str(tmp_path)),
        ),
        (
            "tg_context_pack",
            "tensor_grep.cli.mcp_server.build_context_pack",
            lambda: mcp_server.tg_context_pack("q", str(tmp_path)),
        ),
        (
            "tg_edit_plan",
            "tensor_grep.cli.repo_map.build_context_edit_plan",
            lambda: mcp_server.tg_edit_plan("q", str(tmp_path)),
        ),
        (
            "tg_context_render",
            "tensor_grep.cli.mcp_server.build_context_render",
            lambda: mcp_server.tg_context_render("q", str(tmp_path)),
        ),
        (
            "tg_agent_capsule",
            "tensor_grep.cli.agent_capsule.build_agent_capsule",
            lambda: mcp_server.tg_agent_capsule("q", str(tmp_path)),
        ),
        (
            "tg_session_edit_plan",
            "tensor_grep.cli.session_store.session_context_edit_plan",
            lambda: mcp_server.tg_session_edit_plan("s1", "q", path=str(tmp_path)),
        ),
        (
            "tg_session_context_render",
            "tensor_grep.cli.session_store.session_context_render",
            lambda: mcp_server.tg_session_context_render("s1", "q", path=str(tmp_path)),
        ),
        (
            "tg_session_blast_radius",
            "tensor_grep.cli.session_store.session_blast_radius",
            lambda: mcp_server.tg_session_blast_radius("s1", "sym", path=str(tmp_path)),
        ),
        (
            "tg_session_file_importers",
            "tensor_grep.cli.session_store.session_file_importers",
            lambda: mcp_server.tg_session_file_importers("s1", "marker.py", path=str(tmp_path)),
        ),
        (
            "tg_session_blast_radius_render",
            "tensor_grep.cli.session_store.session_blast_radius_render",
            lambda: mcp_server.tg_session_blast_radius_render("s1", "sym", path=str(tmp_path)),
        ),
        (
            "tg_session_blast_radius_plan",
            "tensor_grep.cli.session_store.session_blast_radius_plan",
            lambda: mcp_server.tg_session_blast_radius_plan("s1", "sym", path=str(tmp_path)),
        ),
        (
            "tg_session_open",
            "tensor_grep.cli.session_store.open_session",
            lambda: mcp_server.tg_session_open(path=str(tmp_path)),
        ),
        (
            "tg_session_list",
            "tensor_grep.cli.session_store.list_sessions",
            lambda: mcp_server.tg_session_list(path=str(tmp_path)),
        ),
        (
            "tg_session_show",
            "tensor_grep.cli.session_store.get_session",
            lambda: mcp_server.tg_session_show("s1", path=str(tmp_path)),
        ),
        (
            "tg_session_refresh",
            "tensor_grep.cli.session_store.refresh_session",
            lambda: mcp_server.tg_session_refresh("s1", path=str(tmp_path)),
        ),
        (
            "tg_session_context",
            "tensor_grep.cli.session_store.session_context",
            lambda: mcp_server.tg_session_context("s1", "q", path=str(tmp_path)),
        ),
    )

    for tool_name, patch_target, invoke in cases:
        with patch(patch_target, side_effect=_raise_poison):
            out = invoke()
        payload = json.loads(out)
        assert isinstance(payload, dict), tool_name
        assert "error" in payload, tool_name
        assert "secret_key_sk_12345" not in out, tool_name
        assert r"private\db.py" not in out and "private/db.py" not in out, tool_name
        assert "RuntimeError" in out, tool_name
        captured = capsys.readouterr()
        assert "secret_key_sk_12345" in captured.err, tool_name


def test_meta_tool_errors_do_not_leak_poison_trace_or_path(tmp_path, monkeypatch, capsys):
    """SEC-007: All 10 meta-tools sanitize internal errors and do not leak poison traces or paths."""
    from tensor_grep.cli import mcp_server

    poison = r"secret_key_sk_12345 leaked in C:\private\db.py"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "marker.py").write_text("x = 1\n", encoding="utf-8")

    def _raise_poison(*_args, **_kwargs):
        raise RuntimeError(poison)

    meta_cases = (
        (
            "tg_navigate",
            "tensor_grep.cli.mcp_server.tg_symbol_defs",
            lambda: mcp_server.tg_navigate(action="defs", symbol="foo", path=str(tmp_path)),
        ),
        (
            "tg_impact",
            "tensor_grep.cli.mcp_server.tg_symbol_impact",
            lambda: mcp_server.tg_impact(action="impact", symbol="foo", path=str(tmp_path)),
        ),
        (
            "tg_query",
            "tensor_grep.cli.mcp_server._tg_query_dispatch",
            lambda: mcp_server.tg_query(action="text", pattern="foo", path=str(tmp_path)),
        ),
        (
            "tg_context",
            "tensor_grep.cli.mcp_server.tg_context_render",
            lambda: mcp_server.tg_context(action="render", query="foo", path=str(tmp_path)),
        ),
        (
            "tg_explore",
            "tensor_grep.cli.mcp_server.tg_orient",
            lambda: mcp_server.tg_explore(action="orient", path=str(tmp_path)),
        ),
        (
            "tg_session",
            "tensor_grep.cli.mcp_server.tg_session_list",
            lambda: mcp_server.tg_session(action="list", path=str(tmp_path)),
        ),
        (
            "tg_scan",
            "tensor_grep.cli.mcp_server.tg_ruleset_scan",
            lambda: mcp_server.tg_scan(action="scan", ruleset="secrets-basic", path=str(tmp_path)),
        ),
        (
            "tg_audit",
            "tensor_grep.cli.mcp_server.tg_audit_manifest_verify",
            lambda: mcp_server.tg_audit(
                action="manifest_verify", manifest_path="marker.py", path=str(tmp_path)
            ),
        ),
        (
            "tg_checkpoint",
            "tensor_grep.cli.mcp_server.tg_checkpoint_list",
            lambda: mcp_server.tg_checkpoint(action="list", path=str(tmp_path)),
        ),
        (
            "tg_rewrite",
            "tensor_grep.cli.mcp_server.tg_rewrite_diff",
            lambda: mcp_server.tg_rewrite(
                action="diff",
                pattern="x",
                replacement="y",
                lang="python",
                path=str(tmp_path),
            ),
        ),
    )

    for tool_name, patch_target, invoke in meta_cases:
        with patch(patch_target, side_effect=_raise_poison):
            out = invoke()
        payload = json.loads(out)
        assert isinstance(payload, dict), tool_name
        assert "error" in payload, tool_name
        assert payload["error"]["code"] == "internal_error", tool_name
        assert "secret_key_sk_12345" not in out, tool_name
        assert r"private\db.py" not in out and "private/db.py" not in out, tool_name
        assert "RuntimeError" in out, tool_name
        captured = capsys.readouterr()
        assert "secret_key_sk_12345" in captured.err, tool_name


def test_path_confinement_never_echoes_absolute_external_path(tmp_path, capsys):
    """SEC-007: PathConfinementError keeps a constant public message; paths go to stderr."""
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()

    with pytest.raises(mcp_server.PathConfinementError) as raised:
        mcp_server._confine_read_path("../../secret", anchor, label="path")

    message = str(raised.value)
    assert "must stay within the MCP root (refused)" in message
    assert str(outside.resolve()) not in message
    assert str(anchor.resolve()) not in message
    # Relative escape form also must not appear as a resolved absolute path dump.
    assert "escapes" not in message

    captured = capsys.readouterr()
    assert "path confinement refusal" in captured.err
    assert "escapes" in captured.err


def test_write_path_confinement_resolution_failure_sanitizes_and_logs(tmp_path, capsys):
    """SEC-007: _confine_write_path wraps resolve() and refuses without leaking paths/errors."""
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()

    with patch("pathlib.Path.resolve", side_effect=RuntimeError("secret_resolution_fail")):
        with pytest.raises(mcp_server.PathConfinementError) as raised:
            mcp_server._confine_write_path("output.txt", anchor, label="output_path")

    message = str(raised.value)
    assert "secret_resolution_fail" not in message
    assert "must stay within the MCP root (refused)" in message

    captured = capsys.readouterr()
    assert "path confinement resolution failure" in captured.err
    assert "secret_resolution_fail" in captured.err


def test_mcp_wire_str_exc_closed_world_ast_ratchet():
    """SEC-007: Closed-world ratchet enforcing that across all 3 MCP tool modules:
    - src/tensor_grep/cli/mcp_server.py (26 sites)
    - src/tensor_grep/cli/mcp_symbol_tools.py (11 sites)
    - src/tensor_grep/cli/mcp_audit_tools.py (15 sites)
    - src/tensor_grep/cli/mcp_rewrite_tools.py (2 sites)
    exactly 54 authorized str(exc) callsites exist, and ALL 54 are PathConfinementError sites
    (53 tool handlers + 1 _meta_confinement_error helper).
    Zero un-allowlisted sites permitted, verified by exact function identity and handler type,
    matching the enclosing handler's bound exception variable name regardless of spelling.
    """
    import ast
    from collections import Counter
    from pathlib import Path

    cli_dir = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli"

    authorized_str_exc_sites: dict[str, dict[tuple[str, str | None], int]] = {
        "mcp_server.py": {
            ("_meta_confinement_error", None): 1,
            ("tg_repo_map", "PathConfinementError"): 1,
            ("tg_orient", "PathConfinementError"): 1,
            ("tg_doctor", "PathConfinementError"): 2,
            ("tg_context_pack", "PathConfinementError"): 1,
            ("tg_edit_plan", "PathConfinementError"): 1,
            ("tg_context_render", "PathConfinementError"): 1,
            ("tg_agent_capsule", "PathConfinementError"): 1,
            ("tg_session_edit_plan", "PathConfinementError"): 1,
            ("tg_session_context_render", "PathConfinementError"): 1,
            ("tg_session_blast_radius", "PathConfinementError"): 1,
            ("tg_session_file_importers", "PathConfinementError"): 2,
            ("tg_session_blast_radius_render", "PathConfinementError"): 1,
            ("tg_session_blast_radius_plan", "PathConfinementError"): 1,
            ("tg_find", "PathConfinementError"): 1,
            ("tg_search", "PathConfinementError"): 2,
            ("tg_ast_search", "PathConfinementError"): 1,
            ("tg_classify_logs", "PathConfinementError"): 1,
            ("tg_session_open", "PathConfinementError"): 1,
            ("tg_session_list", "PathConfinementError"): 1,
            ("tg_session_show", "PathConfinementError"): 1,
            ("tg_session_refresh", "PathConfinementError"): 1,
            ("tg_session_context", "PathConfinementError"): 1,
        },
        "mcp_symbol_tools.py": {
            ("tg_symbol_defs", "PathConfinementError"): 1,
            ("tg_symbol_source", "PathConfinementError"): 1,
            ("tg_symbol_impact", "PathConfinementError"): 1,
            ("tg_symbol_refs", "PathConfinementError"): 1,
            ("tg_symbol_callers", "PathConfinementError"): 1,
            ("tg_symbol_blast_radius", "PathConfinementError"): 1,
            ("tg_symbol_blast_radius_render", "PathConfinementError"): 1,
            ("tg_symbol_blast_radius_plan", "PathConfinementError"): 1,
            ("tg_file_imports", "PathConfinementError"): 1,
            ("tg_file_importers", "PathConfinementError"): 2,
        },
        "mcp_audit_tools.py": {
            ("tg_ruleset_scan", "PathConfinementError"): 2,
            ("tg_index_search", "PathConfinementError"): 1,
            ("tg_rewrite_plan", "PathConfinementError"): 1,
            ("tg_rewrite_apply", "PathConfinementError"): 1,
            ("tg_audit_manifest_verify", "PathConfinementError"): 1,
            ("tg_audit_history", "PathConfinementError"): 1,
            ("tg_audit_diff", "PathConfinementError"): 1,
            ("tg_review_bundle_create", "PathConfinementError"): 2,
            ("tg_review_bundle_verify", "PathConfinementError"): 1,
            ("tg_checkpoint_create", "PathConfinementError"): 1,
            ("tg_checkpoint_list", "PathConfinementError"): 1,
            ("tg_checkpoint_undo", "PathConfinementError"): 1,
            ("tg_rewrite_diff", "PathConfinementError"): 1,
        },
        "mcp_rewrite_tools.py": {
            ("execute_rewrite_apply_json", "PathConfinementError"): 2,
        },
    }

    def _collect_str_exc_sites(ast_tree, source_lines):
        sites: list[tuple[int, tuple[str, str | None], str]] = []
        counts: Counter[tuple[str, str | None]] = Counter()

        for node in ast.walk(ast_tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "str"):
                continue
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
                continue

            curr = getattr(node, "parent", None)
            handler = None
            func_def = None
            while curr:
                if isinstance(curr, ast.ExceptHandler) and handler is None:
                    handler = curr
                if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef)) and func_def is None:
                    func_def = curr
                curr = getattr(curr, "parent", None)

            if handler is not None:
                if not handler.name or node.args[0].id != handler.name:
                    continue
            elif func_def and func_def.name == "_meta_confinement_error":
                if node.args[0].id != "exc":
                    continue
            else:
                continue

            lineno = node.lineno
            line = source_lines[lineno - 1].strip() if source_lines else ""
            fn_name = func_def.name if func_def else "<module>"
            h_type = handler.type.id if handler and isinstance(handler.type, ast.Name) else None

            key = (fn_name, h_type)
            sites.append((lineno, key, line))
            counts[key] += 1

        return sites, counts

    total_sites_count = 0
    for mod_name, expected_sites in authorized_str_exc_sites.items():
        src_path = cli_dir / mod_name
        source = src_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()

        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child.parent = parent

        actual_sites, actual_counts = _collect_str_exc_sites(tree, lines)
        total_sites_count += len(actual_sites)

        unauthorized = [site for site in actual_sites if site[1] not in expected_sites]
        assert unauthorized == [], (
            f"Found unauthorized str(exc) sites in {mod_name}: {unauthorized}"
        )
        assert actual_counts == expected_sites, (
            f"Counts mismatch in {mod_name}: {actual_counts} vs {expected_sites}"
        )
    assert total_sites_count == 54, (
        f"Expected exactly 54 closed-world str(exc) sites across all 4 modules, found {total_sites_count}"
    )

    # Negative control: assert that mutation with a different exception variable name (e.g. 'err') is caught
    mutation_source = (
        "def bad_tool():\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as err:\n"
        "        return str(err)\n"
    )
    mutation_tree = ast.parse(mutation_source)
    for p in ast.walk(mutation_tree):
        for c in ast.iter_child_nodes(p):
            c.parent = p
    mutation_sites, _ = _collect_str_exc_sites(mutation_tree, mutation_source.splitlines())
    assert len(mutation_sites) == 1, f"Expected 1 unauthorized mutation site, got {mutation_sites}"
    assert mutation_sites[0][1] == ("bad_tool", "ValueError")


def test_class_b_narrow_handlers_do_not_leak_poison_trace_or_path(tmp_path, monkeypatch, capsys):
    """SEC-007: All 5 Class B narrow exception handler categories sanitize error responses
    and do not leak poison strings, absolute paths, or internal tracebacks on the wire.
    """
    from tensor_grep.cli import mcp_server
    from tensor_grep.cli.session_store import SessionStaleError

    poison = r"secret_key_sk_12345 leaked in C:\private\db.py"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "marker.py").write_text("x = 1\n", encoding="utf-8")

    # 1. SessionStaleError across session tools
    with patch(
        "tensor_grep.cli.session_store.session_context", side_effect=SessionStaleError(poison)
    ):
        out = mcp_server.tg_session_context("s1", "query", path=str(tmp_path))
        assert poison not in out
        assert "Session cache is stale" in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "invalid_input"
        captured = capsys.readouterr()
        assert poison in captured.err

    # 2. FileNotFoundError in tg_session_file_importers
    with patch(
        "tensor_grep.cli.session_store.session_file_importers",
        side_effect=FileNotFoundError(poison),
    ):
        out = mcp_server.tg_session_file_importers("s1", "marker.py", path=str(tmp_path))
        assert poison not in out
        assert "File not found" in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "invalid_input"
        captured = capsys.readouterr()
        assert poison in captured.err

    # 3. ValueError in tg_orient
    with patch(
        "tensor_grep.cli.mcp_server.build_orient_capsule_json", side_effect=ValueError(poison)
    ):
        out = mcp_server.tg_orient(path=str(tmp_path))
        assert poison not in out
        assert "Invalid orient parameter" in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "invalid_input"
        captured = capsys.readouterr()
        assert poison in captured.err

    # 4. ValueError in tg_agent_capsule
    with patch("tensor_grep.cli.agent_capsule.build_agent_capsule", side_effect=ValueError(poison)):
        out = mcp_server.tg_agent_capsule("query", path=str(tmp_path))
        assert poison not in out
        assert "Invalid input parameter for tg_agent_capsule" in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "invalid_input"
        captured = capsys.readouterr()
        assert poison in captured.err

    # 5. FileNotFoundError in tg_find
    with patch("tensor_grep.cli.mcp_server._execute_find", side_effect=FileNotFoundError(poison)):
        out = mcp_server.tg_find("query", path=str(tmp_path))
        assert poison not in out
        assert "Path not found" in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "invalid_input"
        captured = capsys.readouterr()
        assert poison in captured.err


def test_class_c_backend_errors_do_not_leak_poison_trace_or_path(tmp_path, monkeypatch, capsys):
    """SEC-007: Class C BackendExecutionError handlers in tg_find and tg_search preserve
    distinguishable error codes while sanitizing poison exception messages on the wire.
    """
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    poison = r"secret_key_sk_12345 leaked in C:\private\model"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    # 1. tg_find
    with patch(
        "tensor_grep.cli.mcp_server._execute_find", side_effect=BackendExecutionError(poison)
    ):
        out = mcp_server.tg_find("query", path=str(tmp_path))
        assert poison not in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "find_backend_error"
        assert payload["error"]["retryable"] is False
        assert "BackendExecutionError" in payload["error"]["message"]
        captured = capsys.readouterr()
        assert poison in captured.err

    # 2. tg_search (structured JSON)
    from tensor_grep.core.result import MatchLine

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="query here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch(
            "tensor_grep.cli.mcp_server._apply_semantic_rerank",
            side_effect=BackendExecutionError(poison),
        ),
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("query", str(tmp_path), semantic=True, structured_json=True)
        assert poison not in out
        payload = json.loads(out)
        assert payload["error"]["code"] == "semantic_backend_error"
        assert payload["error"]["retryable"] is False
        assert "BackendExecutionError" in payload["error"]["message"]
        captured = capsys.readouterr()
        assert poison in captured.err

        # 3. tg_search (unstructured plain text)
        out_text = mcp_server.tg_search(
            "query", str(tmp_path), semantic=True, structured_json=False
        )
        assert poison not in out_text
        assert "semantic backend error (BackendExecutionError)" in out_text
        captured = capsys.readouterr()
        assert poison in captured.err


def test_tg_ast_search_configuration_error_does_not_leak_poison(tmp_path, capsys, monkeypatch):
    """SEC-007: tg_ast_search ConfigurationError returns sanitized 'unavailable' message
    without leaking poison paths or tokens on wire, while logging to stderr.
    """
    from tensor_grep.cli import mcp_server
    from tensor_grep.core.pipeline import ConfigurationError

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "marker.py").write_text("x = 1\n", encoding="utf-8")
    poison = r"SEC007_SECRET at C:\private\model\config.yaml"

    with patch("tensor_grep.cli.mcp_server.Pipeline", side_effect=ConfigurationError(poison)):
        # 1. Structured JSON
        out_json = mcp_server.tg_ast_search("foo", "python", str(tmp_path), structured_json=True)
        assert poison not in out_json
        payload = json.loads(out_json)
        assert payload["error"]["code"] == "unavailable"
        assert "AstBackend is not available on this system" in payload["error"]["message"]
        assert poison not in payload["error"]["message"]

        # 2. Plain text
        out_text = mcp_server.tg_ast_search("foo", "python", str(tmp_path), structured_json=False)
        assert poison not in out_text
        assert "AstBackend is not available on this system" in out_text

    # 3. Server stderr diagnostic
    captured = capsys.readouterr()
    assert poison in captured.err


def test_confinement_refusal_envelope_never_contains_external_path(tmp_path, monkeypatch, capsys):
    """SEC-007: All 39 path confinement entrypoints redact candidate paths to '[refused]'
    in wire error envelopes and never leak candidate external paths to callers.
    """
    from tensor_grep.cli import mcp_server

    root = tmp_path / "mcp_root"
    root.mkdir()
    outside = tmp_path / "secret_poison_dir"
    outside.mkdir()
    poison_path = str(outside / "secret_file.py")
    poison_token = "secret_poison_dir"
    poison_file = str(outside / "secret_file_2.py")
    poison_file_token = "secret_file_2.py"

    monkeypatch.setenv("TG_MCP_ROOT", str(root))

    cases = (
        ("tg_repo_map", lambda: mcp_server.tg_repo_map(poison_path)),
        ("tg_orient", lambda: mcp_server.tg_orient(poison_path)),
        ("tg_doctor_path", lambda: mcp_server.tg_doctor(poison_path)),
        ("tg_doctor_config", lambda: mcp_server.tg_doctor(str(root), config=poison_path)),
        ("tg_context_pack", lambda: mcp_server.tg_context_pack("q", poison_path)),
        ("tg_edit_plan", lambda: mcp_server.tg_edit_plan("q", poison_path)),
        ("tg_context_render", lambda: mcp_server.tg_context_render("q", poison_path)),
        ("tg_agent_capsule", lambda: mcp_server.tg_agent_capsule("q", poison_path)),
        (
            "tg_session_edit_plan",
            lambda: mcp_server.tg_session_edit_plan("s1", "q", path=poison_path),
        ),
        (
            "tg_session_context_render",
            lambda: mcp_server.tg_session_context_render("s1", "q", path=poison_path),
        ),
        (
            "tg_session_blast_radius",
            lambda: mcp_server.tg_session_blast_radius("s1", "sym", path=poison_path),
        ),
        (
            "tg_session_file_importers_path",
            lambda: mcp_server.tg_session_file_importers("s1", "marker.py", path=poison_path),
        ),
        (
            "tg_session_file_importers_file",
            lambda: mcp_server.tg_session_file_importers("s1", poison_file, path=str(root)),
        ),
        (
            "tg_session_file_importers_both",
            lambda: mcp_server.tg_session_file_importers("s1", poison_file, path=poison_path),
        ),
        (
            "tg_session_blast_radius_render",
            lambda: mcp_server.tg_session_blast_radius_render("s1", "sym", path=poison_path),
        ),
        (
            "tg_session_blast_radius_plan",
            lambda: mcp_server.tg_session_blast_radius_plan("s1", "sym", path=poison_path),
        ),
        ("tg_find", lambda: mcp_server.tg_find("q", poison_path)),
        ("tg_search", lambda: mcp_server.tg_search("p", poison_path)),
        ("tg_ast_search", lambda: mcp_server.tg_ast_search("p", "python", poison_path)),
        ("tg_classify_logs", lambda: mcp_server.tg_classify_logs(poison_path)),
        ("tg_session_open", lambda: mcp_server.tg_session_open(path=poison_path)),
        ("tg_session_list", lambda: mcp_server.tg_session_list(path=poison_path)),
        ("tg_session_show", lambda: mcp_server.tg_session_show("s1", path=poison_path)),
        ("tg_session_refresh", lambda: mcp_server.tg_session_refresh("s1", path=poison_path)),
        ("tg_session_context", lambda: mcp_server.tg_session_context("s1", "q", path=poison_path)),
        (
            "tg_navigate",
            lambda: mcp_server.tg_navigate(action="defs", symbol="foo", path=poison_path),
        ),
        (
            "tg_impact",
            lambda: mcp_server.tg_impact(action="impact", symbol="foo", path=poison_path),
        ),
        (
            "tg_query",
            lambda: mcp_server.tg_query(action="text", pattern="foo", path=poison_path),
        ),
        (
            "tg_context",
            lambda: mcp_server.tg_context(action="render", query="foo", path=poison_path),
        ),
        ("tg_explore", lambda: mcp_server.tg_explore(action="orient", path=poison_path)),
        ("tg_session", lambda: mcp_server.tg_session(action="list", path=poison_path)),
        (
            "tg_scan",
            lambda: mcp_server.tg_scan(action="scan", ruleset="secrets-basic", path=poison_path),
        ),
        (
            "tg_audit",
            lambda: mcp_server.tg_audit(
                action="manifest_verify", manifest_path=poison_path, path=str(root)
            ),
        ),
        ("tg_checkpoint", lambda: mcp_server.tg_checkpoint(action="list", path=poison_path)),
        (
            "tg_rewrite",
            lambda: mcp_server.tg_rewrite(
                action="diff", pattern="x", replacement="y", lang="python", path=poison_path
            ),
        ),
        ("tg_file_imports", lambda: mcp_server.tg_file_imports(poison_file)),
        (
            "tg_file_importers_path",
            lambda: mcp_server.tg_file_importers("marker.py", path=poison_path),
        ),
        (
            "tg_file_importers_file",
            lambda: mcp_server.tg_file_importers(poison_file, path=str(root)),
        ),
        (
            "tg_file_importers_both",
            lambda: mcp_server.tg_file_importers(poison_file, path=poison_path),
        ),
    )

    for name, invoke in cases:
        out = invoke()
        assert poison_token not in out, f"Poison token leaked in {name}: {out}"
        assert poison_file_token not in out, f"Poison file token leaked in {name}: {out}"
        payload = json.loads(out)
        assert payload.get("error", {}).get("code") == "invalid_input", (
            f"Wrong error code in {name}: {payload}"
        )
        if name == "tg_doctor_config":
            assert payload["config"] == "[refused]", f"Config not refused in {name}: {payload}"
        elif name in {
            "tg_session_file_importers_file",
            "tg_session_file_importers_both",
            "tg_file_importers_file",
            "tg_file_importers_both",
        }:
            assert payload["file"] == "[refused]", f"File not refused in {name}: {payload}"
            if name in {"tg_session_file_importers_both", "tg_file_importers_both"}:
                assert payload["path"] == "[refused]", f"Path not refused in {name}: {payload}"
                if name == "tg_session_file_importers_both":
                    assert payload["error"]["detail"]["file"] == "[refused]"
        elif name == "tg_file_importers_path":
            assert payload["path"] == "[refused]", f"Path not refused in {name}: {payload}"
            assert payload["file"] == "[refused]", f"File not refused in {name}: {payload}"
        elif name == "tg_file_imports":
            assert payload["file"] == "[refused]", f"File not refused in {name}: {payload}"
        elif name == "tg_classify_logs":
            assert payload["file_path"] == "[refused]", (
                f"File path not refused in {name}: {payload}"
            )
        elif "path" in payload:
            assert payload["path"] == "[refused]", f"Path not refused in {name}: {payload['path']}"

    captured = capsys.readouterr()
    assert "path confinement refusal" in captured.err


def test_direct_call_tool_broad_poison_sanitized(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool handles broad exceptions without leaking poison on wire."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_BROAD_SECRET at C:\private\trace.py"
    with patch.object(mcp_server, "build_symbol_defs", side_effect=RuntimeError(poison)):
        content, _data = asyncio.run(
            mcp_server.mcp.call_tool("tg_symbol_defs", {"symbol": "foo", "path": "."})
        )
        text = content[0].text
        assert poison not in text
        assert "RuntimeError" in text
        payload = json.loads(text)
        assert payload["error"]["code"] == "internal_error"
        captured = capsys.readouterr()
        assert poison in captured.err


def test_direct_call_tool_narrow_poison_sanitized(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool handles narrow exceptions without leaking poison on wire."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_NARROW_SECRET at C:\private\missing.py"
    with patch.object(mcp_server, "build_file_importers", side_effect=FileNotFoundError(poison)):
        content, _data = asyncio.run(
            mcp_server.mcp.call_tool(
                "tg_file_importers",
                {"file": "src/tensor_grep/cli/mcp_server.py", "path": "."},
            )
        )
        text = content[0].text
        assert poison not in text
        payload = json.loads(text)
        assert payload["error"]["code"] == "invalid_input"
        assert (
            "File not found" in payload["error"]["message"]
            or "Path not found" in payload["error"]["message"]
        )
        captured = capsys.readouterr()
        assert poison in captured.err


def test_direct_call_tool_cwd_failure_sanitized(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool handles Path.cwd() failure without escaping as ToolError."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_CWD_SECRET"
    with patch("pathlib.Path.cwd", side_effect=RuntimeError(poison)):
        content, _data = asyncio.run(
            mcp_server.mcp.call_tool("tg_classify_logs", {"file_path": "a.log"})
        )
        text = content[0].text
        assert poison not in text
        payload = json.loads(text)
        assert payload["error"]["code"] == "invalid_input"
        assert payload["file_path"] == "[refused]"
        captured = capsys.readouterr()
        assert "root resolution failure for root" in captured.err
        assert poison in captured.err


def test_direct_call_tool_external_path_redacted(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool redacts external paths to [refused] on the wire."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison_file = r"C:\outside\secret.py"
    content, _data = asyncio.run(mcp_server.mcp.call_tool("tg_file_imports", {"file": poison_file}))
    text = content[0].text
    assert "outside" not in text
    assert "secret.py" not in text
    payload = json.loads(text)
    assert payload["error"]["code"] == "invalid_input"
    assert payload["file"] == "[refused]"
    captured = capsys.readouterr()
    assert "path confinement refusal" in captured.err


def test_direct_call_tool_session_open_get_session_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_session_open sanitizes get_session exception on wire."""
    import asyncio

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.py").write_text("def f(): pass\n", encoding="utf-8")

    poison = r"SEC007_GET_SESSION_POISON_SECRET"
    with patch("tensor_grep.cli.session_store.get_session", side_effect=RuntimeError(poison)):
        content, _data = asyncio.run(
            mcp_server.mcp.call_tool("tg_session_open", {"path": str(tmp_path)})
        )
        text = content[0].text
        assert poison not in text
        payload = json.loads(text)
        assert "tracked_file_count_error" in payload
        assert "RuntimeError" in payload["tracked_file_count_error"]
        assert poison not in payload["tracked_file_count_error"]
        captured = capsys.readouterr()
        assert poison in captured.err


def test_direct_call_tool_rewrite_diff_subprocess_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_diff handles subprocess failure without leaking poison."""
    import asyncio
    from pathlib import Path

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    token = "SEC007_DIFF_SUBPROCESS_SECRET"
    poison = OSError(token + r" at C:\private\runner.exe")
    with (
        patch.object(
            mcp_server, "_resolve_native_tg_binary_for_mcp", return_value=(Path("tg"), None)
        ),
        patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("tg")),
        patch.object(mcp_server, "_run_rewrite_subprocess", side_effect=poison),
    ):
        content, data = asyncio.run(
            mcp_server.mcp.call_tool(
                "tg_rewrite_diff",
                {"pattern": "$A", "replacement": "$A", "lang": "python", "path": str(tmp_path)},
            )
        )
        text = content[0].text
        assert token not in text
        data_text = json.dumps(data, default=str)
        assert token not in data_text
        payload = json.loads(text)
        assert payload["error"]["code"] == "execution_failed"
        assert "OSError" in payload["error"]["message"]
        captured = capsys.readouterr()
        assert token in captured.err


def test_direct_call_tool_rewrite_plan_embedded_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_plan handles embedded rewrite failure without leaking poison."""
    import asyncio
    import sys
    import types
    from unittest.mock import MagicMock

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    token = "SEC007_REWRITE_PLAN_SECRET"
    poison = RuntimeError(token + r" at C:\private\native_model.bin")

    fake_rust = types.ModuleType("tensor_grep.rust_core")
    fake_rust.ast_rewrite_plan_json = MagicMock(side_effect=poison)
    fake_rust.ast_rewrite_apply_json = MagicMock(side_effect=poison)
    monkeypatch.setitem(sys.modules, "tensor_grep.rust_core", fake_rust)

    with patch.object(mcp_server, "_resolve_native_tg_binary_for_mcp", return_value=(None, None)):
        content, data = asyncio.run(
            mcp_server.mcp.call_tool(
                "tg_rewrite_plan",
                {"pattern": "$A", "replacement": "$A", "lang": "python", "path": str(tmp_path)},
            )
        )
        text = content[0].text
        assert token not in text
        data_text = json.dumps(data, default=str)
        assert token not in data_text
        payload = json.loads(text)
        assert "RuntimeError" in payload["error"]["message"]
        captured = capsys.readouterr()
        assert token in captured.err


def test_direct_call_tool_rewrite_diff_native_stderr_poison_sanitized(
    tmp_path, monkeypatch, capsys
):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_diff sanitizes native stderr on wire."""
    import asyncio
    from pathlib import Path
    from subprocess import CompletedProcess

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    token = "SEC007_NATIVE_STDERR_SECRET"
    completed = CompletedProcess(
        args=["tg.exe"],
        returncode=2,
        stdout="",
        stderr=f"error: invalid pattern with secret {token} at C:\\private\\db.rs",
    )
    with (
        patch.object(
            mcp_server, "_resolve_native_tg_binary_for_mcp", return_value=(Path("tg"), None)
        ),
        patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("tg")),
        patch.object(mcp_server, "_run_rewrite_subprocess", return_value=completed),
    ):
        content, data = asyncio.run(
            mcp_server.mcp.call_tool(
                "tg_rewrite_diff",
                {"pattern": "$A", "replacement": "$A", "lang": "python", "path": str(tmp_path)},
            )
        )
        text = content[0].text
        assert token not in text
        data_text = json.dumps(data, default=str)
        assert token not in data_text
        payload = json.loads(text)
        assert payload["error"]["code"] == "pattern_error"
        assert token not in payload["error"]["message"]
        captured = capsys.readouterr()
        assert token in captured.err


def test_direct_call_tool_index_search_subprocess_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_index_search handles subprocess failure without leaking poison."""
    import asyncio
    from pathlib import Path

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    token = "SEC007_INDEX_SEARCH_SUBPROCESS_SECRET"
    poison = OSError(token + r" at C:\private\runner.exe")
    with (
        patch.object(
            mcp_server, "_resolve_native_tg_binary_for_mcp", return_value=(Path("tg"), None)
        ),
        patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("tg")),
        patch.object(mcp_server, "_run_rewrite_subprocess", side_effect=poison),
    ):
        content, data = asyncio.run(
            mcp_server.mcp.call_tool(
                "tg_index_search",
                {"pattern": "def foo", "path": str(tmp_path)},
            )
        )
        text = content[0].text
        assert token not in text
        data_text = json.dumps(data, default=str)
        assert token not in data_text
        payload = json.loads(text)
        assert payload["error"]["code"] == "execution_failed"
        assert "OSError" in payload["error"]["message"]
        captured = capsys.readouterr()
        assert token in captured.err


def test_all_mcp_registered_tools_have_outer_fail_closed_boundary():
    """SEC-007: Every registered MCP tool has a top-level try/except Exception boundary."""
    import ast
    from pathlib import Path

    cli_dir = Path("src/tensor_grep/cli")
    target_files = [
        "mcp_server.py",
        "mcp_symbol_tools.py",
        "mcp_audit_tools.py",
        "mcp_rewrite_tools.py",
    ]

    def _is_broad_handler(h: ast.ExceptHandler) -> bool:
        if h.type is None:
            return True
        if isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"):
            return True
        if isinstance(h.type, ast.Tuple):
            return any(
                isinstance(e, ast.Name) and e.id in ("Exception", "BaseException")
                for e in h.type.elts
            )
        return False

    def _check_encompassing_boundary(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        docstring = (
            bool(fn_node.body)
            and isinstance(fn_node.body[0], ast.Expr)
            and isinstance(fn_node.body[0].value, ast.Constant)
            and isinstance(fn_node.body[0].value.value, str)
        )
        stmts = fn_node.body[1:] if docstring else fn_node.body
        if len(stmts) != 1 or not isinstance(stmts[0], ast.Try):
            return False
        try_node = stmts[0]
        broad_handlers = [h for h in try_node.handlers if _is_broad_handler(h)]
        if not broad_handlers:
            return False

        allowed_sinks = {
            "_log_tool_exception",
            "_sanitized_tool_error",
            "_sanitized_tool_error_text",
            "_ruleset_scan_error",
            "_index_search_error",
            "_rewrite_error",
            "_classify_native_rewrite_failure",
            "_safe_exception_class_name",
        }
        protected_names = allowed_sinks | {"_log_tool_exception"}

        for h in broad_handlers:
            # Rule 1: Find direct top-level _log_tool_exception call statement
            log_indices = [
                i
                for i, s in enumerate(h.body)
                if isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Call)
                and isinstance(s.value.func, ast.Name)
                and s.value.func.id == "_log_tool_exception"
            ]
            if not log_indices:
                return False
            first_log_idx = log_indices[0]

            # Prior to first_log_idx, RECURSIVELY check for any control-transfer statements
            for s in h.body[:first_log_idx]:
                for subnode in ast.walk(s):
                    if isinstance(subnode, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                        return False

            # Rule 2: Handler must terminate with a Return statement
            if not (h.body and isinstance(h.body[-1], ast.Return)):
                return False

            # Rule 3: Forbid any 'raise' anywhere inside the handler
            for n in ast.walk(h):
                if isinstance(n, ast.Raise):
                    return False

            # Rule 4: Reject any shadowing/rebinding of allowed sinks or logger names
            shadowed_names = set()
            for n in ast.walk(h):
                if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                    if n.id in protected_names:
                        return False
                    shadowed_names.add(n.id)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if n.name in protected_names:
                        return False
                    shadowed_names.add(n.name)

            # Rule 5: Comprehensive taint analysis
            tainted = set()
            if h.name:
                tainted.add(h.name)

            # Fixed-point taint propagation across assignments, walrus expressions, and destructuring
            for _ in range(5):
                for n in ast.walk(h):
                    if isinstance(n, ast.NamedExpr):
                        if any(
                            isinstance(sub, ast.Name) and sub.id in tainted
                            for sub in ast.walk(n.value)
                        ):
                            if isinstance(n.target, ast.Name):
                                tainted.add(n.target.id)
                    elif isinstance(n, ast.Assign):
                        if any(
                            isinstance(sub, ast.Name) and sub.id in tainted
                            for sub in ast.walk(n.value)
                        ):
                            for t in n.targets:
                                for tn in ast.walk(t):
                                    if isinstance(tn, ast.Name):
                                        tainted.add(tn.id)
                    elif isinstance(n, ast.AnnAssign) and n.value:
                        if any(
                            isinstance(sub, ast.Name) and sub.id in tainted
                            for sub in ast.walk(n.value)
                        ):
                            for tn in ast.walk(n.target):
                                if isinstance(tn, ast.Name):
                                    tainted.add(tn.id)

            # Ensure parent pointers are populated
            for parent in ast.walk(h):
                for child in ast.iter_child_nodes(parent):
                    child.parent = parent

            # Reject-by-default for any tainted load (fail-closed)
            for sub in ast.walk(h):
                if isinstance(sub, ast.Name) and sub.id in tainted:
                    if isinstance(sub.ctx, (ast.Store, ast.Del)):
                        continue
                    p = getattr(sub, "parent", None)
                    if p is None:
                        return False

                    # Safe sink call: sub must be an argument to a direct unshadowed ast.Name call in allowed_sinks
                    if isinstance(p, ast.Call):
                        if (
                            isinstance(p.func, ast.Name)
                            and p.func.id in allowed_sinks
                            and p.func.id not in shadowed_names
                        ):
                            continue
                        return False

                    # Any other parent is REJECTED
                    return False

        return True

    missing_boundary: list[tuple[str, str]] = []
    total_tools = 0
    for filename in target_files:
        src = (cli_dir / filename).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_tool = any(
                    (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr == "tool"
                    )
                    or (isinstance(d, ast.Name) and d.id == "_register_legacy_tool")
                    for d in node.decorator_list
                )
                if is_tool:
                    total_tools += 1
                    if not _check_encompassing_boundary(node):
                        missing_boundary.append((filename, node.name))

    assert total_tools >= 58, f"Expected at least 58 MCP tools, found {total_tools}"
    assert missing_boundary == [], (
        f"Tools missing outer encompassing exception boundaries: {missing_boundary}"
    )

    # Negative controls / mutation controls:
    # 1. Tool lacking try/except entirely
    t1 = ast.parse("@mcp.tool()\ndef t1():\n    return 'no try'\n").body[0]
    assert isinstance(t1, ast.FunctionDef)
    assert not _check_encompassing_boundary(t1), "Mutation control: lacking try must fail"

    # 2. Tool with executable statement BEFORE broad try
    t2 = ast.parse(
        "@mcp.tool()\ndef t2(p='.'):\n    p = _confine(p)\n    try:\n        return p\n    except Exception as exc:\n        _log_tool_exception('t2', exc)\n        return _sanitized_tool_error_text('t2', exc)\n"
    ).body[0]
    assert isinstance(t2, ast.FunctionDef)
    assert not _check_encompassing_boundary(t2), "Mutation control: code before try must fail"

    # 3. Tool with executable statement AFTER broad try
    t3 = ast.parse(
        "@mcp.tool()\ndef t3():\n    try:\n        res = 'ok'\n    except Exception as exc:\n        _log_tool_exception('t3', exc)\n        res = _sanitized_tool_error_text('t3', exc)\n    return res\n"
    ).body[0]
    assert isinstance(t3, ast.FunctionDef)
    assert not _check_encompassing_boundary(t3), "Mutation control: code after try must fail"

    # 4. Tool with try but lacking broad exception handler
    t4 = ast.parse(
        "@mcp.tool()\ndef t4():\n    try:\n        return 'ok'\n    except ValueError:\n        return 'val'\n"
    ).body[0]
    assert isinstance(t4, ast.FunctionDef)
    assert not _check_encompassing_boundary(t4), "Mutation control: narrow handler only must fail"

    # 5. Async tool lacking boundary
    t5 = ast.parse("@mcp.tool()\nasync def t5():\n    return 'async no try'\n").body[0]
    assert isinstance(t5, ast.AsyncFunctionDef)
    assert not _check_encompassing_boundary(t5), (
        "Mutation control: async function lacking boundary must fail"
    )

    # 6. Mutation control: missing _log_tool_exception in broad handler
    t6 = ast.parse(
        "@mcp.tool()\ndef t6():\n    try:\n        return 'ok'\n    except Exception as exc:\n        return 'fail'\n"
    ).body[0]
    assert isinstance(t6, ast.FunctionDef)
    assert not _check_encompassing_boundary(t6), "Mutation control: missing log must fail"

    # 7. Mutation control: direct return exc
    t7 = ast.parse(
        "@mcp.tool()\ndef t7():\n    try:\n        return 'ok'\n    except Exception as exc:\n        _log_tool_exception('t7', exc)\n        return exc\n"
    ).body[0]
    assert isinstance(t7, ast.FunctionDef)
    assert not _check_encompassing_boundary(t7), "Mutation control: return exc must fail"

    # 8. Mutation control: leaking exc via json.dumps({'error': exc})
    t8 = ast.parse(
        "@mcp.tool()\ndef t8():\n    try:\n        return 'ok'\n    except Exception as exc:\n        _log_tool_exception('t8', exc)\n        return json.dumps({'error': exc})\n"
    ).body[0]
    assert isinstance(t8, ast.FunctionDef)
    assert not _check_encompassing_boundary(t8), "Mutation control: json.dumps error dict must fail"

    # 9. Mutation control: taint propagated via alias assignment and return
    t9 = ast.parse(
        "@mcp.tool()\ndef t9():\n    try:\n        return 'ok'\n    except Exception as exc:\n        _log_tool_exception('t9', exc)\n        leaked = exc\n        return leaked\n"
    ).body[0]
    assert isinstance(t9, ast.FunctionDef)
    assert not _check_encompassing_boundary(t9), (
        "Mutation control: taint propagated alias return must fail"
    )

    # 10. Mutation control: arbitrary attribute access on exc (exc.__dict__)
    t10 = ast.parse(
        "@mcp.tool()\ndef t10():\n    try:\n        return 'ok'\n    except Exception as exc:\n        _log_tool_exception('t10', exc)\n        return str(exc.__dict__)\n"
    ).body[0]
    assert isinstance(t10, ast.FunctionDef)
    assert not _check_encompassing_boundary(t10), (
        "Mutation control: arbitrary attribute access on exc must fail"
    )

    # 11. Mutation control: unreachable logging inside if False
    t11 = ast.parse(
        "@mcp.tool()\ndef t11():\n    try:\n        return 'ok'\n    except Exception as exc:\n        if False:\n            _log_tool_exception('t11', exc)\n        return 'error'\n"
    ).body[0]
    assert isinstance(t11, ast.FunctionDef)
    assert not _check_encompassing_boundary(t11), (
        "Mutation control: unreachable logging in handler must fail"
    )

    # 12. Mutation control: handler missing terminal return
    t12 = ast.parse(
        "@mcp.tool()\ndef t12():\n    try:\n        return 'ok'\n    except Exception as exc:\n        _log_tool_exception('t12', exc)\n        return 'error'\n        print('unreachable')\n"
    ).body[0]
    assert isinstance(t12, ast.FunctionDef)
    assert not _check_encompassing_boundary(t12), (
        "Mutation control: non-terminal return in handler must fail"
    )

    # 13. Hostile mutation control: f-string leak `return f"{exc}"`
    t13 = ast.parse(
        "@mcp.tool()\ndef t13():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t13', exc)\n        return f'{exc}'\n"
    ).body[0]
    assert isinstance(t13, ast.FunctionDef)
    assert not _check_encompassing_boundary(t13), (
        "Mutation control: f-string formatting leak must fail"
    )

    # 14. Hostile mutation control: nested alias assignment inside `if True:`
    t14 = ast.parse(
        "@mcp.tool()\ndef t14():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t14', exc)\n        if True:\n            leaked = exc\n            return leaked\n"
    ).body[0]
    assert isinstance(t14, ast.FunctionDef)
    assert not _check_encompassing_boundary(t14), "Mutation control: nested alias return must fail"

    # 15. Hostile mutation control: unreachable log after early return
    t15 = ast.parse(
        "@mcp.tool()\ndef t15():\n    try:\n        return 1\n    except Exception as exc:\n        return 'fail'\n        _log_tool_exception('t15', exc)\n"
    ).body[0]
    assert isinstance(t15, ast.FunctionDef)
    assert not _check_encompassing_boundary(t15), (
        "Mutation control: log after early return must fail"
    )

    # 16. Hostile mutation control: raise then return
    t16 = ast.parse(
        "@mcp.tool()\ndef t16():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t16', exc)\n        raise exc\n        return 'safe'\n"
    ).body[0]
    assert isinstance(t16, ast.FunctionDef)
    assert not _check_encompassing_boundary(t16), "Mutation control: raise in handler must fail"

    # 17. Hostile mutation control: attribute sink spoof `attacker._sanitized_tool_error(exc)`
    t17 = ast.parse(
        "@mcp.tool()\ndef t17():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t17', exc)\n        return attacker._sanitized_tool_error(exc)\n"
    ).body[0]
    assert isinstance(t17, ast.FunctionDef)
    assert not _check_encompassing_boundary(t17), "Mutation control: attribute sink spoof must fail"

    # 18. Hostile mutation control: walrus return `return (leaked := exc)`
    t18 = ast.parse(
        "@mcp.tool()\ndef t18():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t18', exc)\n        return (leaked := exc)\n"
    ).body[0]
    assert isinstance(t18, ast.FunctionDef)
    assert not _check_encompassing_boundary(t18), "Mutation control: walrus return must fail"

    # 19. Round 13 Hostile mutation control: nested early return before log
    t19 = ast.parse(
        "@mcp.tool()\ndef t19():\n    try:\n        return 1\n    except Exception as exc:\n        if True:\n            return 'unlogged'\n        _log_tool_exception('t19', exc)\n        return 'safe'\n"
    ).body[0]
    assert isinstance(t19, ast.FunctionDef)
    assert not _check_encompassing_boundary(t19), (
        "Mutation control: nested early return before log must fail"
    )

    # 20. Round 13 Hostile mutation control: shadowed sanitizer sink
    t20 = ast.parse(
        "@mcp.tool()\ndef t20():\n    try:\n        return 1\n    except Exception as exc:\n        _sanitized_tool_error = lambda t, e: 'fake'\n        _log_tool_exception('t20', exc)\n        return _sanitized_tool_error('t20', exc)\n"
    ).body[0]
    assert isinstance(t20, ast.FunctionDef)
    assert not _check_encompassing_boundary(t20), (
        "Mutation control: shadowed sanitizer sink must fail"
    )

    # 21. Round 13 Hostile mutation control: shadowed logger
    t21 = ast.parse(
        "@mcp.tool()\ndef t21():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception = lambda t, e: None\n        _log_tool_exception('t21', exc)\n        return 'safe'\n"
    ).body[0]
    assert isinstance(t21, ast.FunctionDef)
    assert not _check_encompassing_boundary(t21), "Mutation control: shadowed logger must fail"

    # 22. Round 13 Hostile mutation control: tuple broad handler leaking before compliant handler
    t22 = ast.parse(
        "@mcp.tool()\ndef t22():\n    try:\n        return 1\n    except (Exception,):\n        return str(exc)\n"
    ).body[0]
    assert isinstance(t22, ast.FunctionDef)
    assert not _check_encompassing_boundary(t22), (
        "Mutation control: tuple broad handler must be checked and fail"
    )


def test_direct_call_tool_rewrite_plan_resolver_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_plan handles resolver failure without leaking poison."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_RESOLVER_SECRET at C:\private\tg.exe"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    def _raise_resolver_poison():
        raise OSError(poison)

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", _raise_resolver_poison)

    content, data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_plan",
            {
                "pattern": "$A",
                "replacement": "$A",
                "lang": "python",
                "path": ".",
            },
        )
    )
    wire = content[0].text + "\n" + json.dumps(data, default=str)
    assert poison not in wire, f"Resolver poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Resolver poison not logged to stderr"


def test_direct_call_tool_rewrite_apply_policy_validation_details_poison_sanitized(
    tmp_path, monkeypatch, capsys
):
    """SEC-007: Direct FastMCP tg_rewrite_apply sanitizes PolicyValidationError.details on wire."""
    import asyncio

    from tensor_grep.cli import mcp_server
    from tensor_grep.cli.apply_policy import PolicyValidationError

    poison_token = "SEC007_POLICY_TOKEN_SECRET_98765"
    poison_path = r"C:\private\root\secret.py"

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    def _raise_policy_poison(*_args, **_kwargs):
        raise PolicyValidationError(
            "Unapproved modification",
            details={
                "unapproved_files": [poison_path],
                "secret_tokens": [poison_token],
                "allowed_prefixes": ["src/"],
            },
        )

    (tmp_path / "policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.apply_policy.load_apply_policy", _raise_policy_poison)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_apply",
            {
                "pattern": "$A",
                "replacement": "$A",
                "lang": "python",
                "path": ".",
                "policy": "policy.json",
            },
        )
    )
    wire = content[0].text
    assert poison_token not in wire, f"Poison token leaked to wire: {wire}"
    assert poison_path not in wire, f"Poison path leaked to wire: {wire}"

    captured = capsys.readouterr()
    assert poison_token in captured.err, "Poison token was not logged to stderr"
    assert json.dumps(poison_path) in captured.err, "Poison path was not logged to stderr"


def test_direct_call_tool_rewrite_apply_policy_evaluation_poison_sanitized(
    tmp_path, monkeypatch, capsys
):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_apply handles evaluate_apply_policy failure with edit disclosure."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_POLICY_EVAL_SECRET at C:\private\eval.py"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "policy.json").write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        }),
        encoding="utf-8",
    )

    def _raise_eval_poison(*_args, **_kwargs):
        raise RuntimeError(poison)

    monkeypatch.setattr("tensor_grep.cli.apply_policy.evaluate_apply_policy", _raise_eval_poison)
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: True)

    fake_result = json.dumps({"applied_edits": 1, "edits": [{"path": "foo.py"}]})
    monkeypatch.setattr(mcp_server, "_execute_embedded_rewrite_json", lambda **_kwargs: fake_result)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_apply",
            {
                "pattern": "x = 1",
                "replacement": "x = 2",
                "lang": "python",
                "path": ".",
                "policy": "policy.json",
            },
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Policy eval poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Policy eval poison not logged to stderr"
    assert "Edits may have already been applied" in wire


def test_direct_call_tool_devices_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP tg_devices handles collect_device_inventory poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_DEVICE_SECRET at C:\private\cuda.dll"

    def _raise_devices_poison():
        raise RuntimeError(poison)

    monkeypatch.setattr(mcp_server, "collect_device_inventory", _raise_devices_poison)

    content, _data = asyncio.run(mcp_server.mcp.call_tool("tg_devices", {}))
    wire = content[0].text
    assert poison not in wire, f"Devices poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Devices poison not logged to stderr"


def test_direct_call_tool_rewrite_diff_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_diff handles confinement poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    poison = r"SEC007_OUTER_SECRET at C:\private\diff.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_diff",
            {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_rewrite_plan_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_plan handles confinement poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    poison = r"SEC007_OUTER_SECRET at C:\private\plan.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_plan",
            {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_rewrite_apply_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_apply handles confinement poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    poison = r"SEC007_OUTER_SECRET at C:\private\apply.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite_apply",
            {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_index_search_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_index_search handles confinement poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    poison = r"SEC007_OUTER_SECRET at C:\private\root.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_index_search",
            {"pattern": "x", "path": "."},
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_ruleset_scan_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_ruleset_scan handles confinement poison without leaking."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    poison = r"SEC007_OUTER_SECRET at C:\private\root.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_ruleset_scan",
            {"ruleset": "security", "path": "."},
        )
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_broken_stderr_poison_sanitized(monkeypatch):
    """SEC-007: FastMCP tool call does not leak raw exception when stderr.write raises."""
    import asyncio
    import sys

    from tensor_grep.cli import mcp_server

    stderr_poison = "SEC007_STDERR_FAILURE_SECRET at C:/private/stderr.py"
    device_poison = "SEC007_DEVICE_FAILURE_SECRET at C:/private/gpu.py"

    class RaisingStderr:
        def write(self, s):
            raise OSError(stderr_poison)

        def flush(self):
            pass

    def _raise_devices_poison():
        raise RuntimeError(device_poison)

    monkeypatch.setattr(mcp_server, "collect_device_inventory", _raise_devices_poison)
    monkeypatch.setattr(sys, "stderr", RaisingStderr())

    # Direct FastMCP tool call must complete cleanly without ToolError or stderr leak
    content, _data = asyncio.run(mcp_server.mcp.call_tool("tg_devices", {}))
    wire = content[0].text
    assert stderr_poison not in wire, f"Stderr write poison leaked to wire: {wire}"
    assert device_poison not in wire, f"Device poison leaked to wire: {wire}"
    assert "tg_devices failed: internal error (RuntimeError)" in wire


def test_direct_call_tool_dynamic_exception_type_sanitized(monkeypatch, capsys):
    """SEC-007: Attacker-controlled dynamic exception type name degrades safely to InternalError on wire."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    DynamicSecretError = type("SEC007_DYNAMIC_TYPE_SECRET", (Exception,), {})
    poison_msg = "some private failure at C:/private/rulesets.py"

    def _raise_dynamic_error():
        raise DynamicSecretError(poison_msg)

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_dynamic_error)

    content, _data = asyncio.run(mcp_server.mcp.call_tool("tg_rulesets", {}))
    wire = content[0].text
    assert "SEC007_DYNAMIC_TYPE_SECRET" not in wire, (
        f"Dynamic exception class name leaked to wire: {wire}"
    )
    assert poison_msg not in wire, f"Poison message leaked to wire: {wire}"
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "SEC007_DYNAMIC_TYPE_SECRET" in captured.err, (
        "Dynamic exception class name not logged to stderr"
    )
    assert poison_msg in captured.err, "Poison message not logged to stderr"


def test_direct_call_tool_hostile_class_property_sanitized(monkeypatch, capsys):
    """SEC-007: Hostile __class__ property accessor does not escape boundary or leak secret on wire."""
    import asyncio

    from tensor_grep.cli import mcp_audit_tools, mcp_server

    class HostileClassException(Exception):
        @property
        def __class__(self):
            raise RuntimeError("SEC007_CLASS_ACCESS_SECRET")

    def _raise_hostile_error():
        raise HostileClassException("underlying secret")

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_hostile_error)

    content, _data = asyncio.run(mcp_server.mcp.call_tool("tg_rulesets", {}))
    wire = content[0].text
    assert "SEC007_CLASS_ACCESS_SECRET" not in wire, (
        f"Hostile __class__ accessor secret leaked to wire: {wire}"
    )
    assert "underlying secret" not in wire, f"Underlying secret leaked to wire: {wire}"
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "underlying secret" in captured.err, "Underlying secret not logged to stderr"
