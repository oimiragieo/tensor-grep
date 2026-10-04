from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from tensor_grep.cli import edit_ticket_service, edit_ticket_walk
from tensor_grep.cli.edit_ticket_service import (
    _walk_tracked_files_bounded,
    build_edit_ready_ticket,
    verify_edit_ticket,
)

# Handle / fd ownership, cleanup and error-contract tests split out of
# test_edit_ticket_walk_hardening.py (2000-line test budget).

_CACHEDIR_SIG = b"Signature: 8a477f597d28d172789f06886806bc55\n"
_BAD_TAG = b"Signature: 8a477f597d28d172789f06886806bc55 XX"  # a tag-named file, not a tag


def _ticket(tmp_path: Path):
    return build_edit_ready_ticket(
        repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"]
    )


# ---- round 23: every handle / fd acquired by a walk is owned by the stack or closed exactly once ----


class _OsProxy:
    """A stand-in for the `os` name INSIDE edit_ticket_service only (never the stdlib module):
    forwards everything to the real `os` except the explicitly overridden attributes."""

    def __init__(self, real: object, **overrides: object) -> None:
        self._real = real
        for name, value in overrides.items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


class _FakeEntry:
    def __init__(self, name: str, is_dir: bool) -> None:
        self.name = name
        self._is_dir = is_dir

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return self._is_dir


class _FakeScandir:
    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = entries

    def __enter__(self) -> list[_FakeEntry]:
        return self._entries

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_dir_stat(ino: int) -> os.stat_result:
    return os.stat_result((0o040755, ino, 1, 1, 0, 0, 0, 0, 0, 0))


class _FdWorld:
    """A fake POSIX fd world for `_fd_walk`: fd numbers are handed out by `open`, every close is
    recorded, and the failure points are injectable."""

    def __init__(self) -> None:
        self.next_fd = 100
        self.closed: list[int] = []
        self.tree: dict[int, list[_FakeEntry]] = {}
        self.fail_scandir: set[int] = set()
        self.fail_fstat: set[int] = set()
        self.fail_open_child = False

    def _alloc(self, entries: list[_FakeEntry]) -> int:
        fd = self.next_fd
        self.next_fd += 1
        self.tree[fd] = entries
        return fd

    def install(self, monkeypatch: pytest.MonkeyPatch, root_entries: list[_FakeEntry]) -> None:
        child_entries: dict[str, list[_FakeEntry]] = {}

        def _open_dir(path: str, flags: int) -> int:
            return self._alloc(root_entries)

        def _open_at(name: str, flags: int, dir_fd: int | None = None) -> int:
            if self.fail_open_child:
                raise PermissionError(13, "denied")
            return self._alloc(child_entries.get(name, []))

        def _scandir(fd: int) -> _FakeScandir:
            if fd in self.fail_scandir:
                raise PermissionError(13, "scandir failed")  # e.g. EMFILE on the internal dup
            return _FakeScandir(self.tree[fd])

        def _fstat(fd: int) -> os.stat_result:
            if fd in self.fail_fstat:
                raise OSError(5, "fstat failed")
            return _fake_dir_stat(fd)

        proxy = _OsProxy(
            os, scandir=_scandir, close=self.closed.append, fstat=_fstat, open=_open_at
        )
        monkeypatch.setattr(edit_ticket_walk, "os", proxy)
        monkeypatch.setattr(edit_ticket_walk, "_os_open_dir", _open_dir, raising=False)


def _raise_walk_error(exc: BaseException) -> None:
    raise edit_ticket_walk._PopulationWalkError("unreadable_path") from exc


def _drain(gen: object) -> None:
    for _ in gen:  # type: ignore[attr-defined]
        pass


def test_fd_walk_closes_the_root_fd_exactly_once_when_the_listing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the open takes the fd, then scandir's internal dup fails (EMFILE is the real trigger):
    # nothing owned the fd before the listing, so it leaked
    world = _FdWorld()
    world.install(monkeypatch, [])
    world.fail_scandir = {100}
    with pytest.raises(edit_ticket_walk._PopulationWalkError):
        _drain(edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error))
    assert world.closed == [100]


