"""P0-2 (warm-LSP moat): readiness gate in ExternalLSPClient.

The 2-of-14 under-return happens because `textDocument/references` fires immediately after one
didOpen while the server is still building its workspace index: `window/workDoneProgress/create`
was a bare no-op ACK and `$/progress` notifications were dropped at `_dispatch_response`
(id-less -> return). The client must track progress tokens and expose
`wait_until_ready(deadline_monotonic)` so the first query per (root,language) waits for the index
to settle. A readiness TIMEOUT must NOT arm the 30s `disabled_until_monotonic` cooldown -- only a
real `initialize` failure may do that (otherwise one slow first index blackballs the language for
30s of daemon uptime).

Messages are driven directly through `_handle_server_request` / `_dispatch_response` -- the exact
entry points `_reader_loop` feeds parsed messages into.
"""

import time
from pathlib import Path

import pytest

from tensor_grep.cli import lsp_external_provider as provider_module


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> provider_module.ExternalLSPClient:
    monkeypatch.setattr(provider_module, "_provider_command", lambda language: ["fake-server"])
    made = provider_module.ExternalLSPClient(language="typescript", workspace_root=tmp_path)
    # Neutralize the response write path (no real process): server-request handling ACKs via
    # _write_response, which must not require live stdin in these unit tests.
    monkeypatch.setattr(made, "_write_response", lambda *a, **k: None)
    return made


def _create_progress(client: provider_module.ExternalLSPClient, token: str) -> None:
    client._handle_server_request({
        "id": 99,
        "method": "window/workDoneProgress/create",
        "params": {"token": token},
    })


def _progress(client: provider_module.ExternalLSPClient, token: str, kind: str) -> None:
    client._dispatch_response({
        "method": "$/progress",
        "params": {"token": token, "value": {"kind": kind}},
    })


def test_progress_create_then_end_marks_ready(client) -> None:
    _create_progress(client, "idx-1")
    assert client.wait_until_ready(time.monotonic() + 0.05) is False  # created, never ended
    _progress(client, "idx-1", "begin")
    _progress(client, "idx-1", "report")
    assert client.wait_until_ready(time.monotonic() + 0.05) is False  # still in flight
    _progress(client, "idx-1", "end")
    assert client.wait_until_ready(time.monotonic() + 0.05) is True


def test_server_initiated_begin_without_create_still_tracked(client) -> None:
    _progress(client, "anon-token", "begin")
    assert client.wait_until_ready(time.monotonic() + 0.05) is False
    _progress(client, "anon-token", "end")
    assert client.wait_until_ready(time.monotonic() + 0.05) is True


def test_readiness_timeout_does_not_arm_cooldown(client) -> None:
    _progress(client, "never-ends", "begin")
    assert client.wait_until_ready(time.monotonic() + 0.05) is False
    # The load-bearing assertion: a slow index is NOT an initialize failure.
    assert client.disabled_until_monotonic == 0.0


def test_no_progress_server_uses_stability_probe(client) -> None:
    # Server emits no progress at all; a probe (workspace/symbol hit count) that returns the
    # same value on consecutive polls means the index has settled.
    counts = iter([3, 5, 7, 7])
    assert (
        client.wait_until_ready(
            time.monotonic() + 5.0,
            probe=lambda: next(counts),
            no_progress_grace_seconds=0.01,
            poll_interval_seconds=0.01,
        )
        is True
    )


def test_no_progress_no_probe_returns_ready_after_grace(client) -> None:
    # A server that never advertises progress and has no probe: after the grace window we
    # proceed best-effort (True) rather than burning the whole deadline on silence.
    start = time.monotonic()
    assert (
        client.wait_until_ready(
            start + 5.0, no_progress_grace_seconds=0.02, poll_interval_seconds=0.01
        )
        is True
    )
    assert time.monotonic() - start < 2.0  # did not burn the full deadline


def test_ready_is_cached_and_new_begin_reinvalidates(client) -> None:
    _progress(client, "t", "begin")
    _progress(client, "t", "end")
    assert client.wait_until_ready(time.monotonic() + 0.05) is True
    # cached: an already-ready client answers instantly even with an expired deadline
    assert client.wait_until_ready(time.monotonic() - 1.0) is True
    # a NEW indexing round (e.g. file churn) re-invalidates readiness
    _progress(client, "t2", "begin")
    assert client.wait_until_ready(time.monotonic() + 0.05) is False
    _progress(client, "t2", "end")
    assert client.wait_until_ready(time.monotonic() + 0.05) is True


def test_plain_responses_still_dispatch_to_slots(client) -> None:
    # The $/progress consumer must not break normal response demultiplexing.
    import queue as queue_module

    slot: queue_module.Queue = queue_module.Queue(maxsize=1)
    with client._lock:
        client._pending_requests[7] = slot
    client._dispatch_response({"id": 7, "result": {"ok": True}})
    assert slot.get_nowait()["result"] == {"ok": True}
