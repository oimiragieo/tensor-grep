import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp import types

from tensor_grep.cli import mcp_server

NORMALIZED_VOLATILE_FIELDS: tuple[str, ...] = ()


def _payload(result: str) -> dict[str, object]:
    return json.loads(result)


def test_tg_batch_stamps_its_own_envelope_with_current_contract(monkeypatch):
    monkeypatch.setattr(mcp_server, "tg_search", lambda **kwargs: json.dumps({"ok": True}))

    payload = _payload(mcp_server.tg_batch([{"id": "one", "kind": "search", "pattern": "x"}]))

    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert payload["results"][0]["result"] == {"ok": True}


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
def test_tg_batch_preserves_standalone_json_for_each_kind(monkeypatch, kind, tool_name, args):
    def echo_arguments(**kwargs: object) -> str:
        return json.dumps({"forwarded": kwargs})

    monkeypatch.setattr(mcp_server, tool_name, echo_arguments)
    standalone = _payload(getattr(mcp_server, tool_name)(**args, path="."))

    result = _payload(mcp_server.tg_batch([{"id": "same", "kind": kind, **args}]))

    assert NORMALIZED_VOLATILE_FIELDS == ()
    assert result["results"] == [{"id": "same", "kind": kind, "result": standalone}]
    assert result["results"][0]["result"] == {"forwarded": {**args, "path": "."}}


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
