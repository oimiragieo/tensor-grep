import asyncio
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
from mcp import types

from tensor_grep.cli import (
    mcp_batch_supervisor,
    mcp_batch_tools,
    mcp_batch_worker,
    mcp_search_scope,
    mcp_server,
)


@pytest.fixture(autouse=True)
def _sync_direct_batch_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async_tool = mcp_server.tg_batch
    monkeypatch.setattr(
        mcp_server,
        "tg_batch",
        lambda *args, **kwargs: asyncio.run(async_tool(*args, **kwargs)),
    )


def _payload(result: str) -> dict[str, object]:
    return json.loads(result)


def _install_worker_driver(tmp_path: Path, body: str) -> Path:
    package_root = Path(mcp_batch_worker.__file__).resolve().parents[2]
    driver = tmp_path / "test_batch_worker_driver.py"
    driver.write_text(
        """
import json
import os
import subprocess
import sys
import time
from pathlib import Path

"""
        + f"sys.path.insert(0, {str(package_root)!r})\n"
        + "from tensor_grep.cli import mcp_batch_worker\n\n"
        + dedent(body)
        + "\nraise SystemExit(mcp_batch_worker.main())\n",
        encoding="utf-8",
    )
    return driver


def _track_contained_workers(monkeypatch, driver: Path | None = None):
    real_spawn = mcp_batch_supervisor.spawn_contained
    started = []
    checks = []

    def tracked_spawn(argv, **kwargs):
        if driver is not None:
            argv = [sys.executable, "-I", str(driver)]
        process, containment = real_spawn(argv, **kwargs)
        real_survivors = containment.survivors

        def checked_survivors(deadline):
            result = real_survivors(deadline)
            checks.append((process.pid, result))
            return result

        containment.survivors = checked_survivors
        started.append((process, containment))
        return process, containment

    monkeypatch.setattr(mcp_batch_supervisor, "spawn_contained", tracked_spawn)
    return started, checks


def _wait_for_files(paths: list[Path], timeout: float = 15.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if all(path.exists() for path in paths):
            return
        time.sleep(0.02)
    raise AssertionError(
        f"worker start handshake timed out: {[p.name for p in paths if not p.exists()]}"
    )


def test_tg_batch_discovery_schema_describes_queries_as_object_list() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool = next(item for item in tools if item.name == "tg_batch")
    queries_schema = tool.inputSchema["properties"]["queries"]

    assert queries_schema["type"] == "array"
    assert queries_schema["items"]["type"] == "object"
    assert queries_schema["items"].get("additionalProperties") is True
    assert queries_schema.get("maxItems") == 16


def test_batch_response_size_counter_matches_pretty_json_for_unicode_and_escapes():
    value = {
        "nested": [
            "plain text",
            {
                "unicode": "café 🧪",
                "escaped": 'quote " slash \\ newline\n tab\t control\x01 del\x7f',
            },
        ],
        "empty_object": {},
        "empty_list": [],
    }
    expected_size = len(json.dumps(value, indent=2))

    assert mcp_batch_tools._json_pretty_size(value, expected_size) == expected_size
    assert mcp_batch_tools._json_pretty_size(value, expected_size - 1) is None


def test_bounded_result_is_not_retained_when_it_exceeds_the_budget(monkeypatch):
    monkeypatch.setattr(mcp_batch_tools, "_MAX_BATCH_RESPONSE_BYTES", 512)
    item = {"id": "source", "kind": "source"}
    fallback = mcp_batch_tools._response_limit_item(item)
    results = [fallback]
    candidate = {
        "id": "source",
        "kind": "source",
        "result": {"sources": [{"source": "S" * 700}]},
    }

    mcp_batch_tools._store_bounded_result(mcp_server, results, 0, candidate, fallback)

    assert results[0] is fallback
    assert all(value is not candidate for value in results)


def test_batch_oversized_symbol_source_becomes_item_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_batch_tools, "_MAX_BATCH_RESPONSE_BYTES", 512)
    driver = _install_worker_driver(
        tmp_path,
        """
        def large_source_run(request):
            item = request["item"]
            return {
                "id": item["id"],
                "kind": item["kind"],
                "result": {"sources": [{"source": "S" * 700}]},
            }
        mcp_batch_worker._run = large_source_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    response = mcp_server.tg_batch(
        [{"id": "large-source", "kind": "source", "symbol": "target"}],
        path=str(tmp_path),
    )
    payload = _payload(response)

    assert len(response.encode("utf-8")) <= 512
    assert payload["results"][0]["id"] == "large-source"
    assert payload["results"][0]["error"] == {
        "code": "internal_error",
        "message": "tool result exceeded the batch response size limit",
    }
    assert payload["result_incomplete"] is True


def test_batch_final_response_exact_budget_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    item = {"id": "boundary", "kind": "search", "pattern": "x"}
    result_item = {"id": "boundary", "kind": "search", "result": {"text": "s" * 400}}
    expected = json.dumps(
        {
            "version": 1,
            "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
            "results": [result_item],
            "result_incomplete": False,
        },
        indent=2,
    )
    driver = _install_worker_driver(
        tmp_path,
        """
        def boundary_run(request):
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"text": "s" * 400}}
        mcp_batch_worker._run = boundary_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)
    monkeypatch.setattr(mcp_batch_tools, "_MAX_BATCH_RESPONSE_BYTES", len(expected))

    exact = mcp_server.tg_batch([item], path=str(tmp_path))

    assert exact == expected
    assert len(exact.encode("utf-8")) == len(expected)

    monkeypatch.setattr(mcp_batch_tools, "_MAX_BATCH_RESPONSE_BYTES", len(expected) - 1)
    over = _payload(mcp_server.tg_batch([item], path=str(tmp_path)))
    assert over["results"][0]["error"]["code"] == "internal_error"
    assert "response size limit" in over["results"][0]["error"]["message"]


