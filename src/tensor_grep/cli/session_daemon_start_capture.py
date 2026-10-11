"""Private stderr capture and subprocess spawning for the session daemon."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


def spawn_daemon_subprocess(
    root: Path,
    sessions_dir: Path,
    remove_metadata: Callable[[Path], None],
) -> tuple[int, Path]:
    """Spawn a daemon with an exclusive, uniquely named stderr capture file."""
    # Task #143a-a: unconditional (force) removal is intentional and safe here -- every caller
    # holds the daemon-start lock for `root` (excluding any concurrent spawn), so nothing else can
    # be publishing fresh metadata for this root while we hold it, and we are about to Popen a
    # brand-new instance that will publish its OWN metadata momentarily. There is no prior "self"
    # identity to compare against yet, unlike the guarded callers in stop_session_daemon and
    # run_session_daemon_server's shutdown path below.
    remove_metadata(root)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # audit B20: only inject the editable-checkout 'src' onto PYTHONPATH when it actually
    # exists (an installed wheel has no sibling src/), and derive cwd from the session root
    # rather than assuming a fixed Path(__file__).parents[3] repo layout.
    repo_src = Path(__file__).resolve().parents[3] / "src"
    if repo_src.exists():
        python_path_parts = [str(repo_src)]
        if env.get("PYTHONPATH"):
            python_path_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    spawn_cwd = root if root.is_dir() else root.parent
    # audit I7: detach the daemon from the launching process group/console so it is not
    # killed by signals delivered to the parent and survives the CLI invocation.
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    capture_path = sessions_dir / f"daemon-start.{uuid4().hex}.stderr"
    # O_EXCL rejects a planted symlink or junction at this unique name; the fd is never
    # re-opened by path after creation.
    capture_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_TEMPORARY", 0)
    )
    capture_fd = os.open(capture_path, capture_flags, 0o600)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.session_daemon",
                "--root",
                str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=capture_fd,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
            cwd=str(spawn_cwd),
            env=env,
        )
    except BaseException:
        os.close(capture_fd)
        capture_path.unlink(missing_ok=True)
        raise
    return capture_fd, capture_path


def read_daemon_start_capture(capture_fd: int) -> bytes:
    capture_size = os.fstat(capture_fd).st_size
    os.lseek(capture_fd, max(0, capture_size - 4096), os.SEEK_SET)
    return os.read(capture_fd, 4096)


def cleanup_daemon_start_capture(capture_fd: int, capture_path: Path) -> None:
    with suppress(OSError):
        os.close(capture_fd)
    with suppress(OSError):
        capture_path.unlink(missing_ok=True)
