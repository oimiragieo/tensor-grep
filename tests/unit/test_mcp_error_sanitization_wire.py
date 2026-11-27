"""SEC-007 wire-level MCP tool error sanitization tests (split from test_mcp_error_sanitization.py)."""

import asyncio
import json
import sys
from unittest.mock import patch

from tests.unit.mcp_tool_test_helpers import call_mcp_tool_parts


def _call_meta(tool: str, action: str, **kwargs: object):
    from tensor_grep.cli import mcp_server

    return call_mcp_tool_parts(mcp_server.mcp.call_tool(tool, {"action": action, **kwargs}))


def test_direct_call_tool_broad_poison_sanitized(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool handles broad exceptions without leaking poison on wire."""

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_BROAD_SECRET at C:\private\trace.py"
    with patch.object(mcp_server, "build_symbol_defs", side_effect=RuntimeError(poison)):
        content, _data = _call_meta("tg_navigate", "defs", symbol="foo", path=".")
        text = content[0].text
        assert poison not in text
        assert "RuntimeError" in text
        payload = json.loads(text)
        assert payload["error"]["code"] == "internal_error"
        captured = capsys.readouterr()
        assert poison in captured.err


def test_direct_call_tool_narrow_poison_sanitized(capsys):
    """SEC-007: Direct FastMCP mcp.call_tool handles narrow exceptions without leaking poison on wire."""

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_NARROW_SECRET at C:\private\missing.py"
    with patch.object(mcp_server, "build_file_importers", side_effect=FileNotFoundError(poison)):
        content, _data = _call_meta(
            "tg_navigate", "importers", file="src/tensor_grep/cli/mcp_server.py", path="."
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

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_CWD_SECRET"
    with patch("pathlib.Path.cwd", side_effect=RuntimeError(poison)):
        content, _data = call_mcp_tool_parts(
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

    from tensor_grep.cli import mcp_server

    poison_file = r"C:\outside\secret.py" if sys.platform == "win32" else "/outside/secret.py"
    content, _data = call_mcp_tool_parts(
        mcp_server.mcp.call_tool("tg_navigate", {"action": "imports", "file": poison_file})
    )
    text = content[0].text
    assert "outside" not in text and "secret.py" not in text
    payload = json.loads(text)
    assert payload["error"]["code"] == "invalid_input"
    assert "file" not in payload or payload["file"] == "[refused]"
    captured = capsys.readouterr()
    assert "path confinement refusal" in captured.err


def test_direct_call_tool_session_open_get_session_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_session_open sanitizes get_session exception on wire."""

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.py").write_text("def f(): pass\n", encoding="utf-8")

    poison = r"SEC007_GET_SESSION_POISON_SECRET"
    with patch("tensor_grep.cli.session_store.get_session", side_effect=RuntimeError(poison)):
        from mcp import types

        res = asyncio.run(
            mcp_server.mcp.call_tool("tg_session", {"action": "open", "path": str(tmp_path)})
        )
        content = res.content if isinstance(res, types.CallToolResult) else res[0]
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
        content, data = _call_meta(
            "tg_rewrite", "diff", pattern="$A", replacement="$A", lang="python", path=str(tmp_path)
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
        content, data = _call_meta(
            "tg_rewrite", "plan", pattern="$A", replacement="$A", lang="python", path=str(tmp_path)
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
        content, data = _call_meta(
            "tg_rewrite", "diff", pattern="$A", replacement="$A", lang="python", path=str(tmp_path)
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
        content, data = call_mcp_tool_parts(
            mcp_server.mcp.call_tool(
                "tg_query",
                {"action": "index", "pattern": "def foo", "path": str(tmp_path)},
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
        *(f"mcp/{p.name}" for p in sorted((cli_dir / "mcp").glob("*.py"))),
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

        # Scope-wide shadow analysis: inspect all parameters, definitions, imports, and stores
        # anywhere in fn_node (not just inside except handler) to guarantee protected sinks/loggers are unshadowed.
        for param in fn_node.args.posonlyargs + fn_node.args.args + fn_node.args.kwonlyargs:
            if param.arg in protected_names:
                return False
        if fn_node.args.vararg and fn_node.args.vararg.arg in protected_names:
            return False
        if fn_node.args.kwarg and fn_node.args.kwarg.arg in protected_names:
            return False

        for n in ast.walk(fn_node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                if n.id in protected_names:
                    return False
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if n.name in protected_names:
                    return False
            elif isinstance(n, ast.ExceptHandler):
                if n.name and n.name in protected_names:
                    return False
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    bound = alias.asname or alias.name
                    if bound in protected_names:
                        return False
            elif isinstance(n, ast.ImportFrom):
                for alias in n.names:
                    bound = alias.asname or alias.name
                    if bound in protected_names:
                        return False
            elif isinstance(n, (ast.MatchAs, ast.MatchStar)):
                if n.name and n.name in protected_names:
                    return False
            elif isinstance(n, ast.MatchMapping):
                if n.rest and n.rest in protected_names:
                    return False

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

            # Rule 4: Comprehensive taint analysis
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

                    # Safe sink call: sub must be a direct argument to an unshadowed ast.Name call in allowed_sinks
                    # at the exact sanctioned argument position (e.g. exc position, never tool_name or other position).
                    allowed_sink_positions = {
                        "_log_tool_exception": {1},
                        "_sanitized_tool_error": {1},
                        "_sanitized_tool_error_text": {1},
                        "_ruleset_scan_error": {0},
                        "_index_search_error": {0},
                        "_rewrite_error": {0},
                        "_classify_native_rewrite_failure": {0},
                        "_safe_exception_class_name": {0},
                    }
                    if isinstance(p, ast.Call):
                        if isinstance(p.func, ast.Name) and p.func.id in allowed_sink_positions:
                            # Ensure sub is passed as a direct positional arg at sanctioned index
                            sanctioned = allowed_sink_positions[p.func.id]
                            if any(p.args[idx] is sub for idx in sanctioned if idx < len(p.args)):
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
                    or (
                        isinstance(d, ast.Name)
                        and d.id in {"_register_legacy_tool", "_register_tool"}
                    )
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

    # 23. Round 15 Hostile mutation control: shadowed logger via except target
    t23 = ast.parse(
        "@mcp.tool()\ndef t23():\n    try:\n        return 1\n    except Exception as _log_tool_exception:\n        _log_tool_exception('t23', _log_tool_exception)\n        return _sanitized_tool_error_text('t23', _log_tool_exception)\n"
    ).body[0]
    assert isinstance(t23, ast.FunctionDef)
    assert not _check_encompassing_boundary(t23), (
        "Mutation control: shadowed logger via except target must fail"
    )

    # 24. Round 15 Hostile mutation control: shadowed sanitizer in try body
    t24 = ast.parse(
        "@mcp.tool()\ndef t24():\n    try:\n        _sanitized_tool_error_text = lambda t, e: 'fake'\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t24', exc)\n        return _sanitized_tool_error_text('t24', exc)\n"
    ).body[0]
    assert isinstance(t24, ast.FunctionDef)
    assert not _check_encompassing_boundary(t24), (
        "Mutation control: shadowed sanitizer in try body must fail"
    )

    # 25. Round 15 Hostile mutation control: shadowed sanitizer via parameter
    t25 = ast.parse(
        "@mcp.tool()\ndef t25(_sanitized_tool_error_text=None):\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t25', exc)\n        return _sanitized_tool_error_text('t25', exc)\n"
    ).body[0]
    assert isinstance(t25, ast.FunctionDef)
    assert not _check_encompassing_boundary(t25), (
        "Mutation control: shadowed sanitizer via parameter must fail"
    )

    # 26. Round 15 Hostile mutation control: shadowed sanitizer via pattern matching
    t26 = ast.parse(
        "@mcp.tool()\ndef t26(val):\n    try:\n        match val:\n            case [_sanitized_tool_error_text]:\n                pass\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t26', exc)\n        return _sanitized_tool_error_text('t26', exc)\n"
    ).body[0]
    assert isinstance(t26, ast.FunctionDef)
    assert not _check_encompassing_boundary(t26), (
        "Mutation control: shadowed sanitizer via pattern matching must fail"
    )

    # 27. Round 15 Hostile mutation control: poisoned exc passed as tool_name to sanitizer
    t27 = ast.parse(
        "@mcp.tool()\ndef t27():\n    try:\n        return 1\n    except Exception as exc:\n        _log_tool_exception('t27', exc)\n        return _sanitized_tool_error_text(exc, exc)\n"
    ).body[0]
    assert isinstance(t27, ast.FunctionDef)
    assert not _check_encompassing_boundary(t27), (
        "Mutation control: exc in wrong sink position must fail"
    )


def test_direct_call_tool_rewrite_plan_resolver_poison_sanitized(tmp_path, monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_plan handles resolver failure without leaking poison."""
    from tensor_grep.cli import mcp_server

    poison = r"SEC007_RESOLVER_SECRET at C:\private\tg.exe"
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    def _raise_resolver_poison():
        raise OSError(poison)

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", _raise_resolver_poison)
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: False)

    content, data = _call_meta(
        "tg_rewrite", "plan", pattern="$A", replacement="$A", lang="python", path="."
    )
    assert json.loads(content[0].text)["error"]["code"] == "unavailable"
    wire = content[0].text + "\n" + json.dumps(data, default=str)
    assert poison not in wire, f"Resolver poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Resolver poison not logged to stderr"


def test_direct_call_tool_rewrite_apply_policy_validation_details_poison_sanitized(
    tmp_path, monkeypatch, capsys
):
    """SEC-007: Direct FastMCP tg_rewrite_apply sanitizes PolicyValidationError.details on wire."""

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

    content, _data = _call_meta(
        "tg_rewrite",
        "apply",
        pattern="$A",
        replacement="$A",
        lang="python",
        path=".",
        policy="policy.json",
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

    content, _data = _call_meta(
        "tg_rewrite",
        "apply",
        pattern="x = 1",
        replacement="x = 2",
        lang="python",
        path=".",
        policy="policy.json",
    )
    wire = content[0].text
    assert poison not in wire, f"Policy eval poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Policy eval poison not logged to stderr"
    assert "Edits may have already been applied" in wire


def test_direct_call_tool_devices_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP tg_devices handles collect_device_inventory poison without leaking."""

    from tensor_grep.cli import mcp_server

    poison = r"SEC007_DEVICE_SECRET at C:\private\cuda.dll"

    def _raise_devices_poison():
        raise RuntimeError(poison)

    monkeypatch.setattr(mcp_server, "collect_device_inventory", _raise_devices_poison)

    from mcp import types

    res = asyncio.run(mcp_server.mcp.call_tool("tg_explore", {"action": "devices"}))
    content = res.content if isinstance(res, types.CallToolResult) else res[0]
    wire = content[0].text
    assert poison not in wire, f"Devices poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Devices poison not logged to stderr"


def test_direct_call_tool_rewrite_diff_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_diff handles confinement poison without leaking."""

    from tensor_grep.cli import mcp_audit_tools

    poison = r"SEC007_OUTER_SECRET at C:\private\diff.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = _call_meta(
        "tg_rewrite", "diff", pattern="x", replacement="y", lang="python", path="."
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_rewrite_plan_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_plan handles confinement poison without leaking."""

    from tensor_grep.cli import mcp_audit_tools

    poison = r"SEC007_OUTER_SECRET at C:\private\plan.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = _call_meta(
        "tg_rewrite", "plan", pattern="x", replacement="y", lang="python", path="."
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_rewrite_apply_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_rewrite_apply handles confinement poison without leaking."""

    from tensor_grep.cli import mcp_audit_tools

    poison = r"SEC007_OUTER_SECRET at C:\private\apply.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = _call_meta(
        "tg_rewrite", "apply", pattern="x", replacement="y", lang="python", path="."
    )
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_index_search_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_index_search handles confinement poison without leaking."""

    from tensor_grep.cli import mcp_audit_tools

    poison = r"SEC007_OUTER_SECRET at C:\private\root.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = _call_meta("tg_query", "index", pattern="x", path=".")
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_ruleset_scan_confinement_poison_sanitized(monkeypatch, capsys):
    """SEC-007: Direct FastMCP mcp.call_tool tg_ruleset_scan handles confinement poison without leaking."""

    from tensor_grep.cli import mcp_audit_tools

    poison = r"SEC007_OUTER_SECRET at C:\private\root.py"

    def _poison_confine(*_args, **_kwargs):
        raise OSError(poison)

    monkeypatch.setattr(mcp_audit_tools, "_confine_mcp_path", _poison_confine)

    content, _data = _call_meta("tg_scan", "scan", ruleset="security", path=".")
    wire = content[0].text
    assert poison not in wire, f"Confinement poison leaked to wire: {wire}"
    captured = capsys.readouterr()
    assert poison in captured.err, "Confinement poison not logged to stderr"


def test_direct_call_tool_broken_stderr_poison_sanitized(monkeypatch):
    """SEC-007: FastMCP tool call does not leak raw exception when stderr.write raises."""
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
    from mcp import types

    res = asyncio.run(mcp_server.mcp.call_tool("tg_explore", {"action": "devices"}))
    content = res.content if isinstance(res, types.CallToolResult) else res[0]
    wire = content[0].text
    assert stderr_poison not in wire, f"Stderr write poison leaked to wire: {wire}"
    assert device_poison not in wire, f"Device poison leaked to wire: {wire}"
    assert "tg_devices failed: internal error (RuntimeError)" in wire


def test_direct_call_tool_dynamic_exception_type_sanitized(monkeypatch, capsys):
    """SEC-007: Attacker-controlled dynamic exception type name degrades safely to InternalError on wire."""

    from tensor_grep.cli import mcp_audit_tools

    DynamicSecretError = type("SEC007_DYNAMIC_TYPE_SECRET", (Exception,), {})
    poison_msg = "some private failure at C:/private/rulesets.py"

    def _raise_dynamic_error():
        raise DynamicSecretError(poison_msg)

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_dynamic_error)

    content, _data = _call_meta("tg_scan", "rulesets")
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

    from tensor_grep.cli import mcp_audit_tools

    class HostileClassException(Exception):
        @property
        def __class__(self):
            raise RuntimeError("SEC007_CLASS_ACCESS_SECRET")

    def _raise_hostile_error():
        raise HostileClassException("underlying secret")

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_hostile_error)

    content, _data = _call_meta("tg_scan", "rulesets")
    wire = content[0].text
    assert "SEC007_CLASS_ACCESS_SECRET" not in wire, f"Hostile class secret leaked: {wire}"
    assert "underlying secret" not in wire, f"Underlying secret leaked to wire: {wire}"
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "underlying secret" in captured.err, "Underlying secret not logged to stderr"


def test_direct_call_tool_spoofed_module_exception_type_sanitized(monkeypatch, capsys):
    """SEC-007: Exception with forged __module__ matching tensor_grep degrades safely to InternalError on wire."""

    from tensor_grep.cli import mcp_audit_tools

    SpoofedModuleError = type(
        "SEC007_SPOOFED_MODULE_SECRET",
        (Exception,),
        {"__module__": "tensor_grep.cli.mcp_server"},
    )
    poison_msg = "some spoofed private failure at C:/private/rulesets.py"

    def _raise_spoofed_error():
        raise SpoofedModuleError(poison_msg)

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_spoofed_error)

    content, _data = _call_meta("tg_scan", "rulesets")
    wire = content[0].text
    assert "SEC007_SPOOFED_MODULE_SECRET" not in wire, f"Spoofed leaked: {wire}"
    assert poison_msg not in wire, f"Poison message leaked to wire: {wire}"
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "SEC007_SPOOFED_MODULE_SECRET" in captured.err, (
        "Spoofed exception class name not logged to stderr"
    )
    assert poison_msg in captured.err, "Poison message not logged to stderr"


def test_direct_call_tool_colliding_name_dynamic_exception_type_sanitized(monkeypatch, capsys):
    """SEC-007: Dynamic synthetic exception claiming trusted name (e.g. ValueError) degrades safely to InternalError."""

    from tensor_grep.cli import mcp_audit_tools

    SyntheticValueError = type("ValueError", (Exception,), {})
    poison_msg = "synthetic value error secret at C:/private/rulesets.py"

    def _raise_synthetic_value_error():
        raise SyntheticValueError(poison_msg)

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_synthetic_value_error)

    content, _data = _call_meta("tg_scan", "rulesets")
    wire = content[0].text
    # Exact type identity does not match builtins.ValueError -> must be InternalError
    assert "ValueError" not in wire, f"Synthetic ValueError leaked on wire: {wire}"
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "ValueError" in captured.err, "Synthetic ValueError not logged to stderr"
    assert poison_msg in captured.err, "Poison message not logged to stderr"


def test_direct_call_tool_rewrite_apply_audit_manifest_record_failure_sanitized(
    tmp_path, monkeypatch, capsys
):
    """SEC-007: Dynamic exception during audit manifest recording degrades safely to InternalError in wire record_error."""
    import subprocess
    from pathlib import Path

    from tensor_grep.cli import mcp_server

    (tmp_path / "sample.py").write_text("def f(a): return a\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    AuditRecordSecretError = type("SEC007_AUDIT_RECORD_SECRET", (Exception,), {})

    def _raise_record_error(*_args, **_kwargs):
        raise AuditRecordSecretError("failed to record manifest")

    native_payload = {
        "applied": True,
        "audit_manifest": {"path": str(tmp_path / "audit.json"), "file_count": 1},
    }
    monkeypatch.setattr(
        "tensor_grep.cli.mcp_server.resolve_native_tg_binary", lambda *a, **k: Path("tg.exe")
    )
    monkeypatch.setattr(
        "tensor_grep.cli.mcp_rewrite_tools.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=["tg.exe"], returncode=0, stdout=json.dumps(native_payload), stderr=""
        ),
    )
    monkeypatch.setattr("tensor_grep.cli.audit_manifest.record_audit_manifest", _raise_record_error)

    from mcp import types

    res = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_rewrite",
            {
                "action": "apply",
                "pattern": "def $F($A): return $A",
                "replacement": "def $F($A): return $A",
                "lang": "python",
                "path": str(tmp_path),
                "audit_manifest": "audit.json",
            },
        )
    )
    content = res.content if isinstance(res, types.CallToolResult) else res[0]
    wire = content[0].text
    parsed = json.loads(wire)
    manifest = parsed.get("audit_manifest", {})
    assert manifest.get("recorded") is False
    assert "SEC007_AUDIT_RECORD_SECRET" not in wire, (
        f"Dynamic audit record exception class leaked to wire: {wire}"
    )
    assert manifest.get("record_error") == "InternalError", (
        f"Expected InternalError for dynamic class, got: {manifest.get('record_error')}"
    )
    captured = capsys.readouterr()
    assert "SEC007_AUDIT_RECORD_SECRET" in captured.err, (
        "Dynamic audit record exception class not logged to stderr"
    )


def test_direct_call_tool_metaclass_equality_spoof_sanitized(monkeypatch, capsys):
    """SEC-007: Exception with hostile metaclass spoofing __eq__ and __hash__ degrades to InternalError."""

    from tensor_grep.cli import mcp_audit_tools

    Trusted = ValueError
    Meta = type(
        "Meta",
        (type,),
        {"__hash__": lambda cls: hash(Trusted), "__eq__": lambda cls, other: other is Trusted},
    )
    HostileMetaError = Meta("SEC007_METACLASS_SPOOF_SECRET", (Exception,), {})
    poison_msg = "some private metaclass spoof failure at C:/private/rulesets.py"

    def _raise_meta_error():
        raise HostileMetaError(poison_msg)

    monkeypatch.setattr(mcp_audit_tools, "_build_rulesets_payload", _raise_meta_error)

    content, _data = _call_meta("tg_scan", "rulesets")
    wire = content[0].text
    assert "SEC007_METACLASS_SPOOF_SECRET" not in wire, (
        f"Metaclass spoof secret leaked to wire: {wire}"
    )
    assert "ValueError" not in wire, (
        f"Metaclass equality spoof allowed class to masquerade as ValueError: {wire}"
    )
    assert "Rulesets lookup failed: InternalError" in wire
    captured = capsys.readouterr()
    assert "SEC007_METACLASS_SPOOF_SECRET" in captured.err, (
        "Metaclass spoof exception class not logged to stderr"
    )
    assert poison_msg in captured.err, "Poison message not logged to stderr"
