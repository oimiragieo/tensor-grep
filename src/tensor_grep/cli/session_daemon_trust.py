"""Endpoint trust + rebuild-failure helpers for the session daemon (bug-hunt F-01 / F-03).

``daemon.json`` lives inside the repo, so an attacker who can plant a file there controls the
host, port and token a client will dial. The only unforgeable proof of "this is MY daemon" is a
secret that never lives in the repo: a per-user secret file in the user's state directory
(owner-only), from which the daemon derives ``HMAC(secret, nonce|pid|root|port)`` for a fresh
client nonce. The proof binds the daemon's OWN listening port and the client accepts it only for
the port it actually connected to, so a loopback relay forwarding to the genuine daemon is
rejected.

Kept out of ``session_daemon.py`` because that file is size-ratcheted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import stat as _stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from tensor_grep.cli import session_daemon_winsec as _winsec
from tensor_grep.cli._index_lock import replace_with_retry
from tensor_grep.cli.session_store import _write_json_atomic

DAEMON_HOST = "127.0.0.1"
_DAEMON_SECRET_DIR_ENV = "TG_DAEMON_SECRET_DIR"
_DAEMON_SECRET_FILE = "daemon-secret.json"
_SECRET_FILE_MODE = 0o600

# OS seams (monkeypatched in tests): current token user SID, and (owner SID, granted SIDs).
_win_current_user_sid = _winsec.current_user_sid
_win_owner_and_dacl = _winsec.owner_and_dacl_sids
_win_dacl_entries = _winsec.owner_and_dacl_entries  # (owner SID, [(SID, access mask)])
_lstat = os.lstat  # private seam: tests patch this, never the global os.lstat


def _valid_daemon_port(value: object) -> int | None:
    """``value`` iff it is exactly an int in 1..65535 -- never coerced (True, 4242.9, "80", inf)."""
    return value if type(value) is int and 1 <= value <= 65535 else None


_DAEMON_MODULE = "tensor_grep.cli.session_daemon"


def _argv_serves_root(cmdline: list[str], root: Path) -> bool:
    """True iff the (last, argparse-style) ``--root`` in ``cmdline`` resolves to ``root``.

    Both sides go through ``normcase(realpath(...))`` so spelling, relative paths and (on Windows)
    case cannot make two different roots look equal or one root look different.
    """
    value: str | None = None
    for index, arg in enumerate(cmdline):
        if arg == "--root":
            value = cmdline[index + 1] if index + 1 < len(cmdline) else None
        elif arg.startswith("--root="):
            value = arg[len("--root=") :]
    if not value:
        return False
    try:
        return os.path.normcase(os.path.realpath(value)) == os.path.normcase(os.path.realpath(root))
    except (OSError, ValueError):
        return False


def _process_cmdline(pid: int) -> list[str]:
    """argv of ``pid`` via psutil (test seam). ``LookupError``: the process is gone. ``OSError``:
    it cannot be read (psutil missing, AccessDenied, ...) -- the caller must then NOT signal."""
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception as exc:
        raise OSError("psutil unavailable") from exc
    try:
        return [str(arg) for arg in psutil.Process(pid).cmdline()]
    except psutil.NoSuchProcess as exc:
        raise LookupError(pid) from exc
    except Exception as exc:
        raise OSError(str(exc)) from exc


def _daemon_pid_state(metadata: dict[str, Any] | None, root: Path | None) -> str:
    """``"ours"`` (provably the tensor-grep daemon serving ``root``), ``"gone"`` (no such process
    or not a tensor-grep daemon at all) or ``"unverifiable"`` (alive but not provably ours:
    another root's daemon, an unreadable argv, no psutil, or no root given). Only ``"ours"`` may
    ever be signalled."""
    try:
        pid = int((metadata or {})["pid"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return "gone"
    if pid <= 0 or pid == os.getpid():
        return "gone"
    try:
        cmdline = _process_cmdline(pid)
    except LookupError:
        return "gone"
    except Exception:
        return "unverifiable"
    if _DAEMON_MODULE not in cmdline:
        return "gone"
    return "ours" if root is not None and _argv_serves_root(cmdline, root) else "unverifiable"


def _await_endpoint_refused(
    host: object, port: object, timeout_seconds: float, connect_timeout: float = 3.0
) -> bool:
    """True once a connection to the daemon endpoint is REFUSED (bounded by ``timeout_seconds``).

    Only a refusal is evidence that the listener is gone: a successful connect (still serving),
    a connect timeout or any other error is "cannot tell" and is never treated as stopped. The
    connect timeout is ~3s because a refusal on Windows can take around 2s. At least one attempt
    is always made; an invalid host/port cannot be confirmed (False).
    """
    valid = _valid_daemon_port(port)
    if valid is None or not _is_loopback_host(host):
        return False
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            socket.create_connection((DAEMON_HOST, valid), timeout=connect_timeout).close()
        except ConnectionRefusedError:
            return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _is_loopback_host(host: object) -> bool:
    # "Any loopback" is not enough -- a relay on 127.0.0.2 can share the genuine daemon's port.
    # The daemon always binds DAEMON_HOST, so accept exactly that.
    return str(host) == DAEMON_HOST


def _daemon_secret_path() -> Path:
    # Depends only on LOCALAPPDATA (Windows) / Path.home() (POSIX) -- no XDG_STATE_HOME -- so the
    # client and the daemon it spawned (same environment) always agree on the path.
    override = os.environ.get(_DAEMON_SECRET_DIR_ENV)
    if override:
        return Path(override).expanduser() / _DAEMON_SECRET_FILE
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "tensor-grep" / _DAEMON_SECRET_FILE
    return Path.home() / ".local" / "state" / "tensor-grep" / _DAEMON_SECRET_FILE


_MAX_SECRET_FILE_BYTES = 8192
_WIN_ALLOWED_DACL_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})  # SYSTEM, Administrators


def _windows_handle_trusted(handle: Any, *, check_dacl: bool) -> bool:
    """Owner == this process token's user, and (``check_dacl``) the DACL grants nobody else.

    Fails CLOSED: an API error, an unreadable token, a NULL DACL or any ACE for a SID outside
    {current user, SYSTEM, Administrators} makes the object untrusted. Platform-independent logic
    over two OS seams (``_win_current_user_sid`` / ``_win_owner_and_dacl``) so it is unit-testable.
    """
    user_sid = _win_current_user_sid()
    queried = _win_owner_and_dacl(handle)
    if user_sid is None or queried is None:
        return False
    owner_sid, granted = queried
    if owner_sid != user_sid:
        return False
    if check_dacl:
        allowed = _WIN_ALLOWED_DACL_SIDS | {user_sid}
        if any(sid not in allowed for sid in granted):
            return False
    return True


def _between_validate_and_read(path: Path) -> None:
    """Test seam: runs after the opened object was validated and before it is read."""


def _parent_trusted(parent: Path) -> bool:
    """The secret's directory is a real directory (no symlink/junction) owned by this user."""
    if sys.platform == "win32":
        handle = _winsec.open_no_follow(str(parent), directory=True)
        if handle is None:
            return False
        try:
            return _windows_handle_trusted(handle, check_dacl=False)
        finally:
            _winsec.close_handle(handle)
    try:
        st = os.lstat(parent)  # lstat: a symlinked directory is not a directory here
    except OSError:
        return False
    return (
        _stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o022
    )  # not group/world-writable


_MAX_ANCESTOR_DEPTH = 64
# Everyone, BUILTIN\Users, Authenticated Users: granting these write access to an ancestor lets any
# local account rename/replace the subtree beneath it.
_WIN_BROAD_SIDS = frozenset({"S-1-1-0", "S-1-5-32-545", "S-1-5-11"})
# WRITE_DATA|APPEND_DATA|WRITE_EA|DELETE_CHILD|WRITE_ATTRIBUTES|DELETE|WRITE_DAC|WRITE_OWNER
# |GENERIC_WRITE|GENERIC_ALL
_WIN_WRITE_MASK = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000
_WIN_WRITE_MASK |= 0x40000000 | 0x10000000


def _windows_ancestor_dacl_ok(entries: list[tuple[str, int]]) -> bool:
    """No allow-ACE gives write/modify access to Everyone, Users or Authenticated Users.

    A NULL DACL or an allow-ACE type this code does not parse is refused (fail closed).
    """
    for sid, mask in entries:
        if sid.startswith(("NULL-DACL", "UNPARSED-ACE-TYPE-")):
            return False
        if sid in _WIN_BROAD_SIDS and mask & _WIN_WRITE_MASK:
            return False
    return True


def _posix_dir_ok(st: os.stat_result) -> bool:
    if sys.platform == "win32":
        return False
    euid = os.geteuid()
    if not _stat.S_ISDIR(st.st_mode) or st.st_uid not in (euid, 0):
        return False
    return not st.st_mode & 0o022 or bool(st.st_mode & _stat.S_ISVTX)  # writable => sticky (/tmp)


def _ancestors_trusted(parent: Path) -> bool:
    """StrictModes-style: every ancestor of the secret's directory up to the root is trustworthy.

    POSIX: a real directory owned by the user or root (a symlink is allowed only if root-owned,
    e.g. /var -> /private/var, and the RESOLVED chain is then checked too); group/world-writable
    is refused unless sticky. Windows: not a reparse point and no write/modify grant to Everyone,
    Users or Authenticated Users; the drive root is exempt. Bounded; any error fails closed.
    """
    path = Path(os.path.abspath(parent))
    if len(path.parts) > _MAX_ANCESTOR_DEPTH:
        return False
    if sys.platform == "win32":
        for anc in path.parents:
            if anc.parent == anc:
                continue  # drive root: a system root, not attacker-modifiable
            handle = _winsec.open_no_follow(str(anc), directory=True)
            if handle is None:  # error, or the ancestor is a reparse point (symlink/junction)
                return False
            try:
                queried = _win_dacl_entries(handle)
            finally:
                _winsec.close_handle(handle)
            if queried is None or not _windows_ancestor_dacl_ok(queried[1]):
                return False
        return True
    try:
        for anc in path.parents:
            st = _lstat(anc)
            if _stat.S_ISLNK(st.st_mode):
                if st.st_uid != 0:
                    return False
                continue
            if not _posix_dir_ok(st):
                return False
        real = Path(os.path.realpath(path))
        if len(real.parts) > _MAX_ANCESTOR_DEPTH:
            return False
        return all(_posix_dir_ok(_lstat(anc)) for anc in (real, *real.parents))
    except OSError:
        return False


def _read_secret_bytes(path: Path) -> bytes | None:
    """Open ONCE without following links, validate that opened object, read through the SAME fd."""
    if not _parent_trusted(path.parent) or not _ancestors_trusted(path.parent):
        return None
    if sys.platform == "win32":
        handle = _winsec.open_no_follow(str(path))
        if handle is None:
            return None
        try:
            if not _windows_handle_trusted(handle, check_dacl=True):
                return None
            _between_validate_and_read(path)
            return _winsec.read_all(handle)
        finally:
            _winsec.close_handle(handle)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            return None
        _between_validate_and_read(path)
        data = os.read(fd, _MAX_SECRET_FILE_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= _MAX_SECRET_FILE_BYTES else None


def _read_user_secret(path: Path) -> bytes | None:
    data = _read_secret_bytes(path)
    if data is None:
        return None
    try:
        raw = json.loads(data.decode("utf-8")).get("secret")
    except (ValueError, AttributeError):
        return None
    return (
        raw.encode("ascii") if isinstance(raw, str) and len(raw) >= 32 and raw.isascii() else None
    )


def _write_secret_windows(path: Path, payload: dict[str, Any]) -> None:
    """Create the secret with an owner-only DACL applied BEFORE any secret byte is written.

    Unlike the daemon.json writer (icacls failures are non-fatal there), a failed ACL application
    is FATAL here: the temp file is removed and nothing is published.
    """
    sid = _win_current_user_sid()
    if sid is None:
        raise OSError("cannot determine the current user SID; refusing to create the secret")
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            try:
                done = subprocess.run(
                    ["icacls", str(tmp), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                    check=False,
                    capture_output=True,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise OSError(f"icacls failed for the daemon secret: {exc}") from exc
            if done.returncode != 0:
                raise OSError(f"icacls exited {done.returncode}; refusing to create the secret")
            handle.write(json.dumps(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace_with_retry(tmp, path)


def _load_or_create_user_secret() -> bytes | None:
    path = _daemon_secret_path()
    existing = _read_user_secret(path)
    if existing is not None:
        return existing
    if os.path.lexists(path):
        return None  # present but untrusted (or unreadable): never use it, never overwrite it
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _parent_trusted(path.parent) or not _ancestors_trusted(path.parent):
            return None
        payload = {"secret": secrets.token_hex(32)}
        if sys.platform == "win32":
            _write_secret_windows(path, payload)
        else:
            _write_json_atomic(path, payload, mode=_SECRET_FILE_MODE)
    except OSError:
        return None
    return _read_user_secret(path)


def _daemon_ping_proof(secret: bytes, nonce: str, pid: int, root: str, port: int) -> str:
    msg = "\n".join(("tg-daemon-ping-v1", nonce, str(pid), root, str(port))).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _verify_ping_reply(
    response: dict[str, Any], nonce: str, root: Path, connected_port: int
) -> bool:
    secret = _read_user_secret(_daemon_secret_path())
    pid, reply_root = response.get("pid"), response.get("root")
    port, proof = response.get("port"), response.get("proof")
    if secret is None:
        return False
    if isinstance(pid, bool) or not isinstance(pid, int):
        return False
    if isinstance(port, bool) or not isinstance(port, int):
        return False
    if port != connected_port:  # relay defence: the proof must be for the endpoint we dialled
        return False
    if not isinstance(reply_root, str) or not isinstance(proof, str) or not proof.isascii():
        return False
    if os.path.normcase(reply_root) != os.path.normcase(str(root)):
        return False
    expected = _daemon_ping_proof(secret, nonce, pid, reply_root, port)
    # ASCII bytes: hmac.compare_digest(str, str) raises TypeError on non-ASCII input.
    return hmac.compare_digest(proof.encode("ascii"), expected.encode("ascii"))


def _ping_proof_fields(nonce: object, root: Path, own_port: int) -> dict[str, Any]:
    """Daemon side: the pid/root/port/proof fields for a ping that carried a valid nonce."""
    if not (isinstance(nonce, str) and 16 <= len(nonce) <= 64 and nonce.isascii()):
        return {}
    secret = _load_or_create_user_secret()
    if secret is None:
        return {}
    pid = os.getpid()
    return {
        "pid": pid,
        "root": str(root),
        "port": own_port,
        "proof": _daemon_ping_proof(secret, nonce, pid, str(root), own_port),
    }


class _DaemonRefreshFailed(Exception):
    """A stale-session rebuild failed; carries the trigger so the caller can disclose it."""

    def __init__(self, trigger: str, original_error: str, rebuild_error: str) -> None:
        super().__init__(f"rebuild after {trigger} failed: {rebuild_error}")
        self.trigger = trigger
        self.original_error = original_error
        self.rebuild_error = rebuild_error


def _refresh_failed_error(failed: _DaemonRefreshFailed) -> dict[str, Any]:
    return {
        "code": "refresh_failed",
        "message": str(failed),
        "refresh_trigger": failed.trigger,
        "original_error": failed.original_error,
        "rebuild_error": failed.rebuild_error,
    }
