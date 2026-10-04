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

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import signal
import socket
import stat as _stat
import sys
import time
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from tensor_grep.cli import session_daemon_winsec as _winsec
from tensor_grep.cli._index_lock import (
    IndexLockTimeoutError,
    _publish_bytes_no_clobber,
    atomic_write_bytes_anchored,
    index_lock,
)
from tensor_grep.cli.runtime_paths import _expected_tg_version

DAEMON_HOST = "127.0.0.1"
_DAEMON_SECRET_DIR_ENV = "TG_DAEMON_SECRET_DIR"
_DAEMON_SECRET_FILE = "daemon-secret.json"
_SECRET_FILE_MODE = 0o600

# OS seams (monkeypatched in tests): current token user SID, and (owner SID, granted SIDs).
_win_current_user_sid = _winsec.current_user_sid
_win_owner_and_dacl = _winsec.owner_and_dacl_sids
_win_dacl_entries = _winsec.owner_and_dacl_entries  # (owner SID, [(SID, access mask)])
_win_create_restricted = _winsec.create_new_restricted  # CREATE_NEW with a user-only descriptor
_lstat = os.lstat  # private seam: tests patch this, never the global os.lstat


def _valid_daemon_port(value: object) -> int | None:
    """``value`` iff it is exactly an int in 1..65535 -- never coerced (True, 4242.9, "80", inf)."""
    return value if type(value) is int and 1 <= value <= 65535 else None


_DAEMON_MODULE = "tensor_grep.cli.session_daemon"
_PYTHON_NAME = re.compile(r"^pythonw?(\d+(\.\d+)*)?(\.exe)?$", re.IGNORECASE)
_PY_NOARG_FLAGS = frozenset("BdEiIOPqsSuvxbR")  # interpreter flags that take no argument
_PY_ARG_FLAGS = frozenset("WX")  # -W/-X take an argument (attached or the next argv element)


class _DaemonArgs(argparse.ArgumentParser):
    """The daemon's own grammar (``session_daemon._parse_args``), raising instead of exiting."""

    def error(self, message: str) -> NoReturn:
        raise ValueError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise ValueError(message or "exit")


def _daemon_invocation_root(argv: list[str]) -> str | None:
    """The ``--root`` value iff ``argv`` is EXACTLY a daemon launch, else ``None``.

    ``<python> [interpreter options] -m tensor_grep.cli.session_daemon <daemon args>`` where
    ``-m`` is the module selector (not ``-c``, not a script path, not ``--``, not the module name
    appearing as an argument to something else) and the daemon args bind through the same argparse
    grammar the daemon uses (so abbreviations and "last one wins" behave identically).
    """
    if not argv or not _PYTHON_NAME.match(argv[0].replace("\\", "/").rsplit("/", 1)[-1]):
        return None
    index, module = 1, None
    while module is None:
        if index >= len(argv):
            return None
        arg = argv[index]
        if len(arg) < 2 or arg[0] != "-" or arg.startswith("--"):
            return None  # a script path, "-", "--" or a long option: not a module launch
        index += 1
        for pos in range(1, len(arg)):
            flag = arg[pos]
            if flag == "m":
                if arg[pos + 1 :]:
                    module = arg[pos + 1 :]
                elif index < len(argv):
                    module, index = argv[index], index + 1
                else:
                    return None
                break
            if flag in _PY_ARG_FLAGS:
                if not arg[pos + 1 :]:
                    if index >= len(argv):
                        return None
                    index += 1
                break
            if flag not in _PY_NOARG_FLAGS:
                return None  # -c, -h, -V or anything unknown
    if module != _DAEMON_MODULE:
        return None
    parser = _DaemonArgs(add_help=True)
    parser.add_argument("--root", required=True)
    try:
        return str(parser.parse_args(argv[index:]).root)
    except ValueError:
        return None


def _argv_serves_root(cmdline: list[str], root: Path) -> bool:
    """True iff ``cmdline`` is a genuine daemon launch whose ``--root`` is ``root``.

    The root must be absolute (the daemon is always launched with one; a relative value would be
    resolved against a cwd we cannot see) and both sides go through ``normcase(realpath(...))``.
    """
    value = _daemon_invocation_root(cmdline)
    if not value or not os.path.isabs(value):
        return False
    try:
        return os.path.normcase(os.path.realpath(value)) == os.path.normcase(os.path.realpath(root))
    except (OSError, ValueError):
        return False


