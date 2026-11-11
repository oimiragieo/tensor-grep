"""Exercise batch result envelopes through the real MCP stdio transport."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from textwrap import dedent

import pytest
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.integration


def _wait_for_files(paths: list[Path], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            return
        time.sleep(0.02)
    raise AssertionError(f"worker start handshake timed out: {paths}")


def test_batch_preserves_direct_results_and_partial_errors_over_stdio(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "sample.txt").write_text("needle_audit_transport\n", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "TG_MCP_ROOT": str(tmp_path),
        "TG_MCP_LEGACY_TOOLS": "1",
    }

    async def exercise() -> None:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tensor_grep", "mcp"],
            cwd=root,
            env=env,
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=90)
            ) as session:
                await session.initialize()
                arguments = {
                    "path": str(tmp_path),
                    "pattern": "needle_audit_transport",
                    "structured_json": False,
                }
                direct = await session.call_tool("tg_search", arguments)
                assert not direct.isError
                batch = await session.call_tool(
                    "tg_batch",
                    {
                        "path": str(tmp_path),
                        "item_deadline": 30,
                        "deadline": 60,
                        "queries": [
                            {"id": "good", "kind": "search", **arguments},
                            {
                                "id": "bad",
                                "kind": "search",
                                **arguments,
                                "not_a_tool_argument": True,
                            },
                        ],
                    },
                )
                assert not batch.isError
                payload = json.loads(batch.content[0].text)
                good, bad = payload["results"]
                assert good == {
                    "id": "good",
                    "kind": "search",
                    "result": direct.content[0].text,
                }
                assert bad["id"] == "bad"
                assert bad["error"]["code"] == "invalid_input"
                assert payload["result_incomplete"] is True

    asyncio.run(exercise())


def test_cancelling_batch_over_stdio_reaps_workers_and_restores_capacity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    marker_dir = tmp_path / "started"
    marker_dir.mkdir()
    worker = tmp_path / "controlled_batch_worker.py"
    worker.write_text(
        dedent(
            f"""
            import json
            import os
            import subprocess
            import sys
            import time
            from pathlib import Path

            sys.path.insert(0, {str(root / "src")!r})
            from tensor_grep.cli import mcp_batch_worker

            def run(request):
                item = request["item"]
                pattern = item["pattern"]
                if pattern.startswith("block-"):
                    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
                    marker = Path({str(marker_dir)!r}) / f"{{pattern}}.json"
                    marker.write_text(json.dumps({{"worker": os.getpid(), "descendant": child.pid}}), encoding="utf-8")
                    time.sleep(60)
                return {{"id": item["id"], "kind": item["kind"], "result": {{"pattern": pattern}}}}

            mcp_batch_worker._run = run
            raise SystemExit(mcp_batch_worker.main())
            """
        ),
        encoding="utf-8",
    )
    done_marker = tmp_path / "batch_done"
    server_entry = tmp_path / "mcp_server.py"
    server_entry.write_text(
        dedent(
            f"""
            import sys
            from pathlib import Path

            sys.path.insert(0, {str(root / "src")!r})
            from tensor_grep.cli import mcp_batch_supervisor, mcp_batch_tools, mcp_server

            mcp_batch_supervisor._WORKER_PATH = Path({str(worker)!r})
            run_batch = mcp_batch_tools.run_tg_batch

            def observe_batch(*args, **kwargs):
                try:
                    return run_batch(*args, **kwargs)
                finally:
                    Path({str(done_marker)!r}).write_text("done", encoding="utf-8")

            mcp_batch_tools.run_tg_batch = observe_batch
            mcp_server.mcp.run(transport="stdio")
            """
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "TG_MCP_ROOT": str(tmp_path),
        "TG_MCP_LEGACY_TOOLS": "1",
    }

    async def exercise() -> None:
        server = StdioServerParameters(
            command=sys.executable,
            args=[str(server_entry)],
            cwd=root,
            env=env,
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=90)
            ) as session:
                await session.initialize()
                request_id = session._request_id
                pending = asyncio.create_task(
                    session.call_tool(
                        "tg_batch",
                        {
                            "path": str(tmp_path),
                            "item_deadline": 30,
                            "deadline": 60,
                            "queries": [
                                {
                                    "id": f"block-{index}",
                                    "kind": "search",
                                    "pattern": f"block-{index}",
                                }
                                for index in range(4)
                            ],
                        },
                    )
                )
                marker_paths = [marker_dir / f"block-{index}.json" for index in range(4)]
                await asyncio.to_thread(_wait_for_files, marker_paths, 10.0)
                cancel_started = time.monotonic()
                await session.send_notification(
                    types.ClientNotification(
                        types.CancelledNotification(
                            params=types.CancelledNotificationParams(
                                requestId=request_id,
                                reason="transport cancellation regression",
                            )
                        )
                    )
                )
                cleanup_end = cancel_started + 2.0
                while not done_marker.exists() and time.monotonic() < cleanup_end:
                    await asyncio.sleep(0.01)
                try:
                    assert done_marker.exists(), (
                        "cancelled batch did not finish worker cleanup in 2s"
                    )
                finally:
                    if not pending.done():
                        pending.cancel()
                    try:
                        await pending
                    except BaseException:
                        pass

                recovered = await session.call_tool(
                    "tg_batch",
                    {
                        "path": str(tmp_path),
                        "item_deadline": 5,
                        "deadline": 10,
                        "queries": [
                            {
                                "id": f"recovered-{index}",
                                "kind": "search",
                                "pattern": f"recovered-{index}",
                            }
                            for index in range(4)
                        ],
                    },
                )
                payload = json.loads(recovered.content[0].text)
                assert [item["result"]["pattern"] for item in payload["results"]] == [
                    f"recovered-{index}" for index in range(4)
                ]

    asyncio.run(exercise())
