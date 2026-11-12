"""Execution helpers for the bounded MCP batch tool."""

import json
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Annotated, Any, NoReturn, cast

import anyio
from pydantic import Field

from tensor_grep.cli import mcp_search_scope
from tensor_grep.cli.mcp_batch_supervisor import (
    _MAX_WORKER_DIAGNOSTIC_BYTES,
    _WORKER_OPERATION_ERRORS,
    WorkerRun,
    run_contained_worker,
)
from tensor_grep.cli.mcp_search_bounds import mcp_plain_search_output_is_incomplete

BatchQueriesWire = Annotated[
    object,
    Field(
        json_schema_extra={
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "maxItems": 16,
        },
    ),
]

_KIND_TO_TOOL = {
    "search": "tg_search",
    "find": "tg_find",
    "defs": "tg_symbol_defs",
    "refs": "tg_symbol_refs",
    "callers": "tg_symbol_callers",
    "source": "tg_symbol_source",
}
_DEADLINE_KINDS = {"find", "refs", "callers"}
_BATCH_SLOTS = threading.BoundedSemaphore(4)
_MAX_BATCH_RESPONSE_BYTES = 64 * 1024 * 1024
_BATCH_RESPONSE_LIMIT_MESSAGE = "tool result exceeded the batch response size limit"


class _BatchRejected(Exception):
    pass


def _error_item(item_id: str, kind: str, code: str, message: str) -> dict[str, object]:
    return {"id": item_id, "kind": kind, "error": {"code": code, "message": message}}


def _json_pretty_size(
    value: object,
    max_bytes: int,
    *,
    list_override: tuple[list[Any], int, object] | None = None,
) -> int | None:
    """Count `json.dumps(value, indent=2)` without building its encoded copy.

    Batch results are JSON-decoded values, so only JSON scalar/container types are accepted. The
    default encoder uses `ensure_ascii=True`, which makes its output ASCII; its character count is
    therefore also its UTF-8 byte count. `list_override` sizes one not-yet-retained item in place.
    """
    total = 0

    def add(size: int) -> bool:
        nonlocal total
        total += size
        return total <= max_bytes

    def add_string(text: str) -> bool:
        if not add(2):  # JSON quotes
            return False
        for character in text:
            codepoint = ord(character)
            if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                size = 2
            elif codepoint < 0x20:
                size = 6  # \u00xx
            elif codepoint <= 0x7E:
                size = 1
            elif codepoint <= 0xFFFF:
                size = 6  # \uxxxx, including a lone surrogate
            else:
                size = 12  # a UTF-16 surrogate pair, each encoded as \uxxxx
            if not add(size):
                return False
        return True

    def visit(current: object, depth: int) -> bool:
        if current is None:
            return add(4)
        if current is True:
            return add(4)
        if current is False:
            return add(5)
        if isinstance(current, str):
            return add_string(current)
        if isinstance(current, int):
            return add(len(str(current)))
        if isinstance(current, float):
            # Worker JSON rejects non-finite numbers. Retain json.dumps' default spellings for
            # defensive compatibility with direct unit callers of this private helper.
            token = (
                "NaN"
                if current != current
                else "Infinity"
                if current == float("inf")
                else "-Infinity"
                if current == float("-inf")
                else repr(current)
            )
            return add(len(token))
        if isinstance(current, list):
            if not current:
                return add(2)
            if not add(1):  # [
                return False
            for index, child in enumerate(current):
                if not add(1 + 2 * (depth + 1)):  # newline and nested indentation
                    return False
                if (
                    list_override is not None
                    and current is list_override[0]
                    and index == list_override[1]
                ):
                    child = list_override[2]
                if not visit(child, depth + 1):
                    return False
                if index + 1 < len(current) and not add(1):  # comma
                    return False
            return add(1 + 2 * depth + 1)  # newline, indentation, ]
        if isinstance(current, dict):
            if not current:
                return add(2)
            if not add(1):  # {
                return False
            for index, (key, child) in enumerate(current.items()):
                if not isinstance(key, str):
                    return False
                if not add(1 + 2 * (depth + 1)) or not add_string(key):
                    return False
                if not add(2):  # colon and default separator space
                    return False
                if not visit(child, depth + 1):
                    return False
                if index + 1 < len(current) and not add(1):
                    return False
            return add(1 + 2 * depth + 1)  # newline, indentation, }
        return False

    try:
        return total if visit(value, 0) else None
    except RecursionError:
        return None