def _process_info(pid: int) -> tuple[list[str], float]:
    """``(argv, create_time)`` of ``pid`` via psutil (test seam). ``LookupError``: the process is
    gone. ``OSError``: it cannot be read (psutil missing, AccessDenied, ...) -- the caller must
    then NOT signal."""
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception as exc:
        raise OSError("psutil unavailable") from exc
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            return [str(arg) for arg in process.cmdline()], float(process.create_time())
    except psutil.NoSuchProcess as exc:
        raise LookupError(pid) from exc
    except Exception as exc:
        raise OSError(str(exc)) from exc


def _classify_daemon_pid(
    metadata: dict[str, Any] | None, root: Path | None
) -> tuple[str, tuple[int, float, list[str]] | None]:
    """``(state, identity)``. ``"ours"``: provably the tensor-grep daemon serving ``root`` (identity
    = pid, create_time, argv). ``"gone"``: ONLY independently established absence -- nothing is
    recorded, or the OS reports that no process with the recorded pid exists (psutil
    ``NoSuchProcess``). ``"unverifiable"``: everything else, notably a LIVE process that is
    unrelated, is the caller's own pid, or whose recorded identity is malformed or unreadable (no
    psutil, AccessDenied): it proves nothing about the daemon the metadata names, so the caller
    keeps the metadata and reports the stop as unconfirmed. Only ``"ours"`` may ever be signalled."""
    if not metadata:
        return "gone", None
    pid = metadata.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return "unverifiable", None
    try:
        argv, created = _process_info(pid)
    except LookupError:
        return "gone", None  # the OS says no such process: independently established absence
    except Exception:
        return "unverifiable", None
    if not any(_DAEMON_MODULE in arg for arg in argv):
        return "unverifiable", None  # a live, unrelated process says nothing about our daemon
    if root is not None and _argv_serves_root(argv, root):
        # argv cannot prove which code is running (a shadow package passes every argv check): the
        # real daemon's HMAC over (pid, create_time, port, root, version) can, and the live
        # process must still have the signed create time.
        signed = (metadata or {}).get("create_time")
        if (
            _verify_attestation(metadata or {}, root)
            and isinstance(signed, (int, float))
            and abs(created - float(signed)) <= _PID_CREATE_TIME_TOLERANCE
        ):
            return "ours", (pid, created, argv)
    return "unverifiable", None


def _daemon_pid_state(metadata: dict[str, Any] | None, root: Path | None) -> str:
    return _classify_daemon_pid(metadata, root)[0]


_pidfd_open = getattr(os, "pidfd_open", None)  # Linux 5.3+ (Python 3.9+); seam for tests
_pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)  # seam for tests
_PID_CREATE_TIME_TOLERANCE = 0.01  # seconds; psutil and GetProcessTimes read the same FILETIME
_last_guard_level: str | None = None


def _last_pid_guard_level() -> str | None:
    """Containment level used by the most recent real pid escalation (``None`` if none)."""
    return _last_guard_level


def _pid_guard_field(used: bool) -> dict[str, str]:
    """``{"pid_reuse_guard": level}`` for a stop result, only when a pid escalation delivered."""
    return {"pid_reuse_guard": _last_guard_level or "unknown"} if used else {}


def _psutil_process_cls() -> Any:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return None
    return psutil.Process


class _PidGuard:
    """``"recheck"``: no kernel object pins the pid, so the create_time/argv re-read is the guard
    and a window of microseconds remains between that re-read and the signal."""

    level = "recheck"

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def verify(self, created: float) -> bool:
        return True  # the caller re-reads _process_info after the guard exists

    def terminate(self) -> bool:
        process_cls = _psutil_process_cls()
        if process_cls is not None:
            process_cls(self.pid).terminate()  # psutil itself refuses a pid whose create_time moved
        else:
            os.kill(self.pid, signal.SIGTERM)
        return True

    def wait(self, seconds: float) -> None:
        return None

    def close(self) -> None:
        return None


class _PidfdGuard(_PidGuard):
    """``"pidfd"`` (Linux 5.3+): an open pidfd pins the process; the signal goes through it."""

    level = "pidfd"

    def __init__(self, pid: int, fd: int) -> None:
        super().__init__(pid)
        self.fd = fd

    def terminate(self) -> bool:
        assert _pidfd_send_signal is not None
        _pidfd_send_signal(self.fd, signal.SIGTERM)
        return True

    def wait(self, seconds: float) -> None:
        select.select([self.fd], [], [], seconds)  # a pidfd becomes readable when the process exits

    def close(self) -> None:
        os.close(self.fd)


