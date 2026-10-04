"""ONE exit-code table for ``tg session daemon stop`` / ``status`` (PR #1197 round 7).

Two members of the same class kept slipping through: a stop that reported success without proof (a
missing/null recorded port skipped the refusal check, exit 0) and a structured daemon error kept in the
payload next to an exit 0. The class is closed by a single pure function, ``stop_exit_code``:

* exit 0 ONLY for an explicitly proven outcome -- ``running`` is False, the payload carries NO ``error``
  and a ``proof`` from the closed set the code can produce (``no_metadata``, ``endpoint_refused``,
  ``cooperative_refused``, ``pid_refused``);
* everything else is exit 2: any unconfirmed result, any retained ``error``, existing metadata with a
  missing / null / invalid endpoint (the metadata is kept), and any payload this table does not know.

If a proven shutdown supersedes a failed request, the daemon's reply moves into ``stop_reply_error`` and
the result is an explicit success. ``status_exit_code`` is the same discipline for ``status``.
The shape list below is DERIVED from the code (its constants and the strings in the producer
functions), not hand-copied, and a source scan fails if a producer grows a shape the table lacks.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_stop_cli as cli
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.4)


# ---- the table, over every shape the code can produce ----


def _unconfirmed_payloads() -> list[dict[str, Any]]:
    shapes = []
    for state, delivered, endpoint_ok in itertools.product(
        ("ours", "gone", "unverifiable", "unknown-state"), (True, False), (True, False)
    ):
        shapes.append(trust._unconfirmed_fields(state, delivered, endpoint_ok))
    return shapes


def _success_payloads() -> list[dict[str, Any]]:
    shapes = []
    for method, reply_error in itertools.product(("cooperative", "pid", "none"), (None, "boom")):
        response: dict[str, Any] = {"version": 1, "ok": reply_error is None}
        if reply_error:
            response["error"] = {"code": reply_error}
        shapes.append(trust._stop_success(response, Path("r"), method))
    for killed, had_metadata in itertools.product((True, False), (True, False)):
        shapes.append(trust._stale_success_fields(killed, had_metadata))
    return shapes


def test_every_unconfirmed_shape_exits_2() -> None:
    for payload in _unconfirmed_payloads():
        assert payload["running"] is True and payload["stopped"] is False
        assert payload["unconfirmed_reason"] in trust._UNCONFIRMED_REASONS, payload
        assert cli.stop_exit_code(payload) == 2, payload
        assert cli.stop_exit_code({**payload, "error": {"code": "x"}}) == 2


def test_every_success_shape_exits_0_only_without_an_error_and_with_a_known_proof() -> None:
    seen = set()
    for payload in _success_payloads():
        assert payload["running"] is False, payload
        assert payload["proof"] in trust._STOP_PROOFS, payload
        assert "error" not in payload, payload  # a superseded reply was moved to stop_reply_error
        assert cli.stop_exit_code(payload) == 0, payload
        assert cli.stop_exit_code({**payload, "error": {"code": "unauthorized"}}) == 2
        assert cli.stop_exit_code({k: v for k, v in payload.items() if k != "proof"}) == 2
        assert cli.stop_exit_code({**payload, "proof": "made-up"}) == 2
        seen.add(payload["proof"])
    assert seen == set(trust._STOP_PROOFS), "a documented proof is never produced (or vice versa)"


def test_a_superseded_reply_is_moved_not_kept() -> None:
    reply = {"version": 1, "ok": False, "error": {"code": "unauthorized"}}
    result = trust._stop_success(dict(reply), Path("r"), "none")
    assert "error" not in result
    assert result["stop_reply_error"] == {"code": "unauthorized"}
    assert cli.stop_exit_code(result) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"running": None},
        {"running": False},  # no proof
        {"running": True, "stopped": True, "proof": "cooperative_refused"},
        {"running": "no", "proof": "no_metadata"},
        {"running": False, "proof": "no_metadata", "error": None},
        {"running": False, "proof": ["no_metadata"]},
    ],
    ids=repr,
)
def test_unknown_or_malformed_payloads_never_exit_0(payload: dict[str, Any]) -> None:
    assert cli.stop_exit_code(payload) == 2


def test_the_producers_reasons_and_proofs_are_all_in_the_table() -> None:
    """Derived from the code: every reason / proof string literal in the producer functions must be a
    member of the closed sets the exit table recognises."""

    def literals(func: Any) -> set[str]:
        tree = ast.parse(inspect.getsource(func).lstrip())
        return {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "_" in n.value
        }

    reasons = (literals(trust._unconfirmed_fields) | literals(trust._stale_unconfirmed)) - {
        "unconfirmed_reason",
        "stop_method",
    }
    assert reasons <= set(trust._UNCONFIRMED_REASONS), reasons - set(trust._UNCONFIRMED_REASONS)
    proofs = literals(trust._stop_success) | literals(trust._stale_success_fields)
    proofs = {p for p in proofs if p.endswith("_refused") or p == "no_metadata"}
    assert proofs <= set(trust._STOP_PROOFS), proofs - set(trust._STOP_PROOFS)


# ---- status goes through the same discipline ----


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"running": True, "host": "127.0.0.1", "port": 1, "pid": 2}, 0),
        ({"running": False, "discovered": False}, 0),  # no metadata at all
        ({"running": False, "stale_metadata": True, "endpoint_accepting_connections": False}, 2),
        ({"running": False, "stale_metadata": True, "endpoint_accepting_connections": True}, 2),
        ({"running": False, "endpoint_accepting_connections": True}, 2),
        ({"running": True, "error": {"code": "x"}}, 2),
        ({}, 2),
    ],
    ids=repr,
)
def test_status_exit_table(payload: dict[str, Any], code: int) -> None:
    assert cli.status_exit_code(payload) == code


# ---- the two reproduced round-7 findings, end to end ----


def _dead_pid() -> int:
    psutil = pytest.importorskip("psutil")
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(timeout=30)
    if psutil.pid_exists(done.pid):
        pytest.skip("the pid was recycled before the probe")
    return int(done.pid)


@pytest.mark.parametrize("variant", ["null", "missing"])
def test_repro1_a_missing_or_null_port_never_skips_the_confirmation(
    tmp_path: Path, variant: str
) -> None:
    """A live listener, a dead pid and a null / absent recorded port used to return running=False,
    delete the metadata, run zero refusal checks and exit 0."""
    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    meta: dict[str, Any] = {
        "version": 1,
        "package_version": _expected_tg_version(),
        "root": str(root),
        "host": "127.0.0.1",
        "pid": _dead_pid(),
        "started_at": "x",
        "token": "changed",
    }
    if variant == "null":
        meta["port"] = None
    sd._write_daemon_metadata(root, meta)
    try:
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is True, "an unverifiable endpoint was reported as not running"
        assert result["stopped"] is False
        assert result["unconfirmed_reason"] == "endpoint_unverifiable"
        assert cli.stop_exit_code(result) == 2
        assert sd._read_daemon_metadata(root) is not None  # daemon.json kept
    finally:
        server.shutdown()
        server.server_close()


class _DiesAfterUnauthorizedStop:
    """Answers the first signed ping, replies ``unauthorized`` to ``stop``, then the daemon is GONE:
    the listener closes and the recorded pid is dead."""

    def __init__(self, root: Path, dead_pid: int) -> None:
        secret = trust._load_or_create_user_secret()
        assert secret is not None
        outer = self
        self.pings = 0

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self_inner) -> None:
                try:
                    request = json.loads(self_inner.rfile.readline())
                except ValueError:
                    return
                command = request.get("command")
                if command == "ping":
                    outer.pings += 1
                    if outer.pings > 1:
                        return
                    port = int(outer.server.server_address[1])
                    proof = trust._daemon_ping_proof(
                        secret, request["nonce"], dead_pid, str(root), port
                    )
                    reply: dict[str, Any] = {
                        "version": 1,
                        "ok": True,
                        "pid": dead_pid,
                        "root": str(root),
                        "port": port,
                        "proof": proof,
                    }
                elif command == "stop":
                    reply = {"version": 1, "ok": False, "error": {"code": "unauthorized"}}
                    threading.Timer(0.05, outer.close).start()  # ... and then it dies
                else:
                    reply = {"version": 1, "ok": False}
                self_inner.wfile.write((json.dumps(reply) + "\n").encode())

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        sd._write_daemon_metadata(
            root,
            {
                "version": 1,
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": "127.0.0.1",
                "port": int(self.server.server_address[1]),
                "pid": dead_pid,
                "started_at": "x",
                "token": "tok",
            },
        )

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass


def test_repro2_a_structured_error_superseded_by_a_proven_shutdown_is_an_explicit_success(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    daemon = _DiesAfterUnauthorizedStop(root, _dead_pid())
    try:
        result = sd.stop_session_daemon(str(root))
        assert daemon.pings >= 1, "the signed probe never ran: the test is vacuous"
        assert result["running"] is False
        assert "error" not in result, "a structured daemon error was kept next to a success"
        assert result["stop_reply_error"] == {"code": "unauthorized"}
        assert result["proof"] in trust._STOP_PROOFS
        assert cli.stop_exit_code(result) == 0
    finally:
        daemon.close()


def test_the_cli_helper_routes_through_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unconfirmed = {
        "version": 1,
        "root": "r",
        "running": True,
        "stopped": False,
        "stop_method": "none",
        "unconfirmed_reason": "endpoint_unverifiable",
        "error": {"code": "unauthorized"},
    }
    monkeypatch.setattr(sd, "stop_session_daemon", lambda path: dict(unconfirmed))
    assert cli.run_session_daemon_stop(str(tmp_path), True, lambda p, **k: p) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "stop_unconfirmed"
    assert out["stop_reply_error"] == {"code": "unauthorized"}
    # a success with a stray error is never exit 0 either
    stray = {"running": False, "stopped": False, "stop_method": "none", "proof": "no_metadata"}
    monkeypatch.setattr(sd, "stop_session_daemon", lambda path: {**stray, "error": {"code": "x"}})
    assert cli.run_session_daemon_stop(str(tmp_path), True, lambda p, **k: p) == 2
