"""Focused regression controls for the first security audit wave."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("target", ["keep.txt", ".git/config", "unrelated.json"])
def test_rewrite_manifest_refuses_nonartifact_before_effects(tmp_path, monkeypatch, target):
    from tensor_grep.cli import checkpoint_store, mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    victim = tmp_path / target
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b'{"mine": true}\n')
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"plan": {}}', ""))
    checkpoint = Mock()
    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    monkeypatch.setattr(checkpoint_store, "create_checkpoint", checkpoint)

    raw, code = mcp_server.execute_rewrite_apply_json(
        pattern="foo($A)",
        replacement="bar($A)",
        lang="python",
        path=str(tmp_path),
        audit_manifest=target,
        checkpoint=True,
    )

    assert code == 1, raw
    payload = json.loads(raw)
    assert payload["error"]["code"] == "invalid_input"
    assert "artifact" in payload["error"]["message"]
    run.assert_not_called()
    checkpoint.assert_not_called()
    assert victim.read_bytes() == b'{"mine": true}\n'


def test_rewrite_manifest_accepts_existing_rewrite_artifact(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    manifest = tmp_path / "audit.json"
    manifest.write_text('{"kind": "rewrite-audit-manifest"}', encoding="utf-8")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"plan": {}}', ""))
    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    raw, code = mcp_server.execute_rewrite_apply_json(
        pattern="foo",
        replacement="bar",
        lang="python",
        path=str(tmp_path),
        audit_manifest=str(manifest),
    )
    assert code == 0, raw
    argv = run.call_args.args[0]
    assert argv[argv.index("--audit-manifest") + 1] == str(manifest.resolve())


@pytest.mark.parametrize(
    "recorded,detected",
    [
        ("git-worktree-snapshot", "filesystem-snapshot"),
        ("filesystem-snapshot", "git-worktree-snapshot"),
        ("filesystem-snapshot", "filesystem-snapshot"),
        ("git-worktree-snapshot", "git-worktree-snapshot"),
        (None, "filesystem-snapshot"),
    ],
)
def test_checkpoint_undo_mode_guard(tmp_path, monkeypatch, recorded, detected):
    from tensor_grep.cli import checkpoint_store as store

    checkpoint_id = "ckpt-mode-control"
    snapshot = store._snapshot_path(tmp_path, checkpoint_id)
    snapshot.mkdir(parents=True)
    (snapshot / "source.py").write_bytes(b"before\n")
    (tmp_path / "source.py").write_bytes(b"after\n")
    (tmp_path / ".env").write_bytes(b"SECRET=keep\n")
    metadata = {"entries": {"source.py": True}, "mode": recorded, "scope": "tree"}
    store._metadata_path(tmp_path, checkpoint_id).write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(store, "_detect_checkpoint_root", lambda _path: (tmp_path, detected))
    monkeypatch.setattr(store, "_git_snapshot_entries", lambda _root: ({"source.py": True}, []))
    scan = Mock(wraps=store._filesystem_snapshot_entries)
    monkeypatch.setattr(store, "_filesystem_snapshot_entries", scan)
    if recorded is not None and recorded != detected:
        before = {
            p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
        }
        with pytest.raises(store.CheckpointCorruptError, match=r"mode.*no files were modified"):
            store.undo_checkpoint(checkpoint_id, str(tmp_path))
        assert {
            p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
        } == before
        scan.assert_not_called()
    else:
        result = store.undo_checkpoint(checkpoint_id, str(tmp_path))
        assert result.restored_files == 1
        assert (tmp_path / "source.py").read_bytes() == b"before\n"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows implicitly searches CWD")
@pytest.mark.parametrize("resolver", ["rg", "ast"])
@pytest.mark.parametrize("cwd_entry", [None, ".", "absolute"])
def test_windows_resolver_ignores_cwd_but_accepts_trusted_path(
    tmp_path, monkeypatch, resolver, cwd_entry
):
    from tensor_grep.backends import ast_wrapper_backend
    from tensor_grep.cli import runtime_paths

    cwd = tmp_path / "repo"
    trusted = tmp_path / "trusted"
    cwd.mkdir()
    trusted.mkdir()
    name = "rg.exe" if resolver == "rg" else "ast-grep.cmd"
    (cwd / name).write_bytes(b"untrusted")
    (trusted / name).write_bytes(b"trusted")
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("TG_RG_PATH", raising=False)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    paths = [str(trusted)]
    if cwd_entry is not None:
        paths.insert(0, str(cwd) if cwd_entry == "absolute" else cwd_entry)
    monkeypatch.setenv("PATH", os.pathsep.join(paths))
    probe = Mock(return_value=True)
    monkeypatch.setattr(ast_wrapper_backend, "_is_ast_grep_sg_binary", probe)
    runtime_paths.resolve_ripgrep_binary.cache_clear()
    try:
        result = (
            runtime_paths.resolve_ripgrep_binary()
            if resolver == "rg"
            else ast_wrapper_backend.AstGrepWrapperBackend()._get_binary_name()
        )
        assert Path(result).resolve() == (trusted / name).resolve()
        if resolver == "ast":
            assert [Path(call.args[0]).resolve() for call in probe.call_args_list] == [
                (trusted / name).resolve()
            ]
    finally:
        runtime_paths.resolve_ripgrep_binary.cache_clear()


@pytest.mark.parametrize("ensurepip", [False, True])
def test_upgrade_runs_every_attempt_from_interpreter_directory(tmp_path, monkeypatch, ensurepip):
    from tensor_grep.cli import main

    executable = tmp_path / "trusted" / "python.exe"
    monkeypatch.setattr(sys, "executable", str(executable))
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if ensurepip and len(calls) == 1:
            raise subprocess.CalledProcessError(1, cmd, stderr="No module named pip")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(main.subprocess, "run", run)
    _, label = main._run_upgrade([
        ("pip", [str(executable), "-m", "pip", "install", "tensor-grep"])
    ])
    assert label == ("pip+ensurepip" if ensurepip else "pip")
    assert len(calls) == (3 if ensurepip else 1)
    assert all(kwargs.get("cwd") == str(executable.parent) for _, kwargs in calls)


def test_upgrade_detached_helper_starts_from_interpreter_directory(tmp_path, monkeypatch):
    import importlib.metadata

    from tensor_grep.cli import main

    executable = tmp_path / "trusted" / "python.exe"
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.0.0")
    monkeypatch.setattr(main, "_latest_pypi_tensor_grep_version", lambda: None)
    monkeypatch.setattr(main, "_upgrade_running_session_daemon_snapshot", lambda: None)
    monkeypatch.setattr(main, "_managed_native_frontdoor_path", lambda: None)
    monkeypatch.setattr(
        main._managed_runtime_upgrade, "handle_managed_windows_upgrade", lambda *_a: False
    )
    monkeypatch.setattr(main, "_run_upgrade", Mock(side_effect=RuntimeError("WinError 32")))
    popen = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", popen)
    main.upgrade()
    popen.assert_called_once()
    assert popen.call_args.kwargs.get("cwd") == str(executable.parent)


_SESSION_ACTIONS = [
    "open",
    "list",
    "show",
    "refresh",
    "context",
    "edit_plan",
    "context_render",
    "blast_radius",
    "blast_radius_plan",
    "blast_radius_render",
    "file_importers",
]


@pytest.mark.parametrize("action", _SESSION_ACTIONS)
@pytest.mark.parametrize("direct", [False, True], ids=["meta", "legacy"])
def test_session_discovered_parent_outside_mcp_root_is_refused(
    tmp_path, monkeypatch, action, direct
):
    from tensor_grep.cli import mcp_server

    (tmp_path / ".git").mkdir()
    scope = tmp_path / "packages" / "a"
    scope.mkdir(parents=True)
    source = scope / "a.py"
    source.write_text("def foo(): pass\n", encoding="utf-8")
    monkeypatch.chdir(scope)
    monkeypatch.setenv("TG_MCP_ROOT", str(scope))
    kwargs = {"path": str(scope)}
    if action not in {"open", "list"}:
        kwargs["session_id"] = "session-control"
    if action in {"context", "edit_plan", "context_render"}:
        kwargs["query"] = "foo"
    if action.startswith("blast_radius"):
        kwargs["symbol"] = "foo"
    if action == "file_importers":
        kwargs["file"] = str(source)
    tool = getattr(mcp_server, "tg_session_" + action) if direct else mcp_server.tg_session
    if not direct:
        kwargs["action"] = action
    payload = json.loads(tool(**kwargs))
    assert payload.get("error"), payload
    assert "must stay within" in str(payload["error"]), payload
    assert not (tmp_path / ".tensor-grep").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_session_list_accepts_equal_or_nested_discovered_root(tmp_path, monkeypatch, nested):
    from tensor_grep.cli import mcp_server

    root = tmp_path / "nested" if nested else tmp_path
    root.mkdir(exist_ok=True)
    (root / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    payload = json.loads(mcp_server.tg_session(action="list", path=str(root)))
    assert "error" not in payload, payload
    assert payload["sessions"] == []
