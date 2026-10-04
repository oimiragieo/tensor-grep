from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import edit_ticket_service, edit_ticket_walk
from tensor_grep.cli.edit_ticket_service import (
    _walk_tracked_files_bounded,
    build_edit_ready_ticket,
    verify_edit_ticket,
)

# Walker hardening tests split out of test_edit_ticket_population.py (2000-line test budget).

_CACHEDIR_SIG = b"Signature: 8a477f597d28d172789f06886806bc55\n"


def _ticket(tmp_path: Path):
    return build_edit_ready_ticket(
        repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"]
    )


def _symlink_or_skip(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")


# ---- raw reads are bounded; links are read once ----


class _CountingRaw:
    """Raw handle recording the bytes ACTUALLY pulled from the OS."""

    def __init__(self, inner: object, counter: dict[str, int]) -> None:
        self._inner = inner
        self._counter = counter
        self._counter["opened"] = self._counter.get("opened", 0) + 1

    def make(self, buffering: int) -> object:
        import io

        counter = self._counter
        inner = self._inner

        class _Raw(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buf: bytearray) -> int:
                n = inner.readinto(buf) or 0  # type: ignore[attr-defined]
                counter["raw_bytes"] = counter.get("raw_bytes", 0) + n
                return n

            def fileno(self) -> int:
                return inner.fileno()  # type: ignore[attr-defined]

            def close(self) -> None:
                inner.close()  # type: ignore[attr-defined]
                super().close()

        raw = _Raw()
        return raw if buffering == 0 else io.BufferedReader(raw)


def test_budgeted_read_pulls_at_most_limit_plus_one_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A buffered handle pulls a whole buffer from the OS even for a 10-byte budgeted read, so the
    # budget bounds nothing. The handle must be unbuffered: every request reaches the raw read.
    (tmp_path / "big.bin").write_bytes(b"x" * 200_000)
    counter: dict[str, int] = {}
    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)
        return _CountingRaw(inner, counter).make(buffering)

    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen, raising=False)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    # the size check rejects 200 KB before reading, so grow-after-check isn't needed: also probe
    # the open path directly with the same budget
    assert counter.get("opened", 0) >= 0
    ledger = edit_ticket_walk._ByteLedger(10, 10)
    with edit_ticket_walk._open_regular_no_follow(tmp_path / "big.bin") as handle:
        try:
            list(ledger.iter_chunks(handle))
        except edit_ticket_walk._BudgetExceeded:
            pass
    assert counter.get("opened", 0) >= 1, "the safe open did not go through the _fdopen seam"
    assert counter["raw_bytes"] <= 11
    assert population["status"] == "incomplete"


def _counting_readlink(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"n": 0}
    real = os.readlink

    def _readlink(path: object, *a: object, **k: object) -> object:
        calls["n"] += 1
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_walk, "_readlink", _readlink, raising=False)
    return calls


def test_link_target_is_read_exactly_once_and_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Contract: link targets are read in full once; bounded by the platform path limit.
    _symlink_or_skip(tmp_path / "alias", "t" * 10)
    calls = _counting_readlink(monkeypatch)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert calls["n"] == 1
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


def test_oversized_link_target_is_rejected_after_the_single_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _symlink_or_skip(tmp_path / "alias", "t" * 11)
    calls = _counting_readlink(monkeypatch)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert calls["n"] == 1
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"
    assert population["scanned_bytes"] == 11  # the one read is charged


# ---- raw reads may be short (round 11) and may fail mid-read ----


def _fdopen_with(max_per_read: int | None = None, fail_on_read: bool = False):
    """An `_fdopen` seam returning an UNBUFFERED handle whose reads return at most
    `max_per_read` bytes (a legal short read) or raise OSError(EIO) on the first read."""
    import io

    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)

        class _Raw(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buf: bytearray) -> int:
                if fail_on_read:
                    raise OSError(5, "simulated EIO during read")
                view = memoryview(buf)
                if max_per_read is not None:
                    view = view[:max_per_read]
                return inner.readinto(view) or 0  # type: ignore[attr-defined]

            def fileno(self) -> int:
                return inner.fileno()  # type: ignore[attr-defined]

            def close(self) -> None:
                inner.close()  # type: ignore[attr-defined]
                super().close()

        return _Raw()

    return _fdopen


@pytest.mark.parametrize("per_read", [1, 43])
def test_short_read_of_a_malformed_cachedir_tag_does_not_prune_or_hide_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, per_read: int
) -> None:
    # 43 == len(signature): a read that returns exactly the signature bytes of a longer file is
    # NOT end-of-file. `head == sig` used to accept it, prune pkg/ and hide an undeclared edit.
    monkeypatch.setattr(
        edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
    )
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "CACHEDIR.TAG").write_bytes(
        b"Signature: 8a477f597d28d172789f06886806bc55NOT-A-SIGNATURE-LINE"
    )
    core = pkg / "core.py"
    core.write_text("x = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    assert "pkg/core.py" in ticket.pre_edit_fingerprints
    core.write_text("x = 2\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["pkg/core.py"]


@pytest.mark.parametrize("per_read", [1, 43])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_short_reads_of_a_valid_cachedir_tag_still_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, per_read: int, ending: bytes
) -> None:
    monkeypatch.setattr(
        edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
    )
    d = tmp_path / "cache"
    d.mkdir()
    tail = b"# a comment line\n" if ending else b""
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG.rstrip(b"\n") + ending + tail)
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert "cache/x.py" not in files
    assert "cache" in population["pruned_dirs"]


