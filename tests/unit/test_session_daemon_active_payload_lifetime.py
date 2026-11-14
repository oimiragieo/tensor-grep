"""Behavioral lease/deadline regressions through existing daemon callers; no sockets."""

import io
import json
import socketserver
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tensor_grep.cli import session_daemon as sd


@pytest.fixture
def owned_daemon(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="owned"\nversion="0.0.0"\n')

    def no_socket_init(self, server_address, handler_class):
        self.server_address = server_address

    monkeypatch.setattr(socketserver.TCPServer, "__init__", no_socket_init)
    server = sd._ThreadedSessionDaemon(root, ("unused", 0), token="owned-token")
    payloads, removals = {}, []
    state_lock = threading.Lock()

    def opener(path, **kwargs):
        assert Path(path).resolve() == root.resolve()
        session_id = uuid4().hex
        payload_path = sd._session_payload_path(root, session_id)
        assert payload_path.resolve().is_relative_to(root.resolve())
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_text("{}")
        with state_lock:
            payloads[session_id] = payload_path
        return SimpleNamespace(session_id=session_id)

    def remove(path, session_id, *, deadline_monotonic=None):
        assert Path(path).resolve() == root.resolve()
        payload_path = payloads[session_id]
        assert payload_path.resolve().is_relative_to(root.resolve())
        payload_path.unlink(missing_ok=True)
        with state_lock:
            removals.append(session_id)

    def load(cache, session_id, path, **kwargs):
        payload_path = payloads[session_id]
        return json.loads(payload_path.read_text()), "miss"

    monkeypatch.setattr(sd, "open_session", opener)
    monkeypatch.setattr(sd, "_remove_implicit_session_payload", remove)
    monkeypatch.setattr(sd, "_load_payload_with_status_retry", load)
    monkeypatch.setattr(
        sd, "_serve_daemon_response_with_cache", lambda **kwargs: ({"ok": True}, "bypass")
    )
    return SimpleNamespace(
        root=root, server=server, payloads=payloads, removals=removals, load=load, opener=opener
    )


def _handle(owned, budget):
    handler = sd._SessionDaemonHandler.__new__(sd._SessionDaemonHandler)
    handler.server = owned.server
    handler.connection = SimpleNamespace(settimeout=lambda value: None, close=lambda: None)
    handler.rfile = io.BytesIO(
        (
            json.dumps({
                "command": "context_render",
                "path": str(owned.root),
                "max_repo_files": budget,
                "token": "owned-token",
            })
            + "\n"
        ).encode()
    )
    handler.wfile = io.BytesIO()
    sd._SessionDaemonHandler.handle(handler)
    return json.loads(handler.wfile.getvalue())


def test_uncontended_authenticated_handler_keeps_owned_cached_payload(owned_daemon):
    assert _handle(owned_daemon, 20)["ok"] is True
    assert len(owned_daemon.payloads) == 1
    assert next(iter(owned_daemon.payloads.values())).exists()
    assert not owned_daemon.removals


@pytest.mark.parametrize("readers", [1, 2])
def test_active_implicit_payload_survives_lru_until_last_handler_release(
    owned_daemon, monkeypatch, readers
):
    owned = owned_daemon
    entered = [threading.Event() for _ in range(readers)]
    release = [threading.Event() for _ in range(readers)]
    done = [threading.Event() for _ in range(readers)]
    ids, responses, errors = {}, {}, []

    def paused_load(cache, session_id, path, **kwargs):
        name = threading.current_thread().name
        if name.startswith("owned-reader-"):
            index = int(name.rsplit("-", 1)[1])
            ids[index] = session_id
            entered[index].set()
            if not release[index].wait(10):
                raise TimeoutError("owned handler load gate was not released")
        return owned.load(cache, session_id, path, **kwargs)

    monkeypatch.setattr(sd, "_load_payload_with_status_retry", paused_load)

    def reader(index):
        try:
            responses[index] = _handle(owned, 20)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done[index].set()

    threads = [
        threading.Thread(target=reader, args=(i,), name=f"owned-reader-{i}", daemon=True)
        for i in range(readers)
    ]
    try:
        for i, thread in enumerate(threads):
            thread.start()
            assert entered[i].wait(3), "handler did not reach post-ID payload-load seam"
        assert len(set(ids.values())) == 1, "same key did not share its implicit session"
        held_id = ids[0]
        held_path = owned.payloads[held_id]
        for budget in range(21, 37):
            assert _handle(owned, budget)["ok"] is True
        after_eviction_exists = held_path.exists()
        for i in range(readers):
            release[i].set()
            assert done[i].wait(3), "released handler did not finish"
            if i + 1 < readers:
                after_first_release_exists = held_path.exists()
        after_last_release_exists = held_path.exists()
    finally:
        for gate in release:
            gate.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(3)
    assert all(not thread.is_alive() for thread in threads), "bounded handler cleanup failed"
    assert not errors, repr(errors)
    assert after_eviction_exists, "LRU deleted payload while authenticated handler owned its ID"
    if readers == 2:
        assert after_first_release_exists, "first reader release deleted second reader's payload"
    assert not after_last_release_exists, "retired payload leaked after last handler release"
    assert all(response.get("ok") is True for response in responses.values()), responses
    assert owned.removals.count(held_id) == 1


@pytest.mark.parametrize("expired,owner_fails", [(True, True), (False, True), (False, False)])
def test_owner_exit_respects_follower_original_deadline(
    owned_daemon, monkeypatch, expired, owner_fails
):
    owned = owned_daemon
    clock = {"now": 5.0}
    monkeypatch.setattr(sd, "monotonic", lambda: clock["now"])
    owner_entered, follower_at_lock, owner_release = [threading.Event() for _ in range(3)]
    lock = owned.server._implicit_session_lock

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "deadline-follower":
                follower_at_lock.set()
            lock.acquire()
            return self

        def __exit__(self, *args):
            lock.release()

    owned.server._implicit_session_lock = ObservedLock()
    calls, results, errors = [], {}, {}

    def opener(path, **kwargs):
        name = threading.current_thread().name
        calls.append((name, kwargs["deadline_monotonic"]))
        if name == "deadline-owner":
            owner_entered.set()
            if not owner_release.wait(8):
                raise TimeoutError("owner gate was not released")
            if owner_fails:
                raise LookupError("controlled owner failure")
        return owned.opener(path, **kwargs)

    monkeypatch.setattr(sd, "open_session", opener)

    def run(label, deadline):
        try:
            results[label] = sd._implicit_session_id_for_request(
                owned.server,
                command="context_render",
                session_id="",
                path=str(owned.root),
                request={"max_repo_files": 20},
                deadline_monotonic=deadline,
            )
        except BaseException as exc:
            errors[label] = exc

    owner = threading.Thread(target=run, args=("owner", 100.0), name="deadline-owner", daemon=True)
    follower = threading.Thread(
        target=run, args=("follower", 10.0), name="deadline-follower", daemon=True
    )
    owner.start()
    try:
        assert owner_entered.wait(3)
        follower.start()
        assert follower_at_lock.wait(3)
        if expired:
            clock["now"] = 11.0
    finally:
        owner_release.set()
        owner.join(3)
        if follower.ident is not None:
            follower.join(3)
    assert not owner.is_alive() and not follower.is_alive(), "bounded owner/follower cleanup failed"
    if owner_fails:
        assert isinstance(errors.get("owner"), LookupError)
    else:
        assert "owner" not in errors
    if expired:
        assert len(calls) == 1, "expired follower became new owner and started another open"
        assert isinstance(errors.get("follower"), TimeoutError)
        assert "follower" not in results
    elif owner_fails:
        assert len(calls) == 2
        assert calls[1] == ("deadline-follower", 10.0)
        assert "follower" in results and "follower" not in errors
    else:
        assert len(calls) == 1
        assert results["follower"] == results["owner"]
        assert not errors
