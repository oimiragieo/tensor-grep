"""Meta text searches retain default-scope guards after path confinement."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock

import pytest

from tensor_grep.cli import mcp_search_scope, mcp_server
from tensor_grep.core.result import MatchLine, SearchResult


@pytest.fixture
def search_root(tmp_path, monkeypatch):
    root = tmp_path / "server-root"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_MCP_ROOT", str(root))
    backend = MagicMock()
    backend.search.side_effect = lambda path, *_args, **_kwargs: SearchResult(
        matches=[MatchLine(file=path, line_number=1, text="needle")],
        matched_file_paths=[path],
        total_matches=1,
        total_files=1,
    )
    pipeline = MagicMock()
    pipeline.get_backend.return_value = backend
    pipeline.selected_backend_name = "CpuBackend"
    pipeline.selected_backend_reason = "cpu_fallback"
    pipeline.fallback_reason = None
    pipeline.selected_gpu_device_ids = []
    pipeline.selected_gpu_chunk_plan_mb = []
    monkeypatch.setattr(mcp_server, "Pipeline", lambda **_kwargs: pipeline)
    scanner = MagicMock()
    scanner.scan_truncated = False
    files = [str(root / f"file_{i}.py") for i in range(1501)]
    scanner.walk.side_effect = lambda _path: iter(files)
    monkeypatch.setattr(mcp_server, "DirectoryScanner", lambda _config: scanner)
    return root, backend, scanner


@pytest.mark.parametrize("action", ["text", "search"])
@pytest.mark.parametrize("scope", ["default", "dot", "workspace-dot"])
@pytest.mark.parametrize("filter_args", [{"glob": "*.py"}, {"type_filter": "py"}])
def test_meta_default_scope_refuses_large_root(search_root, action, scope, filter_args):
    root, backend, scanner = search_root
    scope_args = {"path": "."} if scope == "dot" else {}
    if scope == "workspace-dot":
        scope_args = {"workspace_roots": ["."]}
    payload = json.loads(
        mcp_server.tg_query(action=action, pattern="needle", **filter_args, **scope_args)
    )
    if scope == "workspace-dot":
        payload = payload["results_by_root"][str(root)]
    assert "error" in payload, "default-root search must refuse before backend execution"
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "1500" in payload["error"]["message"]
    assert payload["path"] == str(root)
    scanner.walk.assert_called_once_with(str(root))
    backend.search.assert_not_called()


@pytest.mark.parametrize("action", ["text", "search"])
@pytest.mark.parametrize("multi_root", [False, True])
def test_meta_explicit_scope_with_glob_still_searches(search_root, action, multi_root):
    root, backend, scanner = search_root
    scope_args = {"workspace_roots": [str(root)]} if multi_root else {"path": str(root)}
    payload = json.loads(
        mcp_server.tg_query(
            action=action, pattern="needle", glob="*.py", max_results=1, **scope_args
        )
    )
    if multi_root:
        payload = payload["results_by_root"][str(root)]
    assert "error" not in payload
    assert payload["total_matches"] == 1501
    assert payload["rendered_match_count"] == 1
    scanner.walk.assert_called_once_with(str(root))
    assert backend.search.call_count == 1501


@pytest.mark.parametrize("action", ["text", "search"])
def test_meta_scope_refuses_escape_before_scan(search_root, action):
    root, backend, scanner = search_root
    payload = json.loads(
        mcp_server.tg_query(action=action, pattern="needle", path=str(root.parent))
    )
    assert payload["error"]["message"] == "path must stay within the MCP root (refused)"
    scanner.walk.assert_not_called()
    backend.search.assert_not_called()


def test_search_scope_resets_after_nested_calls_and_failure():
    path = "confined-root"

    def explicit_call(*, path):
        assert not mcp_search_scope.paths_defaulted(path)
        raise ValueError("handler failure")

    def default_call(*, path):
        assert mcp_search_scope.paths_defaulted(path)
        assert not mcp_search_scope.paths_defaulted("different-root")
        with pytest.raises(ValueError, match="handler failure"):
            mcp_search_scope.call(explicit_call, path=path, paths_defaulted=False)
        assert mcp_search_scope.paths_defaulted(path)
        return "ok"

    assert mcp_search_scope.call(default_call, path=path, paths_defaulted=True) == "ok"
    assert not mcp_search_scope.paths_defaulted(path)


def test_concurrent_search_scopes_are_isolated():
    barrier = Barrier(2, timeout=5)

    def request(defaulted):
        def handler(*, path):
            barrier.wait()
            assert mcp_search_scope.paths_defaulted(path) is defaulted
            barrier.wait()
            return "ok"

        result = mcp_search_scope.call(handler, path="same-root", paths_defaulted=defaulted)
        assert not mcp_search_scope.paths_defaulted("same-root")
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request, defaulted) for defaulted in (True, False)]
        assert [future.result(timeout=10) for future in futures] == ["ok", "ok"]