def test_signature_followed_by_a_lone_carriage_return_is_not_a_valid_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CRLF is accepted (a Windows-edited tag), a bare CR or any other tail is not
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=1), raising=False)
    d = tmp_path / "cache"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG.rstrip(b"\n") + b"\r")
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert "cache/x.py" in files


def test_short_read_leaf_fingerprint_equals_the_full_read_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "f.py").write_bytes(b"x = 1\n" * 5000)
    full, _p = _walk_tracked_files_bounded(tmp_path)
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=7), raising=False)
    short, _p2 = _walk_tracked_files_bounded(tmp_path)
    assert short == full


def test_short_read_marker_digest_equals_the_full_read_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"home = x\n" * 100)
    _f, full = _walk_tracked_files_bounded(tmp_path)
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=3), raising=False)
    _f2, short = _walk_tracked_files_bounded(tmp_path)
    assert short["pruned_set"] == full["pruned_set"]


def _tree_for(kind: str, tmp_path: Path) -> None:
    if kind == "tag":
        d = tmp_path / "cache"
        d.mkdir()
        (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    elif kind == "marker":
        d = tmp_path / "env"
        d.mkdir()
        (d / "pyvenv.cfg").write_bytes(b"home = x\n")
    else:
        (tmp_path / "f.py").write_bytes(b"x = 1\n")


@pytest.mark.parametrize("kind", ["tag", "marker", "leaf"])
def test_oserror_during_read_is_unreadable_path_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _tree_for(kind, tmp_path)
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(fail_on_read=True), raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


# ---- the ledger must never manufacture EOF (round 12) ----


def _limited_walker(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    real = edit_ticket_service._walk_tracked_files_bounded

    def limited(root: object, **kw: object) -> object:
        return real(root, **{**kw, "max_file_bytes": limit, "max_aggregate_bytes": limit})  # type: ignore[arg-type]

    monkeypatch.setattr(edit_ticket_service, "_walk_tracked_files_bounded", limited)


_BIG = b"".join(bytes([65 + (i % 26)]) for i in range(100_000))


def test_file_exactly_at_both_limits_is_hashed_in_full(tmp_path: Path) -> None:
    # The first 64 KiB read lowered `remaining`; the cap then subtracted the item total AGAIN,
    # so the next read was "allowed 0 bytes", returned b"" and was taken for EOF: a 64 KiB prefix
    # hash reported as a complete population.
    (tmp_path / "big.bin").write_bytes(_BIG)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=100_000, max_aggregate_bytes=100_000
    )
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 100_000
    assert files["big.bin"] == "file:" + hashlib.sha256(_BIG).hexdigest()


def test_file_exactly_at_both_limits_is_hashed_in_full_with_short_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "big.bin").write_bytes(_BIG)
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=1), raising=False)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=100_000, max_aggregate_bytes=100_000
    )
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 100_000
    assert files["big.bin"] == "file:" + hashlib.sha256(_BIG).hexdigest()


def test_suffix_only_edit_of_a_file_at_the_limit_fails_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # end to end through mint, walk and verify: change only the bytes past the first 64 KiB
    _limited_walker(monkeypatch, 100_000)  # big.bin alone fills the aggregate exactly
    big = tmp_path / "big.bin"
    big.write_bytes(_BIG)
    ticket = _ticket(tmp_path)
    assert ticket.population_status["status"] == "complete"
    big.write_bytes(_BIG[:-1] + b"#")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["big.bin"]


def test_tight_aggregate_never_yields_a_complete_population_with_a_partial_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = {f"f{i}.bin": bytes([66 + i]) * 40_000 for i in range(3)}
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen_with(max_per_read=5000), raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=100_000)
    for name, fp in files.items():
        assert fp == "file:" + hashlib.sha256(contents[name]).hexdigest()  # never partial
    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"


def test_ten_equal_files_filling_the_aggregate_exactly_are_all_hashed_in_full(
    tmp_path: Path,
) -> None:
    contents = {f"f{i}.bin": bytes([70 + i]) * 70_000 for i in range(4)}
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=70_000, max_aggregate_bytes=280_000
    )
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 280_000
    for name, data in contents.items():
        assert files[name] == "file:" + hashlib.sha256(data).hexdigest()


def test_bytes_hashed_must_equal_the_fstat_size_taken_at_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second, independent check: a handle that claims EOF early (whatever the cause) must not
    # produce a "complete" population with a prefix hash.
    import io

    (tmp_path / "f.bin").write_bytes(b"x" * 100)
    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)

        class _EarlyEof(io.RawIOBase):
            served = 0

            def readable(self) -> bool:
                return True

            def readinto(self, buf: bytearray) -> int:
                if self.served >= 50:
                    return 0  # false EOF: only 50 of 100 bytes
                view = memoryview(buf)[: 50 - self.served]
                n = inner.readinto(view) or 0  # type: ignore[attr-defined]
                self.served += n
                return n

            def fileno(self) -> int:
                return inner.fileno()  # type: ignore[attr-defined]

            def close(self) -> None:
                inner.close()  # type: ignore[attr-defined]
                super().close()

        return _EarlyEof()

    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert "f.bin" not in files


def test_ledger_never_manufactures_eof_when_the_allowance_is_exhausted(tmp_path: Path) -> None:
    # an item that starts with no aggregate allowance left is a BUDGET failure, never EOF
    import io

    ledger = edit_ticket_walk._ByteLedger(100, 10)
    ledger.remaining = -1  # a previous item overflowed the aggregate
    ledger.begin_item()
    with pytest.raises(edit_ticket_walk._BudgetExceeded) as excinfo:
        ledger.read_budgeted(io.BytesIO(b"abc"), 3)
    assert excinfo.value.reason == "aggregate_byte_limit"