def test_batch_many_individually_valid_items_obey_aggregate_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr(mcp_batch_tools, "_MAX_BATCH_RESPONSE_BYTES", 1400)
    items = [
        {"id": f"aggregate-{index}", "kind": "source", "symbol": f"symbol-{index}"}
        for index in range(4)
    ]
    driver = _install_worker_driver(
        tmp_path,
        """
        def aggregate_run(request):
            item = request["item"]
            return {
                "id": item["id"],
                "kind": item["kind"],
                "result": {"sources": [{"source": "u" * 220}]},
            }
        mcp_batch_worker._run = aggregate_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    response = mcp_server.tg_batch(items, path=str(tmp_path))
    payload = _payload(response)

    assert len(response) <= mcp_batch_tools._MAX_BATCH_RESPONSE_BYTES
    assert [entry["id"] for entry in payload["results"]] == [item["id"] for item in items]
    successes = [entry for entry in payload["results"] if "result" in entry]
    refusals = [entry for entry in payload["results"] if "error" in entry]
    assert 1 < len(successes) < len(items), payload
    assert all(entry["error"]["code"] == "internal_error" for entry in refusals)
    assert all("response size limit" in entry["error"]["message"] for entry in refusals)
    for index in range(len(items)):
        single = [mcp_batch_tools._response_limit_item(item) for item in items]
        candidate = {
            "id": items[index]["id"],
            "kind": items[index]["kind"],
            "result": {"sources": [{"source": "u" * 220}]},
        }
        assert (
            mcp_batch_tools._json_pretty_size(
                mcp_batch_tools._response_payload(mcp_server, single, result_incomplete=False),
                mcp_batch_tools._MAX_BATCH_RESPONSE_BYTES,
                list_override=(single, index, candidate),
            )
            is not None
        )


def test_tg_batch_is_registered_and_returns_single_tool_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    sample = tmp_path / "sample.txt"
    sample.write_text("needle\n", encoding="utf-8")

    standalone = _payload(mcp_server.tg_search("needle", path=str(tmp_path)))
    encoded_result = mcp_server.tg_batch(
        [{"id": "q1", "kind": "search", "pattern": "needle"}],
        path=str(tmp_path),
        item_deadline=30.0,
        deadline=60.0,
    )
    result = _payload(encoded_result)
    expected = json.dumps(
        {
            "version": 1,
            "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
            "results": [{"id": "q1", "kind": "search", "result": standalone}],
            "result_incomplete": False,
        },
        indent=2,
    )

    assert result == {
        "version": 1,
        "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        "results": [{"id": "q1", "kind": "search", "result": standalone}],
        "result_incomplete": False,
    }
    assert encoded_result == expected
    assert "tg_batch" in {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}


@pytest.mark.parametrize(
    "kind,tool_name,args,result_key",
    [
        ("search", "tg_search", {"pattern": "target"}, "total_matches"),
        ("find", "tg_find", {"query": "target"}, "matches"),
        ("defs", "tg_symbol_defs", {"symbol": "target"}, "definitions"),
        ("refs", "tg_symbol_refs", {"symbol": "target"}, "references"),
        ("callers", "tg_symbol_callers", {"symbol": "target"}, "callers"),
        ("source", "tg_symbol_source", {"symbol": "target"}, "sources"),
    ],
)
def test_tg_batch_matches_standalone_for_each_kind(
    tmp_path, monkeypatch, kind, tool_name, args, result_key
):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(tmp_path / "not-fetched"))
    monkeypatch.setenv("TG_LSP_PROVIDER", "lsp")
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def target(value):\n    return value\n\ndef caller():\n    return target('needle')\n",
        encoding="utf-8",
    )
    arguments = {**args, "path": str(tmp_path)}
    standalone = _payload(getattr(mcp_server, tool_name)(**arguments))
    assert "error" not in standalone, standalone
    assert standalone[result_key], standalone

    result = _payload(
        mcp_server.tg_batch(
            [{"id": "same", "kind": kind, **args}],
            path=str(tmp_path),
            item_deadline=30.0,
            deadline=60.0,
        )
    )

    assert result["results"] == [{"id": "same", "kind": kind, "result": standalone}]


def test_tg_batch_preserves_plain_search_result_refusal_and_partial_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n" * 5, encoding="utf-8")
    good = {"id": "good", "kind": "search", "pattern": "needle", "structured_json": False}
    bad = {
        "id": "bad",
        "kind": "search",
        "pattern": "needle",
        "path": "../..",
        "structured_json": False,
    }
    standalone = mcp_server.tg_search("needle", path=str(tmp_path), structured_json=False)

    payload = _payload(
        mcp_server.tg_batch([good, bad], path=str(tmp_path), item_deadline=30.0, deadline=60.0)
    )

    assert payload["results"][0] == {"id": "good", "kind": "search", "result": standalone}
    assert payload["results"][1]["error"]["code"] == "invalid_input"
    assert payload["result_incomplete"] is True


@pytest.mark.parametrize(
    "queries",
    [
        [{"id": str(index), "kind": "search", "pattern": "x"} for index in range(17)],
        [
            {"id": "same", "kind": "search", "pattern": "x"},
            {"id": "same", "kind": "search", "pattern": "y"},
        ],
        [None],
        {},
        [{"id": "x", "kind": "unsupported"}],
    ],
)
def test_tg_batch_rejects_invalid_top_level_queries(queries):
    result = asyncio.run(mcp_server.mcp.call_tool("tg_batch", {"queries": queries}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True
    assert _payload(result.content[0].text)["error"]["code"] == "invalid_input"


def test_tg_batch_confines_each_item_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))

    result = _payload(
        mcp_server.tg_batch(
            [
                {"id": "bad", "kind": "search", "pattern": "x", "path": "../.."},
                {"id": "good", "kind": "search", "pattern": "y"},
            ],
            path=str(tmp_path),
            item_deadline=30.0,
            deadline=60.0,
        )
    )

    assert result["results"][0]["error"]["code"] == "invalid_input"
    assert result["results"][1]["result"]["pattern"] == "y"
    assert result["result_incomplete"] is True


def test_tg_batch_returns_partial_success_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")

    result = _payload(
        mcp_server.tg_batch(
            [
                {"id": "invalid", "kind": "search", "pattern": "["},
                {"id": "valid", "kind": "search", "pattern": "needle"},
            ],
            path=str(tmp_path),
            item_deadline=30.0,
            deadline=60.0,
        )
    )

    assert [item["id"] for item in result["results"]] == ["invalid", "valid"]
    assert result["results"][0]["error"]["code"] == "invalid_input"
    assert result["results"][1]["result"]["total_matches"] == 1
    assert result["result_incomplete"] is True


def test_tg_batch_transfers_default_path_scope_to_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        def scoped_run(request):
            return {
                "id": request["item"]["id"],
                "kind": request["item"]["kind"],
                "result": {"defaulted": request["paths_defaulted"]},
            }
        mcp_batch_worker._run = scoped_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = mcp_search_scope.call(
        lambda path: mcp_server.tg_batch(
            [{"id": "scope", "kind": "search", "pattern": "x"}], path=path
        ),
        path=str(tmp_path),
        paths_defaulted=True,
    )

    assert _payload(result)["results"][0]["result"] == {"defaulted": True}


def test_tg_batch_isolates_tool_stdout_from_worker_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        def noisy_run(request):
            print("python diagnostic")
            os.write(1, b"native diagnostic")
            os.write(2, b"native stderr diagnostic")
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"pattern": item["pattern"]}}
        mcp_batch_worker._run = noisy_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = _payload(
        mcp_server.tg_batch(
            [{"id": "clean", "kind": "search", "pattern": "needle"}], path=str(tmp_path)
        )
    )

    assert result["results"] == [{"id": "clean", "kind": "search", "result": {"pattern": "needle"}}]
    assert "native stderr diagnostic" in capsys.readouterr().err


def test_tg_batch_bounds_forwarded_worker_diagnostics(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        def noisy_run(request):
            os.write(2, b"D" * (64 * 1024 + 128))
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"ok": True}}
        mcp_batch_worker._run = noisy_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = _payload(
        mcp_server.tg_batch(
            [{"id": "bounded-log", "kind": "search", "pattern": "x"}], path=str(tmp_path)
        )
    )
    diagnostics = capsys.readouterr().err

    assert result["results"][0]["result"] == {"ok": True}
    assert diagnostics.count("D") <= 64 * 1024
    assert "diagnostics truncated after 65536 bytes" in diagnostics


def test_tg_batch_caps_utf8_expansion_of_invalid_worker_diagnostics(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        def noisy_run(request):
            os.write(2, b"\\xff" * (64 * 1024 + 128))
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"ok": True}}
        mcp_batch_worker._run = noisy_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = _payload(
        mcp_server.tg_batch(
            [{"id": "invalid-utf8-log", "kind": "search", "pattern": "x"}],
            path=str(tmp_path),
        )
    )
    diagnostics = capsys.readouterr().err

    assert result["results"][0]["result"] == {"ok": True}
    assert len(diagnostics.encode("utf-8")) <= 64 * 1024
    assert "diagnostics truncated after 65536 bytes" in diagnostics


def test_tg_batch_preserves_plain_search_rank_fallback_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "1")
    (tmp_path / "first.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("needle\n", encoding="utf-8")

    standalone = mcp_server.tg_search(
        "needle", path=str(tmp_path), rank=True, structured_json=False
    )
    standalone_warning = capsys.readouterr().err
    batch = _payload(
        mcp_server.tg_batch(
            [
                {
                    "id": "plain-rank",
                    "kind": "search",
                    "pattern": "needle",
                    "rank": True,
                    "structured_json": False,
                }
            ],
            path=str(tmp_path),
            item_deadline=30.0,
            deadline=60.0,
        )
    )
    batch_warning = capsys.readouterr().err

    assert "rank corpus cap reached" in standalone_warning
    assert standalone.startswith("Found 2 matches across 2 files:")
    assert batch["results"][0]["result"].startswith("Found 2 matches across 2 files:")
    assert "rank corpus cap reached" in batch_warning


def test_tg_batch_uses_trusted_worker_imports_with_hostile_cwd_and_pythonpath(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    attacker = tmp_path / "attacker"
    shadow_package = attacker / "tensor_grep"
    shadow_package.mkdir(parents=True)
    root.mkdir()
    (root / "sample.txt").write_text("needle\n", encoding="utf-8")
    marker = tmp_path / "shadow-imported.txt"
    (shadow_package / "__init__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['TG_MCP_TEST_SHADOW_MARKER']).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    shadow_cli = shadow_package / "cli"
    shadow_cli.mkdir()
    (shadow_cli / "__init__.py").write_text("", encoding="utf-8")
    (shadow_cli / "mcp_batch_worker.py").write_text(
        "print('forged worker output')\n", encoding="utf-8"
    )
    monkeypatch.setenv("TG_MCP_ROOT", str(root))
    monkeypatch.setenv("TG_MCP_TEST_SHADOW_MARKER", str(marker))
    monkeypatch.setenv("PYTHONPATH", str(attacker))
    monkeypatch.chdir(attacker)

    standalone = _payload(mcp_server.tg_search("needle", path=str(root)))
    batched = _payload(
        mcp_server.tg_batch(
            [{"id": "trusted", "kind": "search", "pattern": "needle"}],
            path=str(root),
            item_deadline=30.0,
            deadline=60.0,
        )
    )

    assert batched["results"] == [{"id": "trusted", "kind": "search", "result": standalone}]
    assert not marker.exists(), "worker imported tensor_grep from caller-controlled CWD/PYTHONPATH"


@pytest.mark.parametrize("mode", ["malformed", "crash"])
def test_tg_batch_rejects_malformed_worker_output_and_crashes(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setenv("TG_MCP_TEST_WORKER_MODE", mode)
    driver = _install_worker_driver(
        tmp_path,
        """
        if os.environ.get("TG_MCP_TEST_WORKER_MODE") == "malformed":
            mcp_batch_worker._write = lambda fd, value: os.write(fd, b"{")
        def test_run(request):
            if os.environ.get("TG_MCP_TEST_WORKER_MODE") == "crash":
                os._exit(23)
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"pattern": item["pattern"]}}
        mcp_batch_worker._run = test_run
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = _payload(
        mcp_server.tg_batch(
            [{"id": mode, "kind": "search", "pattern": "needle"}], path=str(tmp_path)
        )
    )

    assert result["results"][0]["error"]["code"] == "internal_error"
    assert result["result_incomplete"] is True


