import json
import os
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest

from tensor_grep.cli import mcp_batch_tools, mcp_batch_worker, mcp_server


def test_worker_request_accepts_only_the_private_json_envelope(monkeypatch):
    request = {
        "item": {"id": "one", "kind": "search", "pattern": "needle"},
        "path": ".",
        "paths_defaulted": True,
        "whole_tree_containment": True,
        "deadline_end": 100.0,
    }
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(json.dumps(request).encode())))

    assert mcp_batch_worker._request() == request


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b'{"item": {}, "path": ".", "paths_defaulted": "yes", "whole_tree_containment": true, "deadline_end": 1}',
        b'{"item": {}, "path": ".", "paths_defaulted": true, "whole_tree_containment": true, "deadline_end": true}',
    ],
)
def test_worker_request_rejects_malformed_json_envelopes(monkeypatch, raw):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(raw)))

    with pytest.raises((ValueError, json.JSONDecodeError)):
        mcp_batch_worker._request()


def test_worker_request_enforces_a_bounded_input_read(monkeypatch):
    monkeypatch.setattr(mcp_batch_worker, "MAX_WORKER_INPUT_BYTES", 3)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=BytesIO(b"1234")))

    with pytest.raises(ValueError, match="input too large"):
        mcp_batch_worker._request()


def test_worker_output_limit_returns_a_bounded_error_envelope(monkeypatch):
    monkeypatch.setattr(mcp_batch_worker, "MAX_WORKER_OUTPUT_BYTES", 256)
    original_encoder = json.JSONEncoder.iterencode

    def bounded_encoder(self, value, *args, **kwargs):
        if isinstance(value, dict) and len(value.get("result", "")) > 256:
            pytest.fail("worker encoded the oversized result before refusing it")
        return original_encoder(self, value, *args, **kwargs)

    monkeypatch.setattr(json.JSONEncoder, "iterencode", bounded_encoder)
    read_fd, write_fd = os.pipe()
    try:
        mcp_batch_worker._write(
            write_fd,
            {"id": "large", "kind": "search", "result": "x" * 1024},
        )
        os.close(write_fd)
        write_fd = -1
        encoded = os.read(read_fd, 4096)
        assert len(encoded) <= 256
        payload = json.loads(encoded)
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)

    assert payload["id"] == "large"
    assert payload["error"]["code"] == "internal_error"


def test_json_limit_refuses_before_full_encoding(monkeypatch):
    from tensor_grep.cli import mcp_batch_json

    def forbidden(*args, **kwargs):
        pytest.fail("oversized JSON reached the full encoder")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", forbidden)
    with pytest.raises(mcp_batch_json.JsonByteLimitError):
        list(mcp_batch_json.iter_json_bytes({"value": "x" * 4096}, 256))


def test_supervisor_refuses_large_input_before_spawning(monkeypatch):
    import time

    from tensor_grep.cli import mcp_batch_supervisor

    monkeypatch.setattr(mcp_batch_supervisor, "MAX_WORKER_INPUT_BYTES", 256)
    monkeypatch.setattr(
        mcp_batch_supervisor, "spawn_contained", lambda *a, **k: pytest.fail("spawned large input")
    )
    result = mcp_batch_supervisor.run_contained_worker({"item": "x" * 1024}, time.monotonic() + 1)
    assert result.input_too_large and result.tree_reaped


def test_json_preflight_honors_deadline_and_rejects_cycles():
    import time

    from tensor_grep.cli.mcp_batch_json import iter_json_bytes

    with pytest.raises(TimeoutError):
        list(iter_json_bytes({"value": "ok"}, 256, time.monotonic() - 1))
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="Circular"):
        list(iter_json_bytes(cyclic, 256))


def test_serialization_failure_leaves_worker_capacity_reclaimable(monkeypatch):
    import time

    from tensor_grep.cli import mcp_batch_supervisor

    def fail(*args, **kwargs):
        raise RecursionError("nested input")

    monkeypatch.setattr(mcp_batch_supervisor, "iter_json_bytes", fail)
    outcome = mcp_batch_supervisor.run_contained_worker({}, time.monotonic() + 1)
    assert outcome.tree_reaped and outcome.output is None


@pytest.mark.parametrize("value", ["é😀\n", [1, True, None, {"x": '\\"'}], {"many": ["abc"] * 200}])
def test_bounded_json_matches_exact_utf8_limit(value):
    from tensor_grep.cli.mcp_batch_json import JsonByteLimitError, iter_json_bytes

    expected = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    assert b"".join(iter_json_bytes(value, len(expected))) == expected
    with pytest.raises(JsonByteLimitError):
        list(iter_json_bytes(value, len(expected) - 1))


@pytest.mark.parametrize("kind", ["defs", "refs", "callers", "source"])
@pytest.mark.parametrize("provider", ["lsp", "hybrid"])
def test_process_group_worker_fails_closed_for_nested_external_lsp(monkeypatch, kind, provider):
    request = {
        "item": {"id": "external", "kind": kind, "symbol": "name", "provider": provider},
        "path": ".",
        "paths_defaulted": False,
        "whole_tree_containment": False,
        "deadline_end": 100.0,
    }
    monkeypatch.setattr(
        mcp_batch_tools,
        "_run_item",
        lambda *args, **kwargs: pytest.fail("external provider escaped the process-group guard"),
    )

    result = mcp_batch_worker._run(request)

    assert result["error"]["code"] == "internal_error"
    assert "cannot be contained" in result["error"]["message"]


def test_whole_tree_worker_preserves_explicit_external_provider(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "tg_symbol_defs", lambda **kwargs: json.dumps({"ok": kwargs["provider"]})
    )
    request = {
        "item": {"id": "external", "kind": "defs", "symbol": "name", "provider": "lsp"},
        "path": ".",
        "paths_defaulted": False,
        "whole_tree_containment": True,
        "deadline_end": 100.0,
    }

    result = mcp_batch_worker._run(request)

    assert result["result"] == {"ok": "lsp"}