class _WinGuard(_PidGuard):
    """``"handle"`` (Windows): an open process handle pins the process. The create time is read
    from THAT handle and ``TerminateProcess`` is called on THAT handle, never a fresh OpenProcess."""

    level = "handle"

    def __init__(self, pid: int, handle: Any) -> None:
        super().__init__(pid)
        self.handle = handle

    def verify(self, created: float) -> bool:
        actual = _winsec.process_create_time(self.handle)
        return actual is not None and abs(actual - created) <= _PID_CREATE_TIME_TOLERANCE

    def terminate(self) -> bool:
        return bool(_winsec.terminate_process(self.handle))

    def wait(self, seconds: float) -> None:
        _winsec.wait_process(self.handle, int(seconds * 1000))

    def close(self) -> None:
        _winsec.close_handle(self.handle)


def _open_pid_guard(pid: int) -> _PidGuard | None:
    """The strongest available guard for ``pid``; ``None`` if the process cannot be opened."""
    if sys.platform == "win32":
        handle = _winsec.open_process(pid)
        return _WinGuard(pid, handle) if handle is not None else None
    if _pidfd_open is not None and _pidfd_send_signal is not None:
        try:
            return _PidfdGuard(pid, _pidfd_open(pid))
        except ProcessLookupError:
            return None
        except OSError:
            pass  # EPERM / ENOSYS / EINVAL: fall back to the create_time re-check
    return _PidGuard(pid)


def _terminate_identified(
    identity: tuple[int, float, list[str]], wait_seconds: float = 5.0
) -> bool:
    """Signal the classified process, pinned against PID reuse by the strongest available primitive.

    The guard (Windows process handle / Linux pidfd) is opened FIRST and only then are the create
    time and argv re-verified against the classification; from that moment the pid cannot name a
    different process, so the signal sent through the same handle/pidfd hits exactly the verified
    one. Without either primitive (macOS, old kernels) the create_time re-check remains and the
    stop result records ``pid_reuse_guard: "recheck"``. The guard is always released.
    """
    global _last_guard_level
    pid, created, argv = identity
    _last_guard_level = None
    guard = _open_pid_guard(pid)
    if guard is None:
        return False
    try:
        if not guard.verify(created) or _process_info(pid) != (argv, created):
            return False
        if not guard.terminate():
            return False
        _last_guard_level = guard.level
        guard.wait(wait_seconds)  # bounded; the caller still requires a refused connection
        return True
    except Exception:
        return False
    finally:
        guard.close()


def _attestation_hmac(
    secret: bytes, pid: int, created: float, port: int, root: str, package_version: str
) -> str:
    """HMAC over (pid, create_time, port, canonical root, package_version) under the user secret."""
    msg = "\n".join((
        "tg-daemon-attest-v1",
        str(pid),
        f"{created:.6f}",
        str(port),
        os.path.normcase(root),
        package_version,
    )).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _attestation_fields(root: Path, port: int) -> dict[str, Any]:
    """Daemon side: ``create_time`` + ``attestation`` for daemon.json (``{}`` if unavailable).

    Written only by the REAL daemon, which alone holds the per-user secret at startup. A decoy, a
    shadow package or a planted daemon.json cannot produce it without that secret."""
    secret = _load_or_create_user_secret()
    if secret is None:
        return {}
    try:
        _argv, created = _process_info(os.getpid())
    except (LookupError, OSError):
        return {}
    version = _expected_tg_version()
    return {
        "create_time": created,
        "attestation": _attestation_hmac(secret, os.getpid(), created, port, str(root), version),
    }


def _verify_attestation(metadata: dict[str, Any], root: Path) -> bool:
    """True iff ``metadata`` carries an attestation that verifies under the user secret for THIS
    root. Any missing/mistyped/changed field -- including metadata written by an older daemon that
    has no attestation at all -- is unproven (fail closed)."""
    secret = _read_user_secret(_daemon_secret_path())
    pid, created = metadata.get("pid"), metadata.get("create_time")
    port, attestation = _valid_daemon_port(metadata.get("port")), metadata.get("attestation")
    version = metadata.get("package_version")
    if secret is None or port is None or not isinstance(version, str):
        return False
    if isinstance(pid, bool) or not isinstance(pid, int):
        return False
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return False
    if not isinstance(attestation, str) or not attestation.isascii():
        return False
    expected = _attestation_hmac(secret, pid, float(created), port, str(root), version)
    # ASCII bytes: hmac.compare_digest(str, str) raises TypeError on non-ASCII input.
    return hmac.compare_digest(attestation.encode("ascii"), expected.encode("ascii"))