def test_fd_walk_closes_the_fd_exactly_once_when_onerror_swallows_the_listing_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [])
    world.fail_scandir = {100}
    assert list(edit_ticket_walk._fd_walk(str(tmp_path), lambda exc: None)) == []
    assert world.closed == [100]


def test_fd_walk_closes_the_fd_exactly_once_when_fstat_fails_after_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [])
    world.fail_fstat = {100}
    # An OSError from a filesystem call inside the walk is NOT an exception for the caller: the
    # population is incomplete (unreadable_path). Driven through the real entry point.
    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", edit_ticket_walk._fd_walk)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert world.closed == [100]


def test_fd_walk_closes_parent_and_child_exactly_once_when_the_child_listing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [_FakeEntry("sub", True)])
    world.fail_scandir = {101}  # the child fd
    with pytest.raises(edit_ticket_walk._PopulationWalkError):
        _drain(edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error))
    assert sorted(world.closed) == [100, 101]  # each exactly once, no leak, no double close


def test_fd_walk_closes_the_parent_exactly_once_when_the_child_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [_FakeEntry("sub", True)])
    world.fail_open_child = True
    with pytest.raises(edit_ticket_walk._PopulationWalkError):
        _drain(edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error))
    assert world.closed == [100]


def test_fd_walk_closes_every_fd_exactly_once_on_a_clean_full_walk_and_on_early_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [_FakeEntry("sub", True)])
    _drain(edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error))
    assert sorted(world.closed) == [100, 101]
    world.closed.clear()
    world.next_fd = 200
    gen = edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error)
    next(gen)  # root tuple only
    gen.close()
    assert world.closed == [200]


class _CountingHandle(edit_ticket_walk._DirHandle):
    closes = 0

    def close(self) -> None:
        type(self).closes += 1
        super().close()


@pytest.mark.skipif(sys.platform != "win32", reason="held directory handles are Windows-only")
@pytest.mark.parametrize("fail_at", ["root", "child"])
def test_held_walk_closes_every_handle_exactly_once_when_the_listing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: str
) -> None:
    (tmp_path / "sub").mkdir()
    victim = str(tmp_path if fail_at == "root" else tmp_path / "sub")
    real_scandir = os.scandir

    def _scandir(path: object) -> object:
        if os.path.normcase(str(path)) == os.path.normcase(victim):
            raise PermissionError(13, "listing failed")
        return real_scandir(path)  # type: ignore[arg-type]

    real_hold = edit_ticket_walk._hold_dir
    handles: list[_CountingHandle] = []

    def _hold(path: object) -> object:
        h = real_hold(path)  # type: ignore[arg-type]
        counting = _CountingHandle(h.ident, True, h.close)
        handles.append(counting)
        return counting

    _CountingHandle.closes = 0
    monkeypatch.setattr(edit_ticket_walk, "os", _OsProxy(os, scandir=_scandir))
    monkeypatch.setattr(edit_ticket_walk, "_hold_dir", _hold)
    with pytest.raises(edit_ticket_walk._PopulationWalkError):
        _drain(edit_ticket_walk._held_walk(str(tmp_path), _raise_walk_error))
    assert handles, "no handle was ever held"
    assert _CountingHandle.closes == len(handles)  # every acquired handle closed exactly once


@pytest.mark.skipif(sys.platform != "win32", reason="held directory handles are Windows-only")
def test_hold_dir_closes_the_handle_exactly_once_when_a_post_acquire_call_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    k32 = edit_ticket_walk._k32
    real_close = k32.CloseHandle
    closes: list[object] = []

    def _close(handle: object) -> object:
        closes.append(handle)
        return real_close(handle)

    def _boom(*a: object, **k: object) -> object:
        raise RuntimeError("injected failure after the handle was acquired")

    monkeypatch.setattr(k32, "CloseHandle", _close, raising=False)
    monkeypatch.setattr(k32, "GetFileInformationByHandleEx", _boom, raising=False)
    with pytest.raises(RuntimeError):
        edit_ticket_walk._hold_dir(tmp_path)
    assert len(closes) == 1  # acquired, failed, closed exactly once


# ---- round 24: no OSError from any filesystem call inside a walk may escape ----