def test_tg_batch_rejects_wrong_worker_response_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        mcp_batch_worker._run = lambda request: "not an item envelope"
        """,
    )
    _track_contained_workers(monkeypatch, driver)

    result = _payload(
        mcp_server.tg_batch(
            [{"id": "bad-shape", "kind": "search", "pattern": "x"}], path=str(tmp_path)
        )
    )

    assert result["results"][0]["error"]["code"] == "internal_error"


def test_four_blocked_workers_are_reaped_queue_expires_and_capacity_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setenv("TG_MCP_TEST_WORKER_MODE", "blocked")
    marker_dir = tmp_path / "started"
    marker_dir.mkdir()
    monkeypatch.setenv("TG_MCP_TEST_MARKER_DIR", str(marker_dir))
    driver = _install_worker_driver(
        tmp_path,
        """
        def test_run(request):
            pattern = request["item"]["pattern"]
            if os.environ.get("TG_MCP_TEST_WORKER_MODE") == "blocked":
                child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
                marker = Path(os.environ["TG_MCP_TEST_MARKER_DIR"]) / f"{pattern}.json"
                marker.write_text(json.dumps({"worker": os.getpid(), "descendant": child.pid}), encoding="utf-8")
                time.sleep(60)
            item = request["item"]
            return {"id": item["id"], "kind": item["kind"], "result": {"pattern": pattern}}
        mcp_batch_worker._run = test_run
        """,
    )
    started, survivor_checks = _track_contained_workers(monkeypatch, driver)
    queries = [{"id": f"block-{i}", "kind": "search", "pattern": f"block-{i}"} for i in range(4)]
    batch_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as caller:
        first = caller.submit(
            mcp_server.tg_batch, queries, path=str(tmp_path), item_deadline=6.0, deadline=9.0
        )
        marker_paths = [marker_dir / f"block-{i}.json" for i in range(4)]
        _wait_for_files(marker_paths)
        descendants = [
            json.loads(path.read_text(encoding="utf-8"))["descendant"] for path in marker_paths
        ]

        queued = _payload(
            mcp_server.tg_batch(
                [{"id": "queued", "kind": "search", "pattern": "queued"}],
                path=str(tmp_path),
                item_deadline=0.15,
                deadline=1.0,
            )
        )
        assert queued["results"][0]["error"]["code"] == "deadline_exceeded"
        assert len(started) == 4, "queued request started while all four global slots were owned"

        first_result = _payload(first.result(timeout=15))

    assert all(item["error"]["code"] == "deadline_exceeded" for item in first_result["results"]), (
        first_result
    )
    assert len(started) == 4
    assert all(process.poll() is not None for process, _ in started)
    assert len([result for _, result in survivor_checks if not result]) >= 4
    assert descendants
    assert time.monotonic() - batch_started < 9.0, "cleanup exceeded the 2s grace after deadline"

    monkeypatch.setenv("TG_MCP_TEST_WORKER_MODE", "success")
    recovered = _payload(
        mcp_server.tg_batch(
            [{"id": "recovered", "kind": "search", "pattern": "ok"}], path=str(tmp_path)
        )
    )
    assert recovered["results"][0]["result"] == {"pattern": "ok"}
    assert len(started) == 5


def test_cleanup_failure_is_visible_and_keeps_the_global_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    driver = _install_worker_driver(
        tmp_path,
        """
        mcp_batch_worker._run = lambda request: {
            "id": request["item"]["id"],
            "kind": request["item"]["kind"],
            "result": {"ok": True},
        }
        """,
    )
    _track_contained_workers(monkeypatch, driver)
    slots = threading.BoundedSemaphore(4)
    monkeypatch.setattr("tensor_grep.cli.mcp_batch_tools._BATCH_SLOTS", slots)
    real_spawn = mcp_batch_supervisor.spawn_contained

    def failed_survivor_check(argv, **kwargs):
        process, containment = real_spawn(argv, **kwargs)
        real_survivors = containment.survivors
        calls = 0

        def report_unverified(deadline):
            nonlocal calls
            calls += 1
            result = real_survivors(deadline)
            return result if calls == 1 else ["injected survivor verification failure"]

        containment.survivors = report_unverified
        return process, containment

    monkeypatch.setattr(mcp_batch_supervisor, "spawn_contained", failed_survivor_check)
    result = _payload(
        mcp_server.tg_batch(
            [{"id": "cleanup", "kind": "search", "pattern": "x"}], path=str(tmp_path)
        )
    )

    assert result["results"][0]["error"]["code"] == "internal_error"
    assert "cleanup failed" in result["results"][0]["error"]["message"]
    available = [slots.acquire(blocking=False) for _ in range(4)]
    assert sum(available) == 3, "slot was released without verified process-tree cleanup"
    for acquired in available:
        if acquired:
            slots.release()


def test_tg_batch_plain_text_timeout_and_omission_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n" * 5, encoding="utf-8")

    for structured_json in (True, False):
        result = _payload(
            mcp_server.tg_batch(
                [
                    {
                        "id": "capped",
                        "kind": "search",
                        "pattern": "needle",
                        "max_results": 1,
                        "structured_json": structured_json,
                    }
                ],
                path=str(tmp_path),
                item_deadline=30.0,
                deadline=60.0,
            )
        )
        assert result["result_incomplete"] is True, result
        assert result["results"][0].get("result_incomplete") is True
        if structured_json:
            assert result["results"][0]["result"]["omitted_matches"] > 0
        else:
            assert "output truncated" in result["results"][0]["result"]


@pytest.mark.parametrize(
    "kind,arguments,expected_deadline",
    [
        ("find", {"query": "target"}, 3.25),
        ("refs", {"symbol": "target", "deadline": 0.5}, 0.5),
        ("callers", {"symbol": "target", "deadline": 20.0}, 3.25),
        ("search", {"pattern": "target"}, None),
        ("defs", {"symbol": "target"}, None),
        ("source", {"symbol": "target"}, None),
    ],
)
def test_batch_worker_preserves_remaining_deadline_contract(kind, arguments, expected_deadline):
    received = []
    server = type("Server", (), {})()

    def fake_tool(**kwargs):
        received.append(kwargs)
        return json.dumps({"deadline": kwargs.get("deadline")})

    setattr(server, mcp_batch_tools._KIND_TO_TOOL[kind], fake_tool)
    item = {"id": "bounded", "kind": kind, **arguments}

    result = mcp_batch_tools._run_item(server, item, ".", remaining=3.25)

    assert result["result"]["deadline"] == expected_deadline
    assert len(received) == 1


def test_batch_default_deadline_includes_queue_and_worker_startup(monkeypatch):
    from tensor_grep.cli.mcp_batch_supervisor import WorkerRun

    clock = SimpleNamespace(now=100.0)
    time_stub = SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda _seconds: setattr(clock, "now", clock.now + 2.0),
    )
    slot_attempts = 0
    observed: list[tuple[float, float]] = []

    def acquire_slot(*, blocking: bool = True) -> bool:
        nonlocal slot_attempts
        assert blocking is False
        slot_attempts += 1
        return slot_attempts > 1

    class DelayedExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            def start_worker():
                clock.now += 1.0
                return fn(*args, **kwargs)

            return super().submit(start_worker)

    def run_worker(request, deadline_end, cancel_event=None):
        del cancel_event
        observed.append((deadline_end, deadline_end - clock.now))
        output = json.dumps({
            "id": request["item"]["id"],
            "kind": "search",
            "result": {"ok": True},
        }).encode()
        return WorkerRun(output, clock.now, 0, tree_reaped=True)

    monkeypatch.setattr(mcp_batch_tools, "time", time_stub)
    monkeypatch.setattr(mcp_batch_tools, "ThreadPoolExecutor", DelayedExecutor)
    monkeypatch.setattr(
        mcp_batch_tools,
        "_BATCH_SLOTS",
        SimpleNamespace(acquire=acquire_slot, release=lambda: None),
    )
    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", run_worker)

    payload = _payload(
        mcp_batch_tools.run_tg_batch(
            mcp_server, [{"id": "default", "kind": "search", "pattern": "x"}]
        )
    )

    assert payload["results"] == [{"id": "default", "kind": "search", "result": {"ok": True}}]
    assert observed == [(110.0, 7.0)]