def _response_payload(
    server: Any,
    results: list[dict[str, object]],
    *,
    result_incomplete: bool | None = None,
) -> dict[str, object]:
    return {
        "version": 1,
        "mcp_contract_version": server._TG_MCP_SERVER_CONTRACT_VERSION,
        "results": results,
        # False is the larger JSON literal and reserves the worst-case byte for the final value.
        "result_incomplete": (
            any(_result_is_incomplete(result) for result in results)
            if result_incomplete is None
            else result_incomplete
        ),
    }


def _response_limit_item(item: dict[str, object]) -> dict[str, object]:
    return _error_item(
        str(item["id"]), str(item["kind"]), "internal_error", _BATCH_RESPONSE_LIMIT_MESSAGE
    )


def _store_bounded_result(
    server: Any,
    results: list[dict[str, object]],
    index: int,
    candidate: dict[str, object],
    fallback: dict[str, object],
) -> None:
    """Store a result only if the complete final response remains within its byte budget."""
    size = _json_pretty_size(
        _response_payload(server, results, result_incomplete=False),
        _MAX_BATCH_RESPONSE_BYTES,
        list_override=(results, index, candidate),
    )
    results[index] = candidate if size is not None else fallback


def _result_is_incomplete(result: dict[str, object]) -> bool:
    if "error" in result or result.get("result_incomplete") is True:
        return True
    payload = result.get("result")
    if not isinstance(payload, dict):
        return False
    if any(
        payload.get(key) is True for key in ("result_incomplete", "truncated", "output_truncated")
    ):
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


def _run_item(
    server: Any, item: dict[str, object], common_path: str, remaining: float | None = None
) -> dict[str, object]:
    item_id = str(item["id"])
    kind = str(item["kind"])
    tool_name = _KIND_TO_TOOL[kind]
    arguments = {key: value for key, value in item.items() if key not in {"id", "kind"}}
    arguments.setdefault("path", common_path)
    if kind in _DEADLINE_KINDS and remaining is not None:
        caller_deadline = arguments.get("deadline")
        if caller_deadline is None:
            arguments["deadline"] = remaining
        elif (
            isinstance(caller_deadline, (int, float))
            and not isinstance(caller_deadline, bool)
            and caller_deadline > 0
        ):
            arguments["deadline"] = min(caller_deadline, remaining)
    try:
        output = getattr(server, tool_name)(**arguments)
        if kind == "search" and arguments.get("structured_json") is False:
            if output.startswith("Search failed: semantic backend error"):
                return _error_item(item_id, kind, "semantic_backend_error", output)
            if output.startswith("Search failed:"):
                return _error_item(
                    item_id, kind, "invalid_input", output.removeprefix("Search failed: ")
                )
            if output.startswith("tg_search failed:"):
                return _error_item(item_id, kind, "internal_error", output)
            if output.startswith("tg: "):
                return _error_item(item_id, kind, "broad_scan_refused", output)
            result: dict[str, object] = {"id": item_id, "kind": kind, "result": output}
            if mcp_plain_search_output_is_incomplete(output):
                result["result_incomplete"] = True
            return result
        else:
            payload = json.loads(output)
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
    result = {"id": item_id, "kind": kind, "result": payload}
    if _result_is_incomplete(result):
        result["result_incomplete"] = True
    return result


def _supervise_item(
    item: dict[str, object],
    common_path: str,
    paths_defaulted: bool,
    item_end: float,
    cancel_event: threading.Event | None = None,
) -> WorkerRun:
    return run_contained_worker(
        {
            "item": item,
            "path": common_path,
            "paths_defaulted": paths_defaulted,
            "deadline_end": item_end,
        },
        item_end,
        cancel_event,
    )


def _worker_outcome_item(
    outcome: WorkerRun, item_id: str, kind: str, item_end: float
) -> dict[str, object]:
    if outcome.cleanup_failed or not outcome.tree_reaped:
        return _error_item(
            item_id,
            kind,
            "internal_error",
            "worker cleanup failed; batch capacity remains reserved",
        )
    if outcome.input_too_large:
        return _error_item(
            item_id, kind, "invalid_input", "tool arguments exceed the batch worker limit"
        )
    if outcome.timed_out or outcome.finished_at >= item_end:
        return _error_item(item_id, kind, "deadline_exceeded", "item deadline exceeded")
    if outcome.output_overflow or outcome.exit_code != 0 or outcome.output is None:
        return _error_item(item_id, kind, "internal_error", "tool execution failed")
    try:
        result = json.loads(outcome.output)
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = None
    if (
        not isinstance(result, dict)
        or result.get("id") != item_id
        or result.get("kind") != kind
        or not (isinstance(result.get("error"), dict) or "result" in result)
    ):
        return _error_item(item_id, kind, "internal_error", "tool execution failed")
    return cast(dict[str, object], result)


