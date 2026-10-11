"""Execution helpers for the bounded MCP batch tool."""

import json
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, NoReturn, cast

_KIND_TO_TOOL = {
    "search": "tg_search",
    "find": "tg_find",
    "defs": "tg_symbol_defs",
    "refs": "tg_symbol_refs",
    "callers": "tg_symbol_callers",
    "source": "tg_symbol_source",
}


class _BatchRejected(Exception):
    pass


def _error_item(item_id: str, kind: str, code: str, message: str) -> dict[str, object]:
    return {"id": item_id, "kind": kind, "error": {"code": code, "message": message}}


def _result_is_incomplete(result: dict[str, object]) -> bool:
    if "error" in result:
        return True
    payload = result.get("result")
    if not isinstance(payload, dict):
        return False
    if payload.get("result_incomplete") is True:
        return True
    incomplete = payload.get("incomplete")
    return isinstance(incomplete, dict) and incomplete.get("status") is True


def _reject(message: str) -> NoReturn:
    raise _BatchRejected(
        json.dumps({"version": 1, "error": {"code": "invalid_input", "message": message}})
    )


def _validate(queries: object, item_deadline: float, deadline: float) -> list[dict[str, object]]:
    if not isinstance(queries, list) or len(queries) > 16:
        _reject("queries must be a list containing at most 16 items")
    if not 0 < item_deadline <= 120 or not 0 < deadline <= 120:
        _reject("item_deadline and deadline must be greater than 0 and at most 120 seconds")
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(cast(list[object], queries)):
        if not isinstance(raw, dict):
            _reject(f"queries[{index}] must be an object")
        item = cast(dict[str, object], raw)
        item_id, kind = item.get("id"), item.get("kind")
        if not isinstance(item_id, str) or not item_id.strip() or item_id in seen:
            _reject(f"queries[{index}].id must be a unique non-empty string")
        if not isinstance(kind, str) or kind not in _KIND_TO_TOOL:
            _reject(f"queries[{index}].kind is unsupported")
        seen.add(item_id)
        items.append(item)
    return items


def _run_item(server: Any, item: dict[str, object], common_path: str) -> dict[str, object]:
    item_id = str(item["id"])
    kind = str(item["kind"])
    tool_name = _KIND_TO_TOOL[kind]
    arguments = {key: value for key, value in item.items() if key not in {"id", "kind"}}
    arguments.setdefault("path", common_path)
    try:
        payload = json.loads(getattr(server, tool_name)(**arguments))
    except TypeError:
        return _error_item(item_id, kind, "invalid_input", "tool arguments do not match its schema")
    except Exception:
        return _error_item(item_id, kind, "internal_error", "tool execution failed")
    if not isinstance(payload, dict):
        return _error_item(
            item_id, kind, "internal_error", "tool returned a non-object JSON result"
        )
    error = payload.get("error")
    if isinstance(error, dict):
        return _error_item(
            item_id,
            kind,
            str(error.get("code", "internal_error")),
            str(error.get("message", "tool execution failed")),
        )
    return {"id": item_id, "kind": kind, "result": payload}


def run_tg_batch(
    server: Any,
    queries: object,
    path: str = ".",
    citations: bool = False,
    item_deadline: float = 10.0,
    deadline: float = 30.0,
) -> str:
    try:
        items = _validate(queries, item_deadline, deadline)
    except _BatchRejected as exc:
        payload = json.loads(str(exc))
        payload["mcp_contract_version"] = server._TG_MCP_SERVER_CONTRACT_VERSION
        return json.dumps(payload, indent=2)
    del citations
    started = time.monotonic()
    overall_end = started + deadline
    result_slots: list[dict[str, object] | None] = [None] * len(items)
    futures: dict[Future[dict[str, object]], tuple[int, float, str, str]] = {}
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg-mcp-batch")
    try:
        for index, item in enumerate(items):
            now = time.monotonic()
            if now >= overall_end:
                result_slots[index] = _error_item(
                    str(item["id"]),
                    str(item["kind"]),
                    "deadline_exceeded",
                    "overall batch deadline exceeded",
                )
                continue
            item_end = min(now + item_deadline, overall_end)
            future = executor.submit(_run_item, server, item, path)
            futures[future] = (index, item_end, str(item["id"]), str(item["kind"]))

        pending = set(futures)
        while pending:
            now = time.monotonic()
            completed = {future for future in pending if future.done()}
            for future in completed:
                index, _, _, _ = futures[future]
                try:
                    result_slots[index] = future.result()
                except Exception:
                    _, _, item_id, kind = futures[future]
                    result_slots[index] = _error_item(
                        item_id, kind, "internal_error", "tool execution failed"
                    )
                pending.remove(future)
            now = time.monotonic()
            expired = [future for future in pending if now >= min(futures[future][1], overall_end)]
            for future in expired:
                index, _, item_id, kind = futures[future]
                result_slots[index] = _error_item(
                    item_id, kind, "deadline_exceeded", "item deadline exceeded"
                )
                future.cancel()
                pending.remove(future)
            if pending:
                next_end = min([overall_end, *(futures[future][1] for future in pending)])
                wait(
                    pending,
                    timeout=max(0.0, next_end - time.monotonic()),
                    return_when=FIRST_COMPLETED,
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    results = [result for result in result_slots if result is not None]
    return json.dumps(
        {
            "version": 1,
            "mcp_contract_version": server._TG_MCP_SERVER_CONTRACT_VERSION,
            "results": results,
            "result_incomplete": any(_result_is_incomplete(result) for result in results),
        },
        indent=2,
    )


def tg_batch(
    queries: list[dict[str, object]],
    path: str = ".",
    citations: bool = False,
    item_deadline: float = 10.0,
    deadline: float = 30.0,
) -> str:
    """Run up to 16 independent search and symbol-navigation requests concurrently.

    Invalid top-level requests raise an MCP tool error. Per-item refusals and execution errors
    are returned in the corresponding result entry, and set `result_incomplete=true`.
    `item_deadline` bounds each item, while `deadline` bounds the whole call; each is capped at
    120 seconds. `citations` is accepted for client compatibility; the underlying tools' JSON
    payloads, including their existing source locations, are returned unchanged.
    """
    return run_tg_batch(
        sys.modules["tensor_grep.cli.mcp_server"], queries, path, citations, item_deadline, deadline
    )