# ---- round 13: dir->symlink swap during the walk; one session per marker file ----


def _swap_dir_for_symlink_on_classification(
    monkeypatch: pytest.MonkeyPatch, name: str, target: Path
) -> dict[str, bool]:
    """Deterministic race: right after the walker's classification `lstat(<name>)` (before it
    opens/holds the directory), swap the directory for a symlink."""
    real = os.lstat
    swapped = {"done": False}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        result = real(path, *a, **k)
        p = Path(str(path))
        if p.name == name and not swapped["done"] and not os.path.islink(p):
            swapped["done"] = True
            os.rmdir(p)
            try:
                p.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"directory symlink creation not permitted here: {exc}")
        return result

    monkeypatch.setattr(edit_ticket_walk, "_lstat", _lstat, raising=False)
    return swapped


def test_directory_swapped_for_a_symlink_after_classification_never_passes_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    (root / "alias").mkdir()
    ticket = _ticket(root)
    assert ticket.population_status["status"] == "complete"
    swapped = _swap_dir_for_symlink_on_classification(monkeypatch, "alias", tmp_path / "elsewhere")
    result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
    assert swapped["done"]
    assert result["verdict"] == "FAIL"  # os.walk silently skipped the link: it must never vanish


def test_directory_swapped_for_a_symlink_makes_the_walk_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    (root / "alias").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    swapped = _swap_dir_for_symlink_on_classification(monkeypatch, "alias", tmp_path / "elsewhere")
    _files, population = _walk_tracked_files_bounded(root)
    assert swapped["done"]
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_unswapped_empty_directory_still_verifies_pass(tmp_path: Path) -> None:  # control
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "alias").mkdir()
    ticket = _ticket(tmp_path)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


def _raw_read_counter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count the bytes ACTUALLY pulled from the OS across every handle (cumulative)."""
    counter: dict[str, int] = {}
    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)
        counter["opens"] = counter.get("opens", 0) + 1
        return _CountingRaw(inner, counter).make(buffering)

    monkeypatch.setattr(edit_ticket_walk, "_fdopen", _fdopen, raising=False)
    return counter


def test_valid_cachedir_tag_is_read_in_one_session_under_the_per_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _raw_read_counter(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)  # 44 bytes
    limit = len(_CACHEDIR_SIG)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=limit, max_aggregate_bytes=10 * limit
    )
    assert population["status"] == "complete"
    assert counter["raw_bytes"] <= limit + 1  # was 88: classification + digest each got a session
    assert counter["opens"] == 1
    assert population["scanned_bytes"] == limit


def test_invalid_cachedir_tag_is_read_in_one_session_and_reused_as_a_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _raw_read_counter(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    bad = b"Signature: 8a477f597d28d172789f06886806bc55 XX"  # not a signature line
    (d / "CACHEDIR.TAG").write_bytes(bad)
    limit = len(bad)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=limit, max_aggregate_bytes=10 * limit
    )
    assert population["status"] == "complete"
    assert counter["raw_bytes"] <= limit + 1
    assert counter["opens"] == 1
    assert files["cache/CACHEDIR.TAG"] == "file:" + hashlib.sha256(bad).hexdigest()
    assert population["scanned_bytes"] == limit


def test_normal_marker_is_read_once_under_the_per_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # control
    counter = _raw_read_counter(monkeypatch)
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"home = /usr/bin\n")
    limit = 16
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=limit, max_aggregate_bytes=10 * limit
    )
    assert population["status"] == "complete"
    assert counter["raw_bytes"] <= limit + 1
    assert counter["opens"] == 1


# ---- round 14: enumeration bound to an open directory; stale tag-cache reuse ----

_NEEDS_HELD_CHAIN = pytest.mark.skipif(
    sys.platform != "win32", reason="held no-share-delete directory handles are Windows-only"
)


@_NEEDS_HELD_CHAIN
def test_a_directory_in_the_walk_chain_cannot_be_renamed_while_it_is_being_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows has no dir_fd: every directory in the root-to-leaf chain is HELD open without
    # FILE_SHARE_DELETE, so it cannot be renamed, deleted or replaced mid-walk.
    root = tmp_path / "repo"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "kept.py").write_text("x = 1\n", encoding="utf-8")
    attempts: dict[str, object] = {"n": 0, "error": None}
    real = edit_ticket_walk._content_prune_marker

    def _rename_pkg_while_it_is_walked(path: Path, *a: object, **k: object) -> object:
        if path.name == "sub" and attempts["n"] == 0:  # classifying pkg's children: pkg is held
            attempts["n"] = 1
            try:
                os.rename(root / "pkg", root / "pkg.moved")
            except OSError as exc:  # PermissionError: sharing violation
                attempts["error"] = exc
            else:
                os.rename(root / "pkg.moved", root / "pkg")  # undo; the assertion below fails
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_walk, "_content_prune_marker", _rename_pkg_while_it_is_walked)
    files, population = _walk_tracked_files_bounded(root)
    assert attempts["n"] == 1
    assert isinstance(attempts["error"], PermissionError), "the walked directory was renameable"
    assert population["status"] == "complete"  # the walk is unaffected
    assert files == {"pkg/kept.py": files["pkg/kept.py"]}


def _swap_src_before_descent(
    monkeypatch: pytest.MonkeyPatch, repo: Path, how: str, other: Path
) -> dict[str, object]:
    """`_walk_impl` seam: when the walker is about to descend into `src/` (after our own
    classification), move `src` aside and put an impostor in its place: a symlink to `other`
    (how == "symlink") or a REAL directory with the same name (how == "dir")."""
    real = edit_ticket_walk._default_walk
    state: dict[str, object] = {"swapped": False, "blocked": None}

    def _walk(root: object, onerror: object):  # type: ignore[no-untyped-def]
        for dirpath, dirnames, filenames, handle in real(root, onerror):
            yield dirpath, dirnames, filenames, handle
            if Path(dirpath) == repo and "src" in dirnames and not state["swapped"]:
                state["swapped"] = True
                try:
                    os.rename(repo / "src", repo / "src.moved")
                    if how == "symlink":
                        (repo / "src").symlink_to(other, target_is_directory=True)
                    else:
                        os.rename(other, repo / "src")
                except OSError as exc:
                    state["blocked"] = exc  # e.g. a held directory refuses the rename

    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", _walk, raising=False)
    return state


@pytest.mark.parametrize("how", ["symlink", "dir"])
def test_directory_swapped_before_descent_never_passes_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "allowed.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "impostor").mkdir()
    ticket = _ticket(repo)
    (repo / "src" / "evil.py").write_text("boom\n", encoding="utf-8")
    state = _swap_src_before_descent(monkeypatch, repo, how, tmp_path / "impostor")
    try:
        result = verify_edit_ticket(repo_root=str(repo), ticket=ticket, modified_files=[])
    finally:
        if (repo / "src.moved").exists():  # undo so tmp cleanup is clean
            if os.path.lexists(repo / "src"):
                (repo / "src").unlink() if os.path.islink(repo / "src") else os.rename(
                    repo / "src", tmp_path / "impostor"
                )
            os.rename(repo / "src.moved", repo / "src")
    assert state["swapped"], "the walker never reached the swap point (seam not driven)"
    assert result["verdict"] == "FAIL"  # incomplete (impostor) or evil.py seen; never PASS


def _tag_tree(tmp_path: Path) -> tuple[Path, bytes]:
    d = tmp_path / "cache"
    d.mkdir()
    bad = b"Signature: 8a477f597d28d172789f06886806bc55 XX"  # not a signature line -> leaf
    tag = d / "CACHEDIR.TAG"
    tag.write_bytes(bad)
    return tag, bad


def _rewrite_tag_in_place_keeping_mtime(tag: Path, new: bytes) -> None:
    """Codex's repro: same size, SAME inode, original mtime restored (so identity, size and
    mtime all still match whatever was observed before)."""
    st = os.stat(tag)
    with open(tag, "r+b") as handle:
        handle.write(new)
    os.utime(tag, ns=(st.st_atime_ns, st.st_mtime_ns))


def _after_sibling_lstat(monkeypatch: pytest.MonkeyPatch, sibling: Path, action) -> None:  # type: ignore[no-untyped-def]
    """Run `action()` at the first `_lstat` of `sibling` (a leaf that sorts BEFORE the tag, so the
    walker reaches it after the tag's classification session and before its leaf stage)."""
    real = os.lstat
    done = {"x": False}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        if Path(str(path)) == sibling and not done["x"]:
            done["x"] = True
            action()
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_walk, "_lstat", _lstat, raising=False)