def _forward_worker_diagnostics(outcome: WorkerRun) -> None:
    """Forward bounded tool warnings to MCP server stderr, as direct handlers did before."""
    if not outcome.diagnostics and not outcome.diagnostics_truncated:
        return
    message = outcome.diagnostics.decode("utf-8", errors="replace")
    marker = "\n[tensor-grep MCP batch worker diagnostics truncated after 65536 bytes]\n"
    encoded = message.encode("utf-8")
    marker_bytes = marker.encode("utf-8")
    truncated = outcome.diagnostics_truncated
    prefix_limit = _MAX_WORKER_DIAGNOSTIC_BYTES - len(marker_bytes)
    if len(encoded) > prefix_limit:
        truncated = True
    if truncated:
        message = encoded[:prefix_limit].decode("utf-8", errors="ignore") + marker
    try:
        sys.stderr.write(message)
        sys.stderr.flush()
    except (OSError, ValueError):
        # Diagnostics must not replace the tool's result when the server log stream is closing.
        pass


def run_tg_batch(
    server: Any,
    queries: object,
    path: str = ".",
    citations: bool = False,
    item_deadline: float = 10.0,
    deadline: float = 30.0,
    *,
    cancel_event: threading.Event | None = None,
) -> str:
    try:
        items = _validate(queries, item_deadline, deadline)
    except _BatchRejected as exc:
        payload = json.loads(str(exc))
        payload["mcp_contract_version"] = server._TG_MCP_SERVER_CONTRACT_VERSION
        return json.dumps(payload, indent=2)
    del citations
    fallback_results = [_response_limit_item(item) for item in items]
    if (
        _json_pretty_size(
            _response_payload(server, fallback_results, result_incomplete=False),
            _MAX_BATCH_RESPONSE_BYTES,
        )
        is None
    ):
        # Preserve the existing top-level invalid-input envelope when caller IDs alone cannot fit.
        return json.dumps(
            {
                "version": 1,
                "mcp_contract_version": server._TG_MCP_SERVER_CONTRACT_VERSION,
                "error": {
                    "code": "invalid_input",
                    "message": "query IDs exceed the batch response size limit",
                },
            },
            indent=2,
        )
    started = time.monotonic()
    overall_end = started + deadline
    result_slots = list(fallback_results)
    futures: dict[int, Future[WorkerRun]] = {}
    queued_results: dict[int, dict[str, object]] = {}
    item_ends: dict[int, float] = {}
    held_slots: set[int] = set()
    slots = _BATCH_SLOTS
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg-mcp-batch-supervisor")
    next_to_schedule = 0
    next_to_consume = 0

    def consume_ready_prefix() -> bool:
        """Consume completed outcomes in request order before releasing their global slots."""
        nonlocal next_to_consume
        progressed = False
        while next_to_consume < len(items):
            index = next_to_consume
            queued = queued_results.pop(index, None)
            if queued is not None:
                _store_bounded_result(server, result_slots, index, queued, fallback_results[index])
                next_to_consume += 1
                progressed = True
                continue
            future = futures.get(index)
            if future is None or not future.done():
                break
            item = items[index]
            item_id, kind = str(item["id"]), str(item["kind"])
            tree_reaped = False
            try:
                outcome = future.result()
            except _WORKER_OPERATION_ERRORS:
                candidate = _error_item(
                    item_id,
                    kind,
                    "internal_error",
                    "worker cleanup could not be confirmed; batch capacity remains reserved",
                )
            else:
                _forward_worker_diagnostics(outcome)
                candidate = _worker_outcome_item(outcome, item_id, kind, item_ends[index])
                tree_reaped = outcome.tree_reaped
            _store_bounded_result(server, result_slots, index, candidate, fallback_results[index])
            del futures[index]
            if tree_reaped:
                if index in held_slots:
                    slots.release()
                    held_slots.remove(index)
            next_to_consume += 1
            progressed = True
        return progressed

    try:
        while next_to_consume < len(items):
            if cancel_event is not None and cancel_event.is_set():
                break
            if consume_ready_prefix():
                continue

            if next_to_schedule < len(items):
                index = next_to_schedule
                item = items[index]
                now = time.monotonic()
                if index not in item_ends:
                    item_ends[index] = min(now + item_deadline, overall_end)
                item_end = item_ends[index]
                if now >= overall_end:
                    queued_results[index] = _error_item(
                        str(item["id"]),
                        str(item["kind"]),
                        "deadline_exceeded",
                        "overall batch deadline exceeded",
                    )
                    next_to_schedule += 1
                    continue
                if now >= item_end:
                    queued_results[index] = _error_item(
                        str(item["id"]),
                        str(item["kind"]),
                        "deadline_exceeded",
                        "item deadline exceeded",
                    )
                    next_to_schedule += 1
                    continue
                if slots.acquire(blocking=False):
                    held_slots.add(index)
                    if time.monotonic() >= item_end:
                        slots.release()
                        held_slots.remove(index)
                        queued_results[index] = _error_item(
                            str(item["id"]),
                            str(item["kind"]),
                            "deadline_exceeded",
                            "item deadline exceeded",
                        )
                        next_to_schedule += 1
                        continue
                    try:
                        effective_path = item.get("path", path)
                        paths_defaulted = (
                            mcp_search_scope.paths_defaulted(effective_path)
                            if isinstance(effective_path, str)
                            else False
                        )
                        future = executor.submit(
                            _supervise_item, item, path, paths_defaulted, item_end, cancel_event
                        )
                    except Exception:
                        slots.release()
                        held_slots.remove(index)
                        queued_results[index] = _error_item(
                            str(item["id"]),
                            str(item["kind"]),
                            "internal_error",
                            "tool execution failed",
                        )
                    else:
                        futures[index] = future
                    next_to_schedule += 1
                    continue

            # Wait briefly for the next ordered result or a global slot. The item deadline above
            # continues to include this queue wait, while completed later results remain capped by
            # the four globally owned slots.
            waiting_future = futures.get(next_to_consume)
            if waiting_future is not None and not waiting_future.done():
                wait({waiting_future}, timeout=0.05, return_when=FIRST_COMPLETED)
            else:
                time.sleep(0.05)
    finally:
        for future in futures.values():
            if not future.done():
                future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        for index, future in list(futures.items()):
            if future.cancelled():
                if index in held_slots:
                    slots.release()
                    held_slots.remove(index)
                continue
            try:
                outcome = future.result()
            except _WORKER_OPERATION_ERRORS:
                continue
            if outcome.tree_reaped and index in held_slots:
                slots.release()
                held_slots.remove(index)

    payload = _response_payload(server, result_slots)
    response_size = _json_pretty_size(payload, _MAX_BATCH_RESPONSE_BYTES)
    if response_size is None:
        # Defensive fallback for an encoder-accounting defect; all preflight fallback IDs fit.
        payload = _response_payload(server, fallback_results, result_incomplete=True)
    response = json.dumps(payload, indent=2)
    if len(response) > _MAX_BATCH_RESPONSE_BYTES:
        # ensure_ascii=True makes len(response) the UTF-8 byte count without an extra copy.
        payload = {
            "version": 1,
            "mcp_contract_version": server._TG_MCP_SERVER_CONTRACT_VERSION,
            "error": {
                "code": "internal_error",
                "message": "batch response size accounting failed",
            },
        }
        response = json.dumps(payload, indent=2)
    return response


async def tg_batch(
    queries: BatchQueriesWire,
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
    payloads and search plain text, including existing source locations, are returned unchanged.
    """
    cancelled = threading.Event()
    finished = anyio.Event()
    result: list[str] = []
    errors: list[BaseException] = []

    def execute() -> str:
        return run_tg_batch(
            sys.modules["tensor_grep.cli.mcp_server"],
            queries,
            path,
            citations,
            item_deadline,
            deadline,
            cancel_event=cancelled,
        )

    async def run() -> None:
        try:
            result.append(await anyio.to_thread.run_sync(execute))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    async with anyio.create_task_group() as workers:
        workers.start_soon(run)
        try:
            await finished.wait()
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            with anyio.CancelScope(shield=True):
                await finished.wait()
            raise
    if errors:
        raise errors[0]
    return result[0]
