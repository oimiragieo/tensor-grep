"""Tests for Wave 4 Track Q (IMP-05): tg_batch typed step-chaining pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli import mcp_batch_pipeline, mcp_batch_tools, mcp_server
from tensor_grep.cli.mcp_batch_supervisor import WorkerRun


def _make_worker_run(payload: dict[str, object]) -> WorkerRun:
    return WorkerRun(
        exit_code=0,
        output=json.dumps(payload).encode("utf-8"),
        diagnostics=b"",
        diagnostics_truncated=False,
        timed_out=False,
        output_overflow=False,
        input_too_large=False,
        tree_reaped=True,
        cleanup_failed=False,
        finished_at=0.0,
    )


def test_pipeline_ref_chaining_prev_and_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_items: list[dict[str, object]] = []

    def fake_worker(request: dict[str, object], item_end: float, cancel_event=None) -> WorkerRun:
        del item_end, cancel_event
        item = dict(request["item"])  # type: ignore[arg-type]
        seen_items.append(item)
        item_id = str(item["id"])
        kind = str(item["kind"])
        if item_id == "step0":
            return _make_worker_run({
                "id": item_id,
                "kind": kind,
                "result": {
                    "primary_target": {"file": "src/app.py", "symbol": "compute"},
                    "definitions": [{"file": "src/app.py", "line": 10}],
                    "coverage": {"blob": "x" * 500},
                },
            })
        return _make_worker_run({
            "id": item_id,
            "kind": kind,
            "result": {
                "file": item.get("file"),
                "symbol": item.get("symbol"),
                "pattern": item.get("pattern"),
                "coverage": {"verbose": "y" * 5000},
            },
        })

    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", fake_worker)

    raw = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [
            {"id": "step0", "kind": "find", "query": "compute"},
            {
                "id": "step1",
                "kind": "source",
                "file": "$prev.primary_target.file",
                "symbol": "$prev.primary_target.symbol",
            },
            {
                "id": "step2",
                "kind": "search",
                "pattern": "^compute$",
                "file": "$steps[0].definitions[0].file",
            },
        ],
        path=str(tmp_path),
    )
    payload = json.loads(raw)
    assert payload["result_incomplete"] is False
    assert len(payload["results"]) == 3
    assert seen_items[1]["file"] == "src/app.py"
    assert seen_items[1]["symbol"] == "compute"
    assert seen_items[2]["pattern"] == "^compute$"
    assert seen_items[2]["file"] == "src/app.py"


def test_tg_batch_return_final_only_compacts_final_capsule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_worker(request: dict[str, object], item_end: float, cancel_event=None) -> WorkerRun:
        del item_end, cancel_event
        item = dict(request["item"])  # type: ignore[arg-type]
        item_id = str(item["id"])
        kind = str(item["kind"])
        if item_id == "s0":
            return _make_worker_run({
                "id": item_id,
                "kind": kind,
                "result": {"primary_target": {"file": "src/app.py"}},
            })
        return _make_worker_run({
            "id": item_id,
            "kind": kind,
            "result": {
                "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
                "routing_backend": "RepoMap",
                "routing_reason": "source",
                "sidecar_used": False,
                "primary_target": {"file": item.get("file"), "line": 1},
                "coverage": {"huge": "z" * 6000},
            },
        })

    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", fake_worker)

    raw = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [
            {"id": "s0", "kind": "find", "query": "app"},
            {"id": "s1", "kind": "source", "file": "$prev.primary_target.file"},
        ],
        path=str(tmp_path),
        return_final_only=True,
    )
    payload = json.loads(raw)
    assert payload["result_incomplete"] is False
    assert len(payload["results"]) == 1
    assert payload["results"][0]["id"] == "s1"
    final_res = payload["results"][0]["result"]
    assert final_res["primary_target"]["file"] == "src/app.py"
    assert "coverage" not in final_res


@pytest.mark.parametrize(
    "bad_ref",
    [
        "$prev",
        "$prev.__class__",
        "$prev._private",
        "$prev.primary_target.__dict__",
        "$steps[0]",
        "$steps[-1].file",
        "$steps[99].file",
        "$prev.a.b.c.d.e.f.g.h.i",
    ],
)
def test_pipeline_rejects_unsafe_or_invalid_refs(bad_ref: str) -> None:
    with pytest.raises(mcp_batch_pipeline.PipelineRefError):
        mcp_batch_pipeline.parse_pipeline_ref(bad_ref, step_index=1)


def test_pipeline_ref_at_step_zero_and_missing_field_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_worker(request: dict[str, object], item_end: float, cancel_event=None) -> WorkerRun:
        del item_end, cancel_event
        item = dict(request["item"])  # type: ignore[arg-type]
        return _make_worker_run({
            "id": str(item["id"]),
            "kind": str(item["kind"]),
            "result": {"definitions": []},
        })

    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", fake_worker)

    raw_step0 = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [{"id": "s0", "kind": "defs", "symbol": "$prev.primary_target.symbol"}],
        path=str(tmp_path),
    )
    p0 = json.loads(raw_step0)
    assert p0["result_incomplete"] is True
    assert p0["results"][0]["error"]["code"] == "invalid_input"

    raw_oob = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [
            {"id": "s0", "kind": "defs", "symbol": "foo"},
            {"id": "s1", "kind": "source", "file": "$steps[0].definitions[0].file"},
        ],
        path=str(tmp_path),
    )
    p_oob = json.loads(raw_oob)
    assert p_oob["result_incomplete"] is True
    assert p_oob["results"][1]["error"]["code"] == "invalid_input"


def test_pipeline_enforces_path_confinement_on_chained_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(root))

    def fake_worker(request: dict[str, object], item_end: float, cancel_event=None) -> WorkerRun:
        del item_end, cancel_event
        item = dict(request["item"])  # type: ignore[arg-type]
        if item["id"] == "s0":
            return _make_worker_run({
                "id": "s0",
                "kind": "find",
                "result": {"primary_target": {"file": str(outside)}},
            })
        return _make_worker_run(
            mcp_batch_tools._run_item(
                mcp_server,
                item,
                str(request["path"]),
            )
        )

    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", fake_worker)

    raw = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [
            {"id": "s0", "kind": "find", "query": "secret"},
            {
                "id": "s1",
                "kind": "search",
                "pattern": "secret",
                "path": "$prev.primary_target.file",
            },
        ],
        path=str(root),
        return_final_only=True,
    )
    payload = json.loads(raw)
    assert payload["result_incomplete"] is True
    assert payload["results"][0]["error"]["code"] == "invalid_input"


def test_pipeline_preflight_rejected_step_is_never_dispatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatched_ids: list[str] = []

    def fake_worker(request: dict[str, object], item_end: float, cancel_event=None) -> WorkerRun:
        del item_end, cancel_event
        item = dict(request["item"])  # type: ignore[arg-type]
        item_id = str(item["id"])
        dispatched_ids.append(item_id)
        return _make_worker_run({
            "id": item_id,
            "kind": str(item["kind"]),
            "result": {"definitions": [{"file": "src/app.py", "line": 1}]},
        })

    monkeypatch.setattr(mcp_batch_tools, "run_contained_worker", fake_worker)

    raw = mcp_batch_tools.run_tg_batch(
        mcp_server,
        [
            {"id": "s0", "kind": "defs", "symbol": "$prev.primary_target.symbol"},
            {"id": "s1", "kind": "defs", "symbol": "valid_symbol"},
        ],
        path=str(tmp_path),
    )
    payload = json.loads(raw)
    assert payload["result_incomplete"] is True
    assert payload["results"][0]["id"] == "s0"
    assert payload["results"][0]["error"]["code"] == "invalid_input"
    assert payload["results"][1]["id"] == "s1"
    assert "result" in payload["results"][1]
    assert dispatched_ids == ["s1"]
