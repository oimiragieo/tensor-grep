"""Round-6/7 r9: ExternalLSPClient.start() check-then-spawn must be serialized. Two daemon worker
threads (ThreadingMixIn) calling into the SAME cached client (get_client is shared per
(root,language)) previously both passed the `if self.process is not None` None-check and both
Popen'd, orphaning one language-server child. A double-checked _start_lock guards the spawn."""

import io
import threading
import time

from tensor_grep.cli import lsp_external_provider as provider_module


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()  # empty -> reader loop hits EOF cleanly
        self.stderr = io.BytesIO()

    def poll(self):
        return None  # alive

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_concurrent_start_spawns_exactly_one_process(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_module, "_provider_command", lambda language: ["fake-server"])
    monkeypatch.setattr(provider_module, "direct_managed_node_command", lambda *a, **k: None)
    monkeypatch.setattr(provider_module, "wrap_windows_batch_command", lambda cmd: list(cmd))
    monkeypatch.setattr(provider_module, "managed_provider_env", lambda *a, **k: {})

    client = provider_module.ExternalLSPClient(language="python", workspace_root=tmp_path)

    spawns: list[int] = []

    def _fake_popen(*_a, **_k):
        spawns.append(1)
        time.sleep(0.05)  # widen the race window so an unserialized start() would double-spawn
        return _FakeProc()

    monkeypatch.setattr(provider_module.subprocess, "Popen", _fake_popen)
    # skip the real initialize handshake (which would need a live reader/stdio round-trip)
    monkeypatch.setattr(client, "request", lambda *a, **k: {"capabilities": {}})

    threads = [threading.Thread(target=client.start) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(spawns) == 1, (
        f"start() double-spawned ({len(spawns)} Popen calls) — the race is open"
    )


def test_start_lock_and_lock_are_distinct(tmp_path, monkeypatch):
    # Guards the anti-deadlock invariant: start() must not reuse _lock (its handshake takes _lock).
    monkeypatch.setattr(provider_module, "_provider_command", lambda language: ["fake-server"])
    client = provider_module.ExternalLSPClient(language="python", workspace_root=tmp_path)
    assert client._start_lock is not client._lock