def test_invalid_tag_fingerprint_is_the_bytes_read_at_classification_with_no_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An invalid tag is hashed ONCE, in its classification session, and its fingerprint is
    # EMITTED into the population right there. There is no cache and no identity/size/mtime
    # comparison left to fool: an in-place same-size rewrite with the mtime restored, applied
    # after classification, changes nothing about what this walk reports (every file is hashed
    # exactly once, at one point in the walk).
    counter = _raw_read_counter(monkeypatch)
    tag, bad = _tag_tree(tmp_path)
    sibling = tag.parent / "A.txt"  # sorts before CACHEDIR.TAG
    sibling.write_bytes(b"s")
    _after_sibling_lstat(
        monkeypatch,
        sibling,
        lambda: _rewrite_tag_in_place_keeping_mtime(
            tag, b"Signature: 8a477f597d28d172789f06886806bc55 YY"
        ),
    )
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert files["cache/CACHEDIR.TAG"] == "file:" + hashlib.sha256(bad).hexdigest()
    assert counter["raw_bytes"] <= len(bad) + 1 + 1  # tag (one session) + the 1-byte sibling
    assert counter["opens"] == 2  # the tag exactly once, the sibling once
    # no cache path exists any more
    ledger = edit_ticket_walk._ByteLedger(1, 1)
    assert not hasattr(ledger, "leaf_fingerprints")
    assert not hasattr(ledger, "tag_digests")
    assert not hasattr(edit_ticket_walk, "_stat_snapshot")


def test_in_place_same_size_tag_rewrite_before_the_walk_reaches_it_fails_verify(
    tmp_path: Path,
) -> None:
    tag, _bad = _tag_tree(tmp_path)
    ticket = _ticket(tmp_path)
    assert ticket.population_status["status"] == "complete"
    _rewrite_tag_in_place_keeping_mtime(tag, b"Signature: 8a477f597d28d172789f06886806bc55 YY")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["cache/CACHEDIR.TAG"]


def test_untouched_invalid_tag_leaf_verifies_pass(tmp_path: Path) -> None:  # control
    _tag, bad = _tag_tree(tmp_path)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert files["cache/CACHEDIR.TAG"] == "file:" + hashlib.sha256(bad).hexdigest()
    ticket = _ticket(tmp_path)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


# ---- round 15: authenticate a child BEFORE inspecting its markers; no marker reads in name-pruned dirs