def _unconfirmed_fields(state: str, delivered: bool) -> dict[str, Any]:
    """The honest stop result when shutdown cannot be confirmed: still running, not stopped."""
    if delivered:
        reason = "endpoint_still_accepting_connections"
    else:
        reason = {"unverifiable": "pid_unproven", "ours": "termination_failed"}.get(
            state, "stop_not_confirmed"
        )
    return {"running": True, "stopped": False, "stop_method": "none", "unconfirmed_reason": reason}


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
            if not _windows_handle_trusted(handle, check_dacl=False):
                return False
            queried = _win_dacl_entries(handle)  # the directory's OWN DACL too, not just owner
            return queried is not None and _windows_ancestor_dacl_ok(queried[1])
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
    """Create the secret so it is NEVER broader than the current user, not even for an instant.

    The temp file is created with ``CreateFileW(CREATE_NEW)`` and an explicit protected descriptor
    (``D:P(A;;FA;;;<user>)``), share mode 0: there is no window in which it carries an inherited
    broad DACL that a pre-opened handle could outlive (tightening a DACL later does not revoke
    handles already open). The descriptor is re-read from the open handle before any byte is
    written; anything unexpected removes the temp file and nothing is published.
    """
    sid = _win_current_user_sid()
    if sid is None:
        raise OSError("cannot determine the current user SID; refusing to create the secret")
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    handle = _win_create_restricted(str(tmp), sid)
    if handle is None:
        raise OSError("cannot create the restricted secret file; refusing to create the secret")
    try:
        queried = _win_owner_and_dacl(handle)
        if queried is None or queried[0] != sid or any(granted != sid for granted in queried[1]):
            raise OSError("created secret has an unexpected security descriptor; refusing")
        if not _winsec.write_all(handle, json.dumps(payload).encode("utf-8")):
            raise OSError("cannot write the secret file")
    except BaseException:
        _winsec.close_handle(handle)
        tmp.unlink(missing_ok=True)
        raise
    _winsec.close_handle(handle)  # exclusive until now; the publish below needs it closed
    try:
        _publish_bytes_no_clobber(tmp, path)  # hard link: fails if the secret already exists
    finally:
        tmp.unlink(missing_ok=True)


def _after_parent_pinned(parent: Path) -> None:
    """Test seam: runs while the parent directory handle is held, before the secret is created."""


def _write_secret_posix(path: Path, payload: dict[str, Any]) -> None:
    """Publish the secret 0600 WITHOUT replacing an existing one (hard-link no-clobber)."""
    atomic_write_bytes_anchored(
        path, json.dumps(payload).encode("utf-8"), mode=_SECRET_FILE_MODE, replace=False
    )


def _load_or_create_user_secret() -> bytes | None:
    """The per-user secret; created at most ONCE across every root/daemon of this user.

    Creation is serialized with the repo's lock primitive (``index_lock``) in the same trusted
    directory, absence is re-checked UNDER the lock, and the secret is published without replacing
    an existing one; a secret that appeared meanwhile is read (normal trust checks) and used, so two
    concurrent first uses can never invalidate attestations the other already signed.
    """
    path = _daemon_secret_path()
    existing = _read_user_secret(path)
    if existing is not None:
        return existing
    if os.path.lexists(path):
        return None  # present but untrusted (or unreadable): never use it, never overwrite it
    pinned: Any = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform == "win32":
            # Hold the directory open through creation and publish so it cannot be renamed or
            # deleted underneath us (no FILE_SHARE_DELETE); FILE_SHARE_WRITE is required or the
            # rename of the temp file into place inside it is refused. Vet it AFTER pinning.
            pinned = _winsec.open_no_follow(str(path.parent), directory=True, share=0x3)
            if pinned is None:
                return None
        if not _parent_trusted(path.parent) or not _ancestors_trusted(path.parent):
            return None
        _after_parent_pinned(path.parent)
        with index_lock(path):
            existing = _read_user_secret(path)  # re-check UNDER the lock, full trust checks
            if existing is not None:
                return existing
            if os.path.lexists(path):
                return None
            payload = {"secret": secrets.token_hex(32)}
            try:
                if sys.platform == "win32":
                    _write_secret_windows(path, payload)
                else:
                    _write_secret_posix(path, payload)
            except FileExistsError:
                pass  # lost a race the lock did not cover (e.g. a reclaimed stale lock): use theirs
    except (OSError, IndexLockTimeoutError):
        return None
    finally:
        if pinned is not None:
            _winsec.close_handle(pinned)
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
