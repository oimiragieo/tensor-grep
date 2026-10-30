from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock as locks
from tensor_grep.cli.codemap_pinned import PinnedDirectory
from tensor_grep.cli.codemap_publication import LOCK_NAME, publication_lock


def _snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    return {
        str(path.relative_to(root)): (info.st_ino, info.st_size, info.st_mtime_ns)
        for path in [root, *root.rglob("*")]
        for info in [path.stat()]
    }


def test_persistent_locks_and_read_only_pins(tmp_path: Path) -> None:
    output, index = tmp_path / "output", tmp_path / "custom" / "index.md"
    with publication_lock(output, index, create=True) as state:
        assert state.unchanged()
        (output / "page.md").write_bytes(b"page")
        index.write_bytes(b"index")
    assert not state.unchanged()
    before = _snapshot(tmp_path)
    for create in (False, True, False):
        with publication_lock(output, index, create=create) as state:
            assert state.unchanged()
            assert state.output.read("page.md", 4) == b"page"
            assert state.index_parent.read(index.name, 5) == b"index"
    assert _snapshot(tmp_path) == before
    assert (output / LOCK_NAME).read_bytes() == (index.parent / LOCK_NAME).read_bytes() == b""


@pytest.mark.parametrize("existing", ["directory", "invalid-lock", "nothing"])
def test_reader_refuses_missing_or_invalid_locks_without_writes(
    tmp_path: Path, existing: str
) -> None:
    output = tmp_path / "output"
    if existing != "nothing":
        output.mkdir()
    if existing == "invalid-lock":
        (output / LOCK_NAME).mkdir()
    before = _snapshot(tmp_path)
    with pytest.raises(OSError, match="codemap publication lock unavailable"):
        with publication_lock(output, output / "index.md", create=False):
            pytest.fail("unavailable lock admitted a reader")
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("shared", [False, True])
def test_lock_order_is_sorted_and_shared_parent_is_locked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    output = tmp_path / "z"
    index = (output if shared else tmp_path / "a") / "index.md"
    opened: list[Path] = []
    original = PinnedDirectory.open_regular

    def open_regular(pin: PinnedDirectory, name: str) -> int:
        opened.append(pin.path)
        return original(pin, name)

    monkeypatch.setattr(PinnedDirectory, "open_regular", open_regular)
    with publication_lock(output, index, create=True) as state:
        assert state.unchanged()
        assert (state.output is state.index_parent) == shared
    assert opened == sorted({output, index.parent})


@pytest.mark.parametrize("stage", ["acquisition", "body"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_exception_releases_locks_and_directory_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, error_type: type[BaseException]
) -> None:
    output, index = tmp_path / "a", tmp_path / "z" / "index.md"
    before = locks._HELD_LOCK_FDS.copy()
    failure = error_type("interrupted publication")
    original = PinnedDirectory.open_regular

    def open_regular(pin: PinnedDirectory, name: str) -> int:
        if stage == "acquisition" and pin.path == index.parent:
            raise failure
        return original(pin, name)

    with monkeypatch.context() as patch:
        patch.setattr(PinnedDirectory, "open_regular", open_regular)
        with pytest.raises(error_type) as caught:
            with publication_lock(output, index, create=True):
                raise failure
        assert caught.value is failure or caught.value.__cause__ is failure
    assert locks._HELD_LOCK_FDS == before
    parked = tmp_path / "parked"
    output.rename(parked)
    parked.rename(output)
    with publication_lock(output, index, create=False) as state:
        assert state.unchanged()


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("overlap", ["output", "index-parent"])
def test_process_contention_and_exit_release(tmp_path: Path, create: bool, overlap: str) -> None:
    output, index = tmp_path / "z", tmp_path / "shared" / "index.md"
    other = tmp_path / "a"
    with publication_lock(other, index, create=True):
        pass
    script = """
import sys
from pathlib import Path
from tensor_grep.cli.codemap_publication import publication_lock
with publication_lock(Path(sys.argv[1]), Path(sys.argv[2]), create=True):
    print("ready", flush=True)
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(output), str(index)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready: queue.Queue[str] = queue.Queue()
    assert process.stdout is not None
    threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
    try:
        assert ready.get(timeout=15).strip() == "ready"
        target = output if overlap == "output" else other
        target_index = other / "index.md" if overlap == "output" else index
        with pytest.raises(OSError, match="contended"):
            with publication_lock(target, target_index, create=create):
                pytest.fail("overlapping publication admitted")
        # Failed acquisition must release the earlier, disjoint parent immediately.
        with publication_lock(other, other / "index.md", create=False) as state:
            assert state.unchanged()
        if create:
            process.terminate()  # OS cleanup, without running context-manager cleanup.
        else:
            assert process.stdin is not None
            process.stdin.write("x")
            process.stdin.flush()
        process.wait(timeout=15)
        with publication_lock(output, index, create=False) as state:
            assert state.unchanged()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork lifecycle")
def test_fork_context_cannot_be_used_or_release_child_locks(tmp_path: Path) -> None:
    script = """
import os, sys
from pathlib import Path
from tensor_grep.cli.codemap_publication import publication_lock
root = Path(sys.argv[1])
context = publication_lock(root / "parent", root / "parent" / "index.md", create=True)
with context as inherited:
    child = os.fork()
    if child == 0:
        try:
            assert not inherited.unchanged()
            with publication_lock(root / "child", root / "child" / "index.md", create=True) as own:
                context.__exit__(None, None, None)
                assert own.unchanged()
            os._exit(0)
        except BaseException:
            os._exit(1)
    assert os.waitpid(child, 0)[1] == 0
    assert inherited.unchanged()
with publication_lock(root / "parent", root / "parent" / "index.md", create=False) as current:
    assert current.unchanged()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_pinned_lock_descriptor_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / LOCK_NAME
    path.write_bytes(b"existing lock")
    with PinnedDirectory(tmp_path) as pin:
        fd = pin.open_regular(LOCK_NAME)
        try:
            assert pin.file_unchanged(LOCK_NAME, fd)
            with pytest.raises(OSError):
                os.write(fd, b"changed")
        finally:
            os.close(fd)
    assert path.read_bytes() == b"existing lock"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle ownership")
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_failed_windows_handle_transfer_closes_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    import msvcrt

    path = tmp_path / LOCK_NAME
    path.write_bytes(b"")
    failure = error_type("handle transfer failed")

    def fail(*args: object) -> int:
        raise failure

    with PinnedDirectory(tmp_path) as pin:
        monkeypatch.setattr(msvcrt, "open_osfhandle", fail)
        with pytest.raises(error_type) as caught:
            pin.open_regular(LOCK_NAME)
        assert caught.value is failure
    path.rename(tmp_path / "closed")