def _swap_after_lstat(
    monkeypatch: pytest.MonkeyPatch, name: str, swap: object
) -> dict[str, object]:
    """Run `swap()` right AFTER the walker's classification `_lstat(<name>)` (before it opens,
    holds or inspects the directory), then return the stat taken before the swap."""
    real = os.lstat
    state: dict[str, object] = {"done": False, "blocked": None}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        result = real(path, *a, **k)
        if Path(str(path)).name == name and not state["done"] and not os.path.islink(path):
            state["done"] = True
            try:
                swap()  # type: ignore[operator]
            except OSError as exc:
                state["blocked"] = exc
        return result

    monkeypatch.setattr(edit_ticket_walk, "_lstat", _lstat, raising=False)
    return state


def _outside_with_marker(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pyvenv.cfg").write_bytes(b"home = outside\n")
    return outside


def _link_dir(link: Path, target: Path, kind: str) -> None:
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions are Windows-only")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            timeout=30,
            capture_output=True,
        )
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"directory symlink creation not permitted here: {exc}")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_outside_marker_is_never_read_through_a_directory_swapped_after_lstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "m.py").write_text("x = 1\n", encoding="utf-8")
    outside = _outside_with_marker(tmp_path)
    opened: list[str] = []
    real_open = edit_ticket_walk._os_open

    def _recording_open(path: object, flags: int) -> int:
        opened.append(str(path))
        return real_open(path, flags)  # type: ignore[arg-type]

    monkeypatch.setattr(edit_ticket_walk, "_os_open", _recording_open, raising=False)

    def _swap() -> None:
        os.rename(repo / "src", repo / "src.moved")
        _link_dir(repo / "src", outside, kind)

    state = _swap_after_lstat(monkeypatch, "src", _swap)
    try:
        _files, population = _walk_tracked_files_bounded(repo)
    finally:
        if os.path.lexists(repo / "src") and (
            os.path.islink(repo / "src") or (repo / "src.moved").exists()
        ):
            if os.path.islink(repo / "src"):
                os.unlink(repo / "src")
            else:
                os.rmdir(repo / "src")  # junction
            if (repo / "src.moved").exists():
                os.rename(repo / "src.moved", repo / "src")
    assert state["done"]
    if state["blocked"] is None:  # the swap happened (always on POSIX)
        assert population["status"] == "incomplete"
        assert "src" not in population["pruned_set"]  # never recorded as content-pruned
        assert not any(p.endswith("pyvenv.cfg") and "src" in p for p in opened)
    else:  # a held directory refused the rename: nothing was swapped, nothing can be read
        assert population["status"] == "complete"


def test_unswapped_marker_directory_still_content_prunes(tmp_path: Path) -> None:  # control
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"home = x\n")
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert population["pruned_set"]["env"].startswith("pyvenv.cfg:")


@pytest.mark.parametrize("name", ["node_modules", ".pytest_cache"])
def test_oversized_marker_inside_a_name_pruned_dir_is_never_inspected(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    d = tmp_path / name
    d.mkdir()
    (d / "pyvenv.cfg").write_bytes(b"x" * 11)  # over the budget of 10
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path="app.py",
        query="a",
        allowed_files=["app.py"],
        max_file_bytes=10,
        max_aggregate_bytes=10,
    )
    pop = ticket.population_status
    assert pop["status"] == "complete", pop
    assert pop["pruned_set"][name] == "name"  # not turned into a content-prune by a marker
    # only app.py is charged: no open, hold or charge for the marker
    assert pop["scanned_bytes"] == len((tmp_path / "app.py").read_bytes())


