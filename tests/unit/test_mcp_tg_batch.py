import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from mcp import types

from tensor_grep.backends import ripgrep_backend
from tensor_grep.cli import mcp_batch_tools, mcp_server

NORMALIZED_VOLATILE_FIELDS: tuple[str, ...] = ()


def _payload(result: str) -> dict[str, object]:
    return json.loads(result)


def test_tg_batch_stamps_its_own_envelope_with_current_contract(monkeypatch):
    monkeypatch.setattr(mcp_server, "tg_search", lambda **kwargs: json.dumps({"ok": True}))

    payload = _payload(mcp_server.tg_batch([{"id": "one", "kind": "search", "pattern": "x"}]))

    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert payload["results"][0]["result"] == {"ok": True}


def test_tg_batch_discovery_schema_describes_queries_as_object_list() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool = next(item for item in tools if item.name == "tg_batch")
    queries_schema = tool.inputSchema["properties"]["queries"]

    assert queries_schema["type"] == "array"
    assert queries_schema["items"]["type"] == "object"
    assert queries_schema["items"].get("additionalProperties") is True
    assert queries_schema.get("maxItems") == 16


def test_tg_batch_is_registered_and_returns_single_tool_json(monkeypatch):
    def fake_search(pattern: str, path: str = ".", **kwargs: object) -> str:
        return json.dumps({"pattern": pattern, "path": path, "kwargs": kwargs})

    monkeypatch.setattr(mcp_server, "tg_search", fake_search)
    standalone = _payload(fake_search("needle", "."))
    result = _payload(mcp_server.tg_batch([{"id": "q1", "kind": "search", "pattern": "needle"}]))

    assert result == {
        "version": 1,
        "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        "results": [{"id": "q1", "kind": "search", "result": standalone}],
        "result_incomplete": False,
    }
    assert "tg_batch" in {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}


@pytest.mark.parametrize(
    "kind,tool_name,args",
    [
        ("search", "tg_search", {"pattern": "p"}),
        ("find", "tg_find", {"query": "q"}),
        ("defs", "tg_symbol_defs", {"symbol": "s"}),
        ("refs", "tg_symbol_refs", {"symbol": "s"}),
        ("callers", "tg_symbol_callers", {"symbol": "s"}),
        ("source", "tg_symbol_source", {"symbol": "s"}),
    ],
)
def test_tg_batch_preserves_standalone_json_for_each_kind(
    tmp_path, monkeypatch, kind, tool_name, args
):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "dense model disabled for batch equivalence test"),
    )
    (tmp_path / "sample.py").write_text(
        "def target(value):\n    return value\n\ndef caller():\n    return target('needle')\n",
        encoding="utf-8",
    )
    arguments = {**args, "path": str(tmp_path)}
    if kind == "search":
        arguments["pattern"] = "target"
    elif kind == "find":
        arguments["query"] = "target"
    else:
        arguments["symbol"] = "target"
    standalone = _payload(getattr(mcp_server, tool_name)(**arguments))
    assert "error" not in standalone, standalone
    if kind == "search":
        assert standalone["total_matches"] > 0, standalone
    else:
        result_key = {
            "find": "matches",
            "defs": "definitions",
            "refs": "references",
            "callers": "callers",
            "source": "sources",
        }[kind]
        assert standalone[result_key], standalone

    result = _payload(mcp_server.tg_batch([{"id": "same", "kind": kind, **arguments}]))

    assert NORMALIZED_VOLATILE_FIELDS == ()
    assert result["results"] == [{"id": "same", "kind": kind, "result": standalone}]


def test_tg_batch_preserves_plain_text_search_result_and_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    good = {"id": "good", "kind": "search", "pattern": "needle", "structured_json": False}
    bad = {
        "id": "bad",
        "kind": "search",
        "pattern": "needle",
        "path": "../..",
        "structured_json": False,
    }
    standalone = mcp_server.tg_search("needle", path=str(tmp_path), structured_json=False)

    payload = _payload(mcp_server.tg_batch([good, bad], path=str(tmp_path)))

    assert payload["results"][0] == {"id": "good", "kind": "search", "result": standalone}
    assert payload["results"][1]["error"]["code"] == "invalid_input"
    assert payload["result_incomplete"] is True


