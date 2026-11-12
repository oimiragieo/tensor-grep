"""Private JSON-only entry point for one allowlisted MCP batch item."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, cast

MAX_WORKER_INPUT_BYTES = 64 * 1024 * 1024
MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
_EXTERNAL_PROVIDER_KINDS = {"defs", "refs", "callers", "source"}

if not __package__:
    # Direct isolated launch has no import path from cwd or PYTHONPATH; use this worker's package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_WORKER_INPUT_BYTES + 1)
    if len(raw) > MAX_WORKER_INPUT_BYTES:
        raise ValueError("batch worker input too large")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("item"), dict)
        or not isinstance(value.get("path"), str)
        or not isinstance(value.get("paths_defaulted"), bool)
        or not isinstance(value.get("whole_tree_containment"), bool)
        or not isinstance(value.get("deadline_end"), (int, float))
        or isinstance(value.get("deadline_end"), bool)
    ):
        raise ValueError("invalid batch worker request")
    return value


def _run(request: dict[str, Any]) -> dict[str, object]:
    from tensor_grep.cli import mcp_batch_tools, mcp_search_scope, mcp_server

    item = request["item"]
    if item.get("kind") not in mcp_batch_tools._KIND_TO_TOOL:
        raise ValueError("unsupported batch worker kind")
    provider = item.get("provider", "native")
    # POSIX LSP children start a separate session; only Windows Job Objects own both trees.
    if (
        not request["whole_tree_containment"]
        and item["kind"] in _EXTERNAL_PROVIDER_KINDS
        and isinstance(provider, str)
        and provider.strip().lower() in {"lsp", "hybrid"}
    ):
        return mcp_batch_tools._error_item(
            str(item["id"]),
            str(item["kind"]),
            "internal_error",
            "external LSP provider cannot be contained by batch on this platform",
        )
    deadline_end = float(request["deadline_end"])
    effective_path = item.get("path", request["path"])
    if isinstance(effective_path, str):
        result = mcp_search_scope.call(
            lambda path: json.dumps(
                mcp_batch_tools._run_item(
                    mcp_server, item, request["path"], deadline_end - time.monotonic()
                )
            ),
            path=effective_path,
            paths_defaulted=request["paths_defaulted"],
        )
        return cast(dict[str, object], json.loads(result))
    return mcp_batch_tools._run_item(
        mcp_server, item, request["path"], deadline_end - time.monotonic()
    )


def _write(fd: int, value: dict[str, object]) -> None:
    from tensor_grep.cli.mcp_batch_json import (
        JsonByteLimitError,
        check_json_size,
        iter_json_bytes,
    )

    try:
        check_json_size(value, MAX_WORKER_OUTPUT_BYTES)
    except JsonByteLimitError:
        item = value
        value = {
            "id": item.get("id", ""),
            "kind": item.get("kind", ""),
            "error": {
                "code": "internal_error",
                "message": "tool result exceeded the batch worker output limit",
            },
        }
    for chunk in iter_json_bytes(value, MAX_WORKER_OUTPUT_BYTES):
        view = memoryview(chunk)
        while view:
            written = os.write(fd, view)
            view = view[written:]


def main() -> int:
    # Keep protocol JSON separate from tool stdout. The parent drains stderr with a fixed cap,
    # preserving existing tool warnings without allowing diagnostics to fill an unbounded buffer.
    result_fd = os.dup(sys.stdout.fileno())
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 1)
    os.close(devnull)
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    try:
        request = _request()
        result = _run(request)
        _write(result_fd, result)
        return 0
    except BaseException:
        return 1
    finally:
        os.close(result_fd)


if __name__ == "__main__":
    raise SystemExit(main())