def test_a_changed_marker_inside_a_name_pruned_dir_does_not_violate_the_ticket(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "pyvenv.cfg").write_text("home = a\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    (nm / "pyvenv.cfg").write_text("home = b\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


# ---- round 17: a symlinked / junctioned repo root ----


def _unlink_dir_link(link: Path) -> None:
    try:
        os.unlink(link)
    except OSError:
        os.rmdir(link)  # Windows directory symlink / junction


def _root_alias(tmp_path: Path, kind: str, target: Path) -> Path:
    alias = tmp_path / "repo"
    _link_dir(alias, target, kind)
    return alias


def _real_tree(tmp_path: Path, name: str = "real") -> Path:
    real = tmp_path / name
    real.mkdir()
    (real / "app.py").write_text("a = 1\n", encoding="utf-8")
    (real / "secret.py").write_text("s = 1\n", encoding="utf-8")
    return real


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_symlinked_root_edit_to_an_undeclared_file_fails_verify(tmp_path: Path, kind: str) -> None:
    # os.fwalk(follow_symlinks=False) yields NOTHING for a symlink root: the old walk was an
    # empty "complete" population and any undeclared edit verified PASS.
    real = _real_tree(tmp_path)
    alias = _root_alias(tmp_path, kind, real)
    try:
        ticket = _ticket(alias)
        assert ticket.population_status["status"] == "complete"
        assert "secret.py" in ticket.pre_edit_fingerprints
        (real / "secret.py").write_text("s = 2\n", encoding="utf-8")
        result = verify_edit_ticket(repo_root=str(alias), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert result["violations"] == ["secret.py"]
    finally:
        _unlink_dir_link(alias)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_unchanged_tree_verifies_pass_through_a_root_alias(tmp_path: Path, kind: str) -> None:
    real = _real_tree(tmp_path)
    alias = _root_alias(tmp_path, kind, real)
    try:
        ticket = _ticket(alias)
        assert ticket.population_status["status"] == "complete"
        result = verify_edit_ticket(repo_root=str(alias), ticket=ticket, modified_files=[])
        assert result["verdict"] == "PASS"
    finally:
        _unlink_dir_link(alias)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_retargeted_root_alias_between_mint_and_verify_never_passes(
    tmp_path: Path, kind: str
) -> None:
    real1 = _real_tree(tmp_path, "real1")
    real2 = _real_tree(tmp_path, "real2")  # identical content, different directory
    alias = _root_alias(tmp_path, kind, real1)
    try:
        ticket = _ticket(alias)
        _unlink_dir_link(alias)
        _link_dir(alias, real2, kind)
        result = verify_edit_ticket(repo_root=str(alias), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert result["violations"] == ["root_identity_changed"]
    finally:
        _unlink_dir_link(alias)


def test_a_walk_that_never_visits_the_root_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")

    def _empty_walk(top: object, onerror: object):  # type: ignore[no-untyped-def]
        return iter(())

    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", _empty_walk, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert files == {}


def test_ticket_without_a_recorded_root_identity_is_refused(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    assert ticket.population_status["root_identity"]
    legacy_dict = ticket.to_dict()
    del legacy_dict["population_status"]["root_identity"]
    legacy = edit_ticket_service.EditReadyTicketV1.from_dict(legacy_dict)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=legacy, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "ticket_format_outdated"


# ---- round 18: the root identity comes from the directory that SUPPLIED the listing ----


def test_root_handle_with_a_different_inode_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex's seam probe: the first tuple's handle is not the directory the root pathname lstat
    # authenticated. It used to be skipped entirely and the population was "complete".
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    real = edit_ticket_walk._default_walk
    DirHandle = edit_ticket_walk._DirHandle

    def _walk(top: object, onerror: object):  # type: ignore[no-untyped-def]
        for dirpath, dirnames, filenames, handle in real(top, onerror):
            if handle is not None and handle.ident is not None:
                handle = DirHandle((handle.ident[0], handle.ident[1] + 1), True)
            yield dirpath, dirnames, filenames, handle
            return  # the root tuple is all this probe needs

    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", _walk, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert "app.py" not in files


def _swap_root_before_the_walk_opens_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, object]:
    """Deterministic race through the `_walk_impl` seam: AFTER the walker's root `lstat` and
    BEFORE the real walker opens the root, rename the root aside and put a real directory with
    only the unchanged `app.py` in its place; restore the original after the walk."""
    real = edit_ticket_walk._default_walk
    state: dict[str, object] = {"swapped": False}

    def _walk(top: object, onerror: object):  # type: ignore[no-untyped-def]
        root = Path(str(top))
        saved = root.with_name(root.name + ".saved")
        os.rename(root, saved)
        root.mkdir()
        (root / "app.py").write_bytes((saved / "app.py").read_bytes())
        state["swapped"] = True
        try:
            yield from real(top, onerror)
        finally:
            shutil.rmtree(root)
            os.rename(saved, root)

    monkeypatch.setattr(edit_ticket_walk, "_walk_impl", _walk, raising=False)
    return state


def test_root_swapped_for_a_lookalike_directory_before_the_walk_opens_it_never_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(repo)
    (repo / "evil.py").write_text("boom\n", encoding="utf-8")  # undeclared, in the ORIGINAL
    state = _swap_root_before_the_walk_opens_it(monkeypatch, tmp_path)
    result = verify_edit_ticket(repo_root=str(repo), ticket=ticket, modified_files=[])
    assert state["swapped"], "the swap point was never reached (seam not driven)"
    assert result["verdict"] == "FAIL"  # incomplete (root identity mismatch); never PASS


def test_root_identity_is_recorded_from_the_walked_handle(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    st = os.lstat(os.path.realpath(tmp_path))
    ident = ticket.population_status["root_identity"]
    assert ident[1] == st.st_ino  # same directory, now sourced from the handle
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


# ---- round 19: a directory swapped for a FILE (and back) between the listing and the lstat ----


def _swap_before_lstat(
    monkeypatch: pytest.MonkeyPatch, target: Path, swap: object
) -> dict[str, bool]:
    """Run `swap()` right BEFORE the walker's first `_lstat(<target>)`, i.e. after the entry was
    listed (in dirnames / filenames) and before its classification."""
    real = os.lstat
    state = {"done": False}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        if Path(str(path)) == target and not state["done"]:
            state["done"] = True
            swap()  # type: ignore[operator]
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_walk, "_lstat", _lstat, raising=False)
    return state


@pytest.mark.parametrize("name", ["node_modules", "pkg"])
def test_directory_swapped_for_a_regular_file_before_classification_never_passes_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # A regular file where a directory was listed used to be recorded as pruned_set[name]="name"
    # (name pruning ran before any S_ISDIR check) and never fingerprinted: PASS.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / name).mkdir()
    ticket = _ticket(tmp_path)
    target = tmp_path / name

    def _dir_to_file() -> None:
        os.rmdir(target)
        target.write_bytes(b"boom\n")

    state = _swap_before_lstat(monkeypatch, target, _dir_to_file)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert state["done"]
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


def test_file_swapped_for_a_directory_before_its_leaf_lstat_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reverse: enumerated as a FILE, a directory by the time it is classified. It must not be
    # skipped (its subtree would vanish): the leaf stage refuses a non-link directory.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "thing").write_bytes(b"x")
    ticket = _ticket(tmp_path)
    target = tmp_path / "thing"

    def _file_to_dir() -> None:
        os.unlink(target)
        target.mkdir()
        (target / "evil.py").write_bytes(b"boom\n")

    state = _swap_before_lstat(monkeypatch, target, _file_to_dir)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert state["done"]
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


def test_unchanged_node_modules_still_name_prunes_and_verifies_pass(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    ticket = _ticket(tmp_path)
    assert ticket.population_status["pruned_set"]["node_modules"] == "name"
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


# ---- round 20: the emitted invalid-tag fingerprint still goes through the leaf checks ----


def _swap_tag_at_leaf_stage(
    monkeypatch: pytest.MonkeyPatch, tag: Path, swap: object
) -> dict[str, int]:
    """Run `swap()` at the tag's LEAF-stage `_lstat` (its 3rd: calls 1-2 are the classification
    session), i.e. after the walker listed it in `filenames` and hashed it, before it is
    accepted as a leaf. Records how many times the tag was lstat'ed."""
    real = os.lstat
    state = {"calls": 0}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        if Path(str(path)) == tag:
            state["calls"] += 1
            if state["calls"] == 3:
                swap()  # type: ignore[operator]
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_walk, "_lstat", _lstat, raising=False)
    return state


def _tag_dir_to_directory(tag: Path) -> None:
    os.unlink(tag)
    tag.mkdir()
    (tag / "evil.py").write_bytes(b"boom\n")


def _tag_to_new_inode(tag: Path) -> None:
    other = tag.parent / "CACHEDIR.TAG.new"
    other.write_bytes(b"Signature: 8a477f597d28d172789f06886806bc55 YY")
    os.replace(other, tag)


def _tag_to_symlink(tag: Path) -> None:
    target = tag.parent / "elsewhere.txt"
    target.write_bytes(b"x")
    os.unlink(tag)
    try:
        tag.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")


@pytest.mark.parametrize(
    "swap",
    [_tag_dir_to_directory, _tag_to_new_inode, _tag_to_symlink],
    ids=["dir", "inode", "link"],
)
def test_emitted_tag_fingerprint_is_not_accepted_for_a_swapped_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: object
) -> None:
    tag, bad = _tag_tree(tmp_path)
    state = _swap_tag_at_leaf_stage(monkeypatch, tag, lambda: swap(tag))  # type: ignore[operator]
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert state["calls"] >= 3, "the tag never reached its leaf-stage lstat (shortcut skipped it)"
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    # the stale emitted hash of the OLD object is never trusted (a swapped-in object is
    # fingerprinted as an ordinary leaf, if at all, and the population is incomplete anyway)
    assert files.get("cache/CACHEDIR.TAG") != "file:" + hashlib.sha256(bad).hexdigest()


def test_tag_replaced_by_a_directory_with_evil_py_never_passes_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag, _bad = _tag_tree(tmp_path)
    ticket = _ticket(tmp_path)
    _swap_tag_at_leaf_stage(monkeypatch, tag, lambda: _tag_dir_to_directory(tag))
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


def test_unchanged_invalid_tag_uses_the_emitted_fingerprint_with_one_read_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # control
    counter = _raw_read_counter(monkeypatch)
    tag, bad = _tag_tree(tmp_path)
    state = _swap_tag_at_leaf_stage(monkeypatch, tag, lambda: None)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert state["calls"] >= 3  # the leaf-stage lstat/type/identity check DID run
    assert files["cache/CACHEDIR.TAG"] == "file:" + hashlib.sha256(bad).hexdigest()
    assert counter["opens"] == 1  # exactly one read session
    assert counter["raw_bytes"] <= len(bad) + 1


# ---- round 21: case-insensitive filesystems (the on-disk name differs from the marker name) ----


def _fs_is_case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "CaseProbe.X"
    probe.write_bytes(b"x")
    try:
        return (tmp_path / "caseprobe.x").exists()
    finally:
        probe.unlink()


_BAD_TAG = b"Signature: 8a477f597d28d172789f06886806bc55 XX"  # a tag-named file, not a tag


@pytest.mark.parametrize("on_disk", ["cachedir.tag", "CacheDir.Tag"])
def test_invalid_tag_with_a_different_case_name_is_read_once_and_charged_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_disk: str
) -> None:
    if not _fs_is_case_insensitive(tmp_path):
        pytest.skip("case-insensitive filesystem only (NTFS / default APFS)")
    counter = _raw_read_counter(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    (d / on_disk).write_bytes(_BAD_TAG)
    size = len(_BAD_TAG)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=size, max_aggregate_bytes=size
    )
    assert population["status"] == "complete", population
    assert counter["opens"] == 1  # one read session, not classification + a second leaf read
    assert population["scanned_bytes"] == size  # charged exactly once
    # pinned: the fingerprint is recorded under the ENUMERATED (real on-disk) name
    assert f"cache/{on_disk}" in files
    assert files[f"cache/{on_disk}"] == "file:" + hashlib.sha256(_BAD_TAG).hexdigest()


@pytest.mark.parametrize("on_disk", ["cachedir.tag", "CacheDir.Tag"])
def test_unchanged_tree_with_a_different_case_tag_verifies_pass_at_tight_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_disk: str
) -> None:
    if not _fs_is_case_insensitive(tmp_path):
        pytest.skip("case-insensitive filesystem only (NTFS / default APFS)")
    d = tmp_path / "cache"
    d.mkdir()
    (d / on_disk).write_bytes(_BAD_TAG)
    _limited_walker(monkeypatch, len(_BAD_TAG))  # mint AND verify walk with both limits = size
    ticket = _ticket(tmp_path)
    assert ticket.population_status["status"] == "complete"
    assert f"cache/{on_disk}" in ticket.pre_edit_fingerprints
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