def test_tg_batch_timeout_does_not_accumulate_active_work_across_mcp_calls(monkeypatch):
    release = threading.Event()
    four_started = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def blocked_search(**kwargs: object) -> str:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                four_started.set()
        release.wait(5)
        with lock:
            active -= 1
        return json.dumps({"pattern": kwargs["pattern"]})

    monkeypatch.setattr(mcp_server, "tg_search", blocked_search)
    queries = [{"id": str(index), "kind": "search", "pattern": str(index)} for index in range(4)]
    try:
        first = asyncio.run(
            mcp_server.mcp.call_tool("tg_batch", {"queries": queries, "item_deadline": 0.1})
        )
        assert four_started.is_set()
        second = asyncio.run(
            mcp_server.mcp.call_tool("tg_batch", {"queries": queries, "item_deadline": 0.1})
        )
        assert isinstance(first, tuple)
        assert isinstance(second, tuple)
        assert all(
            item["error"]["code"] == "deadline_exceeded"
            for wire in (first, second)
            for item in _payload(wire[0][0].text)["results"]
        )
        assert maximum <= 4, f"timed-out batch work accumulated {maximum} active workers"
    finally:
        release.set()


@pytest.mark.parametrize(
    "queries",
    [
        [{"id": str(index), "kind": "search", "pattern": "x"} for index in range(17)],
        [
            {"id": "same", "kind": "search", "pattern": "x"},
            {"id": "same", "kind": "search", "pattern": "y"},
        ],
    ],
)
def test_tg_batch_rejects_over_limit_or_duplicate_ids(queries):
    result = asyncio.run(mcp_server.mcp.call_tool("tg_batch", {"queries": queries}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True
    payload = _payload(result.content[0].text)
    assert payload["error"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    "queries",
    [
        [None],
        {},
        [{"id": "x", "kind": "search", "pattern": "a"}, None],
    ],
)
def test_tg_batch_wire_malformed_queries_return_invalid_input_envelope(queries: object) -> None:
    result = asyncio.run(mcp_server.mcp.call_tool("tg_batch", {"queries": queries}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True
    payload = _payload(result.content[0].text)
    assert payload["error"]["code"] == "invalid_input"


def test_tg_batch_confines_each_item_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    result = _payload(
        mcp_server.tg_batch(
            [
                {"id": "bad", "kind": "search", "pattern": "x", "path": "../.."},
                {"id": "good", "kind": "search", "pattern": "y"},
            ],
            path=str(tmp_path),
        )
    )

    assert result["results"][0]["error"]["code"] == "invalid_input"
    assert result["results"][1]["result"]["pattern"] == "y"
    assert result["result_incomplete"] is True


def test_tg_batch_item_exception_is_visible_to_client(monkeypatch):
    def failing_search(**kwargs: object) -> str:
        raise RuntimeError("private worker detail")

    monkeypatch.setattr(mcp_server, "tg_search", failing_search)
    wire = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_batch", {"queries": [{"id": "bad", "kind": "search", "pattern": "x"}]}
        )
    )

    assert isinstance(wire, tuple)
    payload = _payload(wire[0][0].text)
    assert payload["results"][0]["error"]["code"] == "internal_error"
    assert "private worker detail" not in wire[0][0].text


def test_tg_batch_times_out_slow_item_while_completing_other_items(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_search(pattern: str, path: str = ".", **kwargs: object) -> str:
        if pattern == "slow":
            started.set()
            release.wait(2)
        return json.dumps({"pattern": pattern})

    monkeypatch.setattr(mcp_server, "tg_search", fake_search)
    try:
        result = _payload(
            mcp_server.tg_batch(
                [
                    {"id": "slow", "kind": "search", "pattern": "slow"},
                    {"id": "fast", "kind": "search", "pattern": "fast"},
                ],
                item_deadline=0.05,
            )
        )
    finally:
        release.set()

    assert result["results"][0]["error"]["code"] == "deadline_exceeded"
    assert result["results"][1]["result"] == {"pattern": "fast"}
    assert result["result_incomplete"] is True
    assert started.is_set()


def test_tg_batch_never_runs_more_than_four_items_at_once(monkeypatch):
    four_started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_search(pattern: str, path: str = ".", **kwargs: object) -> str:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                four_started.set()
        release.wait(2)
        with lock:
            active -= 1
        return json.dumps({"pattern": pattern})

    monkeypatch.setattr(mcp_server, "tg_search", fake_search)
    queries = [{"id": str(index), "kind": "search", "pattern": str(index)} for index in range(8)]
    with ThreadPoolExecutor(max_workers=1) as outer:
        future = outer.submit(mcp_server.tg_batch, queries, item_deadline=5)
        assert four_started.wait(2)
        release.set()
        result = _payload(future.result(timeout=3))

    assert maximum == 4
    assert len(result["results"]) == 8


def test_tg_batch_rejects_real_search_completed_after_item_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    real_search = mcp_server.tg_search
    search_completed = threading.Event()
    release = threading.Event()
    advance_clock = threading.Event()
    real_add_done_callback = Future.add_done_callback

    def completed_late_search(**kwargs: object) -> str:
        result = real_search(**kwargs)
        search_completed.set()
        assert release.wait(2), "real search was not released"
        return result

    def collect_after_completion(self: Future, callback: object) -> None:
        assert search_completed.wait(2), "real search did not reach the deadline handshake"
        advance_clock.set()
        release.set()
        self.result(timeout=2)
        real_add_done_callback(self, callback)

    monkeypatch.setattr(mcp_server, "tg_search", completed_late_search)
    monkeypatch.setattr(
        mcp_batch_tools,
        "time",
        SimpleNamespace(monotonic=lambda: time.monotonic() + int(advance_clock.is_set())),
    )
    monkeypatch.setattr(Future, "add_done_callback", collect_after_completion)
    payload = _payload(
        mcp_server.tg_batch(
            [{"id": "late", "kind": "search", "pattern": "needle"}],
            path=str(tmp_path),
            item_deadline=0.001,
        )
    )

    assert payload["results"][0]["error"]["code"] == "deadline_exceeded", payload


def test_tg_batch_plain_text_search_timeout_sets_result_incomplete(monkeypatch) -> None:
    def timed_out_plain_search(**kwargs: object) -> str:
        del kwargs
        return (
            "Found 1 matches across 1 files:\n"
            "Routing: backend=RustBackend reason=default gpu_device_ids=[] "
            "gpu_chunk_plan_mb=[] distributed=False workers=0\n"
            "\na.log:\n  1: needle\n"
            "\nnative search exceeded the 30s timeout and was stopped; "
            "returning partial results. Scope the search to a smaller path."
        )

    monkeypatch.setattr(mcp_server, "tg_search", timed_out_plain_search)
    payload = _payload(
        mcp_server.tg_batch([
            {
                "id": "plain-timeout",
                "kind": "search",
                "pattern": "needle",
                "structured_json": False,
            }
        ])
    )

    result = payload["results"][0]
    assert payload["result_incomplete"] is True, payload
    assert result.get("result_incomplete") is True, result


@pytest.mark.parametrize("structured_json", [True, False])
def test_tg_batch_preserves_real_search_omission_signal(tmp_path, monkeypatch, structured_json):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n" * 5, encoding="utf-8")
    item = {
        "id": "capped",
        "kind": "search",
        "pattern": "needle",
        "max_results": 1,
        "structured_json": structured_json,
    }
    payload = _payload(mcp_server.tg_batch([item], path=str(tmp_path)))
    result = payload["results"][0]

    assert payload["result_incomplete"] is True, payload
    assert result.get("result_incomplete") is True, result
    if structured_json:
        assert result["result"]["truncated"] is True
        assert result["result"]["omitted_matches"] > 0
    else:
        assert "output truncated" in result["result"]


def test_tg_batch_failed_text_search_spawns_once_like_standalone(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    real_run = ripgrep_backend.run_subprocess
    calls = 0

    def counted_run(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(ripgrep_backend, "run_subprocess", counted_run)
    standalone = mcp_server.tg_search("[", path=str(tmp_path), structured_json=False)
    assert calls == 1, f"standalone invalid regex used {calls} subprocesses: {standalone}"

    calls = 0
    payload = _payload(
        mcp_server.tg_batch(
            [{"id": "bad", "kind": "search", "pattern": "[", "structured_json": False}],
            path=str(tmp_path),
        )
    )
    assert calls == 1, f"batch invalid regex used {calls} subprocesses: {payload}"
    assert payload["results"][0]["error"]["code"] == "invalid_input"


def test_tg_batch_settles_executor_threads_after_real_search(tmp_path):
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    script = """
import json
import os
import sys
import threading
from tensor_grep.cli import mcp_server
from tensor_grep.cli import mcp_batch_tools
os.environ['TG_MCP_ROOT'] = sys.argv[1]
settled = threading.Event()
real_release = mcp_batch_tools._release_batch_slot
def release_and_signal(future):
    real_release(future)
    settled.set()
mcp_batch_tools._release_batch_slot = release_and_signal
payload = json.loads(mcp_server.tg_batch([{'id': 'one', 'kind': 'search', 'pattern': 'needle'}], path=sys.argv[1]))
assert payload['results'][0]['result']['total_matches'] == 1, payload
assert settled.wait(2), 'batch worker did not settle'
workers = [thread for thread in threading.enumerate() if thread.name.startswith('tg-mcp-batch')]
for worker in workers:
    worker.join(timeout=2)
live = [worker.name for worker in workers if worker.is_alive()]
assert not live, f'batch worker threads remained after search completed: {live}'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=os.environ.copy(),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