class _Injector:
    """Counts calls and raises OSError(EIO) on the `fail_at`-th (0-based); -1 never fails."""

    def __init__(self, fail_at: int = -1) -> None:
        self.calls = 0
        self.fail_at = fail_at

    def wrap(self, fn):  # type: ignore[no-untyped-def]
        def inner(*a: object, **k: object) -> object:
            index = self.calls
            self.calls += 1
            if index == self.fail_at:
                raise OSError(5, "EIO injected")
            return fn(*a, **k)

        return inner


_CENSUS_OS_SITES = ["lstat", "stat", "scandir", "fstat", "open", "readlink", "close"]
_CENSUS_K32_SITES = ["CreateFileW", "GetFileInformationByHandle"]


def _census_tree(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    (root / "env").mkdir()
    (root / "env" / "pyvenv.cfg").write_bytes(b"home = x\n")  # marker (digest read)
    (root / "cache").mkdir()
    (root / "cache" / "CACHEDIR.TAG").write_bytes(_BAD_TAG)  # invalid tag -> emitted leaf
    (root / "cache" / "x.py").write_text("1\n", encoding="utf-8")
    (root / "tagged").mkdir()
    (root / "tagged" / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)  # valid tag -> pruned
    (root / "node_modules").mkdir()  # name-pruned
    try:
        (root / "alias.py").symlink_to("app.py")  # link leaf (readlink)
    except (OSError, NotImplementedError):
        pass


class _Accounting:
    """Handles acquired by CreateFileW vs CloseHandle calls (Windows held handles)."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0


def _install_census(
    monkeypatch: pytest.MonkeyPatch, site: str, fail_at: int
) -> tuple[_Injector, _Accounting]:
    injector = _Injector(fail_at)
    accounting = _Accounting()
    if sys.platform == "win32":
        k32 = edit_ticket_walk._k32
        real_create = k32.CreateFileW
        real_close = k32.CloseHandle

        def _create(*a: object) -> object:
            handle = real_create(*a)
            if handle is not None and handle != edit_ticket_walk._INVALID_HANDLE:
                accounting.opened += 1
            return handle

        def _close(handle: object) -> object:
            accounting.closed += 1
            return real_close(handle)

        monkeypatch.setattr(k32, "CreateFileW", _create, raising=False)
        monkeypatch.setattr(k32, "CloseHandle", _close, raising=False)
        if site in _CENSUS_K32_SITES:
            monkeypatch.setattr(k32, site, injector.wrap(getattr(k32, site)), raising=False)
    if site in _CENSUS_OS_SITES:
        real = getattr(os, site)
        monkeypatch.setattr(edit_ticket_walk, "os", _OsProxy(os, **{site: injector.wrap(real)}))
    return injector, accounting


def _census_sites() -> list[str]:
    return _CENSUS_OS_SITES + (_CENSUS_K32_SITES if sys.platform == "win32" else [])


def _call_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str) -> int:
    with monkeypatch.context() as m:
        injector, _acc = _install_census(m, site, -1)
        _walk_tracked_files_bounded(tmp_path)
        return injector.calls


@pytest.mark.parametrize("site", _CENSUS_OS_SITES + _CENSUS_K32_SITES)
def test_no_oserror_from_any_filesystem_call_site_escapes_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    if site in _CENSUS_K32_SITES and sys.platform != "win32":
        pytest.skip("held-handle API is Windows-only")
    _census_tree(tmp_path)
    baseline_files, baseline_pop = _walk_tracked_files_bounded(tmp_path)
    assert baseline_pop["status"] == "complete"
    total = _call_count(tmp_path, monkeypatch, site)
    if total == 0:
        pytest.skip(f"{site} is not exercised by the walk on this platform")
    for index in range(total):
        with monkeypatch.context() as m:
            _inj, acc = _install_census(m, site, index)
            files, population = _walk_tracked_files_bounded(tmp_path)  # must not raise
        if sys.platform == "win32":
            assert acc.opened == acc.closed, (site, index, acc.opened, acc.closed)
        if population["status"] == "incomplete":
            assert population["reason"] in {
                "unreadable_path",
                "per_file_byte_limit",
                "aggregate_byte_limit",
                "cleanup_failed",
            }, (site, index, population["reason"])
        else:
            # absorbed BY DESIGN ("cannot classify a marker -> walk it"): strictly MORE coverage,
            # never less, so it can only add violations at verify
            assert set(baseline_files) <= set(files), (site, index)
            assert set(population["pruned_set"]) <= set(baseline_pop["pruned_set"]), (site, index)


@pytest.mark.parametrize("site", _CENSUS_OS_SITES + _CENSUS_K32_SITES)
def test_public_mint_and_verify_survive_an_oserror_at_every_call_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    if site in _CENSUS_K32_SITES and sys.platform != "win32":
        pytest.skip("held-handle API is Windows-only")
    _census_tree(tmp_path)
    clean = _ticket(tmp_path)
    assert clean.population_status["status"] == "complete"
    total = _call_count(tmp_path, monkeypatch, site)
    if total == 0:
        pytest.skip(f"{site} is not exercised by the walk on this platform")
    for index in sorted({0, total // 2, total - 1}):
        with monkeypatch.context() as m:
            _inj, acc = _install_census(m, site, index)
            minted = _ticket(tmp_path)  # public mint: must not raise
        if sys.platform == "win32":
            assert acc.opened == acc.closed, (site, index)
        if minted.population_status["status"] == "complete":
            assert set(minted.pre_edit_fingerprints) >= set(clean.pre_edit_fingerprints)
        with monkeypatch.context() as m:
            _inj, acc = _install_census(m, site, index)
            result = verify_edit_ticket(  # public verify: must not raise
                repo_root=str(tmp_path), ticket=clean, modified_files=[]
            )
        if sys.platform == "win32":
            assert acc.opened == acc.closed, (site, index)
        assert result["verdict"] in {"PASS", "FAIL"}
        if result["verdict"] == "FAIL":
            assert result["reason"] in {
                "verify_population_incomplete",
                "edit_contract_violated",
            }, (site, index, result["reason"])


# ---- round 25: root resolution inside the boundary; cleanup drains EVERY owned handle ----


def test_realpath_failure_is_inside_the_boundary_for_walk_mint_and_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    clean = _ticket(tmp_path)

    class _Path:
        def __getattr__(self, name: str) -> object:
            return getattr(os.path, name)

        def realpath(self, *a: object, **k: object) -> str:
            raise OSError(5, "EIO injected in realpath")

    monkeypatch.setattr(edit_ticket_service, "os", _OsProxy(os, path=_Path()))
    files, population = _walk_tracked_files_bounded(tmp_path)  # used to RAISE
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert files == {}
    minted = _ticket(tmp_path)
    assert minted.population_status["status"] == "incomplete"
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=clean, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


class _RaisingHandle(edit_ticket_walk._DirHandle):
    """A held handle whose close() records the attempt, then fails (EIO)."""

    attempts: ClassVar[list[str]] = []

    def __init__(self, label: str, ident: tuple[int, int]) -> None:
        super().__init__(ident, True, None)
        self.label = label

    def close(self) -> None:
        type(self).attempts.append(self.label)
        raise OSError(5, f"EIO closing {self.label}")


@pytest.mark.skipif(sys.platform != "win32", reason="held directory handles are Windows-only")
def test_held_walk_attempts_every_owned_handle_even_when_closes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path
    for _ in range(3):
        current = current / "d"
        current.mkdir()
    real_hold = edit_ticket_walk._hold_dir
    _RaisingHandle.attempts = []
    real_handles: list[object] = []

    def _hold(path: object) -> object:
        h = real_hold(path)  # type: ignore[arg-type]
        real_handles.append(h)
        return _RaisingHandle(os.path.basename(str(path)) or "root", h.ident)

    monkeypatch.setattr(edit_ticket_walk, "_hold_dir", _hold)
    gen = edit_ticket_walk._held_walk(str(tmp_path), lambda exc: None)
    for _ in range(4):  # root + 3 levels: four handles owned by the stack
        next(gen)
    with pytest.raises(edit_ticket_walk._PopulationWalkError) as excinfo:
        gen.close()  # every close fails: ALL four must still be attempted
    assert excinfo.value.reason == "cleanup_failed"
    assert len(_RaisingHandle.attempts) == 4, _RaisingHandle.attempts
    for h in real_handles:  # release the real OS handles the fakes never closed
        h.close()  # type: ignore[attr-defined]


@pytest.mark.skipif(sys.platform != "win32", reason="CloseHandle is Windows-only")
def test_closehandle_returning_false_makes_the_population_incomplete_never_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    clean = _ticket(tmp_path)
    assert clean.population_status["status"] == "complete"
    k32 = edit_ticket_walk._k32
    real_close = k32.CloseHandle
    state = {"n": 0}

    def _false_once(handle: object) -> object:
        real_close(handle)  # the OS handle really is released
        state["n"] += 1
        return 0 if state["n"] == 1 else 1  # but the call REPORTS failure once

    monkeypatch.setattr(k32, "CloseHandle", _false_once, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert state["n"] >= 1
    assert population["status"] == "incomplete"
    assert population["reason"] == "cleanup_failed"
    state["n"] = 0
    minted = _ticket(tmp_path)  # public mint
    assert minted.population_status["status"] == "incomplete"
    state["n"] = 0
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=clean, modified_files=[])
    assert result["verdict"] == "FAIL"  # public verify: never PASS
    assert result["reason"] == "verify_population_incomplete"


def test_fd_walk_attempts_every_fd_even_when_closes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [_FakeEntry("sub", True)])
    attempted: list[int] = []

    def _close(fd: int) -> None:
        attempted.append(fd)
        raise OSError(5, "EIO closing fd")

    monkeypatch.setattr(
        edit_ticket_walk,
        "os",
        _OsProxy(
            os,
            scandir=world_scandir(world),
            close=_close,
            fstat=lambda fd: _fake_dir_stat(fd),
            open=lambda name, flags, dir_fd=None: world._alloc([]),
        ),
    )
    gen = edit_ticket_walk._fd_walk(str(tmp_path), _raise_walk_error)
    next(gen)  # root
    next(gen)  # child: two fds owned by the stack
    with pytest.raises(edit_ticket_walk._PopulationWalkError) as excinfo:
        gen.close()
    assert excinfo.value.reason == "cleanup_failed"
    assert sorted(attempted) == [100, 101]  # BOTH attempted although the first failed


def world_scandir(world: _FdWorld):  # type: ignore[no-untyped-def]
    def _scandir(fd: int) -> _FakeScandir:
        return _FakeScandir(world.tree[fd])

    return _scandir


# ---- the census is derived from the modules by AST: a new filesystem call cannot be missed ----

# name -> the injection test(s) that exercise that call site (every one must exist)
_FS_SITE_COVERAGE: dict[str, tuple[str, ...]] = {
    "lstat": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "stat": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "fstat": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "scandir": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "open": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "readlink": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "close": (
        "test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",
        "test_fd_walk_attempts_every_fd_even_when_closes_fail",
    ),
    "realpath": ("test_realpath_failure_is_inside_the_boundary_for_walk_mint_and_verify",),
    "fdopen": ("test_oserror_during_read_is_unreadable_path_not_a_crash",),
    "read": ("test_oserror_during_read_is_unreadable_path_not_a_crash",),
    "readinto": ("test_oserror_during_read_is_unreadable_path_not_a_crash",),
    "CreateFileW": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "GetFileInformationByHandle": (
        "test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",
    ),
    "GetFileInformationByHandleEx": (
        "test_hold_dir_closes_the_handle_exactly_once_when_a_post_acquire_call_raises",
    ),
    "CloseHandle": ("test_closehandle_returning_false_makes_the_population_incomplete_never_pass",),
    "exists": ("test_no_oserror_from_any_filesystem_call_site_escapes_the_walk",),
    "is_dir": (
        "test_fd_walk_entry_type_failure_is_unreadable_path_and_closes_every_fd",
        "test_held_walk_entry_type_failure_and_abspath_failure_are_unreadable_path",
    ),
    "abspath": ("test_held_walk_entry_type_failure_and_abspath_failure_are_unreadable_path",),
    "resolve": ("test_path_resolve_failure_in_the_root_comparison_never_raises_from_verify",),
}
# calls on os / os.path / ctypes handles that touch NO filesystem state
_PURE_CALLS = frozenset({
    "fspath",
    "fsencode",
    "join",
    "normcase",
    "basename",
    "dirname",
    "sizeof",
    "byref",
    "get_last_error",
    "WinDLL",
    "POINTER",
    "c_void_p",
})
_FS_RECEIVERS = frozenset({"os", "path", "_k32", "_k32."})


def _filesystem_call_names(source: str) -> set[str]:
    """Every attribute-call on `os`, `os.path` or the kernel32 binding, plus every method call
    that is a known filesystem accessor, minus the explicitly pure ones."""
    import ast

    found: set[str] = set()
    accessors = {
        "exists",
        "is_file",
        "is_dir",
        "is_symlink",
        "resolve",
        "read_bytes",
        "read_text",
        "iterdir",
        "unlink",
        "rename",
        "rmdir",
        "mkdir",
        "samefile",
        "readinto",
        "read",
    }
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        receiver = node.func.value
        recv_name = (
            receiver.id
            if isinstance(receiver, ast.Name)
            else receiver.attr
            if isinstance(receiver, ast.Attribute)
            else ""
        )
        if recv_name in {"os", "path", "_k32"} and attr not in _PURE_CALLS:
            found.add(attr)
        elif attr in accessors:
            found.add(attr)
    return found


def test_every_filesystem_call_in_the_walk_modules_has_an_injection_test() -> None:
    src_dir = Path(edit_ticket_service.__file__).resolve().parent
    sites: set[str] = set()
    for name in ("edit_ticket_walk.py", "edit_ticket_service.py"):
        sites |= _filesystem_call_names((src_dir / name).read_text(encoding="utf-8"))
    missing = sorted(sites - set(_FS_SITE_COVERAGE))
    assert not missing, (
        f"filesystem call sites with no injection test registered: {missing}. Add an injection "
        "test and register it in _FS_SITE_COVERAGE (or add it to _PURE_CALLS if it touches no "
        "filesystem state)."
    )
    test_dir = Path(__file__).resolve().parent
    sources = "\n".join(
        (test_dir / name).read_text(encoding="utf-8")
        for name in ("test_edit_ticket_walk_cleanup.py", "test_edit_ticket_walk_hardening.py")
    )
    for site, tests in _FS_SITE_COVERAGE.items():
        for test_name in tests:
            assert f"def {test_name}(" in sources, (
                f"{site}: the registered test {test_name} does not exist"
            )


class _BoomEntry:
    name = "x"

    def is_dir(self, *a: object, **k: object) -> bool:
        raise OSError(5, "EIO injected in DirEntry.is_dir")


def test_fd_walk_entry_type_failure_is_unreadable_path_and_closes_every_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _FdWorld()
    world.install(monkeypatch, [])
    world.tree[100] = [_BoomEntry()]  # type: ignore[list-item]
    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", edit_ticket_walk._fd_walk)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert world.closed == [100]


@pytest.mark.skipif(sys.platform != "win32", reason="held directory handles are Windows-only")
def test_held_walk_entry_type_failure_and_abspath_failure_are_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    real_scandir = os.scandir

    class _Scan:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __enter__(self) -> list[object]:
            with self._inner as it:  # type: ignore[attr-defined]
                list(it)
            return [_BoomEntry()]

        def __exit__(self, *exc: object) -> None:
            return None

    with monkeypatch.context() as m:
        m.setattr(edit_ticket_walk, "os", _OsProxy(os, scandir=lambda p: _Scan(real_scandir(p))))
        _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"

    class _Path:
        def __getattr__(self, name: str) -> object:
            return getattr(os.path, name)

        def abspath(self, *a: object, **k: object) -> str:
            raise OSError(5, "EIO injected in abspath")

    with monkeypatch.context() as m:
        m.setattr(edit_ticket_walk, "os", _OsProxy(os, path=_Path()))
        _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_path_resolve_failure_in_the_root_comparison_never_raises_from_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)

    def _boom(self: Path, *a: object, **k: object) -> Path:
        raise OSError(5, "EIO injected in Path.resolve")

    monkeypatch.setattr(Path, "resolve", _boom)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] in {"PASS", "FAIL"}  # lexical comparison fallback; no exception