def test_marker_with_a_different_case_name_is_read_once_and_charged_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sweep of the same class: pyvenv.cfg is looked up by the hard-coded name in classification
    # and its digest read through that same name (a pruned directory's markers are never
    # enumerated as leaves), so case cannot make it a second read; pinned here.
    if not _fs_is_case_insensitive(tmp_path):
        pytest.skip("case-insensitive filesystem only (NTFS / default APFS)")
    counter = _raw_read_counter(monkeypatch)
    env = tmp_path / "env"
    env.mkdir()
    body = b"home = /usr/bin\n"
    (env / "PyVenv.CFG").write_bytes(body)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=len(body), max_aggregate_bytes=len(body)
    )
    assert population["status"] == "complete", population
    assert population["pruned_set"]["env"].startswith("pyvenv.cfg:")
    assert counter["opens"] == 1
    assert population["scanned_bytes"] == len(body)


def test_lowercase_cachedir_tag_on_a_case_sensitive_filesystem_is_an_ordinary_leaf(
    tmp_path: Path,
) -> None:
    if _fs_is_case_insensitive(tmp_path):
        pytest.skip("case-sensitive filesystem only (Linux)")
    d = tmp_path / "cache"
    d.mkdir()
    valid = b"Signature: 8a477f597d28d172789f06886806bc55\n"
    (d / "cachedir.tag").write_bytes(valid)  # right content, wrong NAME (spec: exact name)
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert "cache" not in population["pruned_set"]  # not treated as a CACHEDIR tag
    assert files["cache/cachedir.tag"] == "file:" + hashlib.sha256(valid).hexdigest()
    assert "cache/x.py" in files


