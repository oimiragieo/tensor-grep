from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import edit_ticket_service
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

    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen, raising=False)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    # the size check rejects 200 KB before reading, so grow-after-check isn't needed: also probe
    # the open path directly with the same budget
    assert counter.get("opened", 0) >= 0
    ledger = edit_ticket_service._ByteLedger(10, 10)
    with edit_ticket_service._open_regular_no_follow(tmp_path / "big.bin") as handle:
        try:
            list(ledger.iter_chunks(handle))
        except edit_ticket_service._BudgetExceeded:
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

    monkeypatch.setattr(edit_ticket_service, "_readlink", _readlink, raising=False)
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
        edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
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
        edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
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
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=1), raising=False)
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
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=7), raising=False)
    short, _p2 = _walk_tracked_files_bounded(tmp_path)
    assert short == full


def test_short_read_marker_digest_equals_the_full_read_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"home = x\n" * 100)
    _f, full = _walk_tracked_files_bounded(tmp_path)
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=3), raising=False)
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
    monkeypatch.setattr(
        edit_ticket_service, "_fdopen", _fdopen_with(fail_on_read=True), raising=False
    )
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
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=1), raising=False)
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
    monkeypatch.setattr(
        edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=5000), raising=False
    )
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

    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert "f.bin" not in files


def test_ledger_never_manufactures_eof_when_the_allowance_is_exhausted(tmp_path: Path) -> None:
    # an item that starts with no aggregate allowance left is a BUDGET failure, never EOF
    import io

    ledger = edit_ticket_service._ByteLedger(100, 10)
    ledger.remaining = -1  # a previous item overflowed the aggregate
    ledger.begin_item()
    with pytest.raises(edit_ticket_service._BudgetExceeded) as excinfo:
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

    monkeypatch.setattr(edit_ticket_service, "_lstat", _lstat, raising=False)
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

    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen, raising=False)
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
    real = edit_ticket_service._content_prune_marker

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

    monkeypatch.setattr(
        edit_ticket_service, "_content_prune_marker", _rename_pkg_while_it_is_walked
    )
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
    real = edit_ticket_service._default_walk
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

    monkeypatch.setattr(edit_ticket_service, "_walk_impl", _walk, raising=False)
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

    monkeypatch.setattr(edit_ticket_service, "_lstat", _lstat, raising=False)


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
    ledger = edit_ticket_service._ByteLedger(1, 1)
    assert not hasattr(ledger, "leaf_fingerprints")
    assert not hasattr(ledger, "tag_digests")
    assert not hasattr(edit_ticket_service, "_stat_snapshot")


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

    monkeypatch.setattr(edit_ticket_service, "_lstat", _lstat, raising=False)
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
    real_open = edit_ticket_service._os_open

    def _recording_open(path: object, flags: int) -> int:
        opened.append(str(path))
        return real_open(path, flags)  # type: ignore[arg-type]

    monkeypatch.setattr(edit_ticket_service, "_os_open", _recording_open, raising=False)

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

    monkeypatch.setattr(edit_ticket_service, "_walk_impl", _empty_walk, raising=False)
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