# ---- round 22: deep ordinary trees (the walk must be iterative, not recursive) ----

_DEEP = 1100  # past Python's default recursion limit (1000) with room to spare


def _make_deep_tree(root: Path, depth: int) -> Path:
    current = str(root)
    try:
        for _ in range(depth):
            current = os.path.join(current, "d")
            os.mkdir(current)
    except OSError as exc:
        pytest.skip(f"cannot create a {depth}-level tree here (long paths / limits): {exc}")
    return Path(current)


def _remove_deep_tree(root: Path) -> None:
    # shutil.rmtree / os.walk are recursive on some supported Pythons; use the OS
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "rmdir", "/s", "/q", "\\\\?\\" + str(root)],
            check=False,
            timeout=120,
            capture_output=True,
        )
    else:
        subprocess.run(["rm", "-rf", str(root)], check=False, timeout=120)


def _skip_if_fd_limit_too_low() -> None:
    if sys.platform != "win32":
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft != resource.RLIM_INFINITY and soft < _DEEP + 200:
            pytest.skip(f"RLIMIT_NOFILE {soft} too low to hold a {_DEEP}-deep fd chain")


def test_deep_ordinary_tree_mints_and_verifies_pass_unchanged(tmp_path: Path) -> None:
    _skip_if_fd_limit_too_low()
    root = tmp_path / "deep"
    root.mkdir()
    bottom = _make_deep_tree(root, _DEEP)
    try:
        (bottom / "f.py").write_text("x = 1\n", encoding="utf-8")
        (root / "app.py").write_text("a = 1\n", encoding="utf-8")
        ticket = _ticket(root)  # used to raise RecursionError after ~997 directory tuples
        assert ticket.population_status["status"] == "complete", ticket.population_status
        assert len(ticket.pre_edit_fingerprints) == 2
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "PASS"
    finally:
        _remove_deep_tree(root)


def test_undeclared_edit_at_the_bottom_of_a_deep_tree_fails_verify(tmp_path: Path) -> None:
    _skip_if_fd_limit_too_low()
    root = tmp_path / "deep"
    root.mkdir()
    bottom = _make_deep_tree(root, _DEEP)
    try:
        (bottom / "f.py").write_text("x = 1\n", encoding="utf-8")
        (root / "app.py").write_text("a = 1\n", encoding="utf-8")
        ticket = _ticket(root)
        (bottom / "f.py").write_text("x = 2\n", encoding="utf-8")
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert len(result["violations"]) == 1
        assert result["violations"][0].endswith("d/f.py")
    finally:
        _remove_deep_tree(root)


def _process_handle_count() -> int | None:
    if sys.platform != "win32":
        return len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    import ctypes
    from ctypes import wintypes

    count = wintypes.DWORD(0)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        return None
    return int(count.value)


def test_early_termination_mid_walk_leaves_every_handle_closed(tmp_path: Path) -> None:
    # a 60-level tree with a file at every level; max_files=3 stops the walk deep inside it
    root = tmp_path / "tree"
    root.mkdir()
    current = root
    for i in range(60):
        (current / f"f{i}.py").write_text("x\n", encoding="utf-8")
        current = current / "d"
        current.mkdir()

    def _once() -> dict[str, object]:
        _files, population = _walk_tracked_files_bounded(root, max_files=3)
        return population

    population = _once()  # warm-up
    assert population["status"] == "incomplete"
    assert population["reason"] == "file_count_limit"
    before = _process_handle_count()
    if before is None:
        pytest.skip("no handle/fd counter on this platform")
    for _ in range(20):
        _once()
    after = _process_handle_count()
    assert after is not None
    assert after - before <= 3, f"handles grew {before} -> {after} across 20 early-terminated walks"


def test_closing_the_walk_generator_mid_iteration_closes_every_held_handle(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    current = root
    for _ in range(40):
        current = current / "d"
        current.mkdir()
    before = _process_handle_count()
    if before is None:
        pytest.skip("no handle/fd counter on this platform")
    gen = edit_ticket_walk._default_walk(root, lambda exc: (_ for _ in ()).throw(exc))
    for _ in range(25):  # 25 directories deep: 25 handles / fds held
        next(gen)
    held = _process_handle_count()
    assert held is not None and held - before >= 20  # the chain really is held open
    gen.close()
    after = _process_handle_count()
    assert after is not None
    assert after - before <= 3, f"handles {before} -> {held} -> {after} after close()"


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
