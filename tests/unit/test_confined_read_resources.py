from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from tensor_grep.io import confined


class _ShortReadStream:
    def __init__(self, chunks: list[bytes | None]) -> None:
        self._chunks: Iterator[bytes | None] = iter(chunks)
        self.requests: list[int] = []

    def read(self, size: int) -> bytes | None:
        self.requests.append(size)
        return next(self._chunks)


def _grow_before_open(
    path: Path, payload: bytes, real_open: Callable[..., int]
) -> Callable[..., int]:
    grew = False

    def open_file(target: str | os.PathLike[str], flags: int, *args, **kwargs) -> int:
        nonlocal grew
        if Path(target) == path and not grew:
            path.write_bytes(payload)
            grew = True
        return real_open(target, flags, *args, **kwargs)

    return open_file


def _grow_after_read_starts(
    path: Path, extra: bytes, reader: Callable[..., bytes]
) -> Callable[..., bytes]:
    def read_after_growth(stream: BinaryIO, limit: int) -> bytes:
        with path.open("ab") as output:
            output.write(extra)
        return reader(stream, limit)

    return read_after_growth


def test_ReadConfined_TinyFileAtLargeLimit_KeepsPeakAllocationBounded(tmp_path: Path) -> None:
    path = tmp_path / "tiny.json"
    path.write_bytes(b"hello world!")
    repository = Path(__file__).resolve().parents[2]
    script = """
import sys
import tracemalloc
from pathlib import Path
from tensor_grep.io.confined import read_confined
root, path = map(Path, sys.argv[1:3])
expected_source = Path(sys.argv[3]).resolve()
from tensor_grep.io import confined
assert Path(confined.__file__).resolve() == expected_source
tracemalloc.start()
data = read_confined(root, path, 64 * 1024 * 1024)
print(len(data), tracemalloc.get_traced_memory()[1])
"""
    environment = os.environ.copy()
    source_root = str(repository / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH")) if value
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path),
            str(path),
            str(repository / "src" / "tensor_grep" / "io" / "confined.py"),
        ],
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    byte_count, peak_bytes = map(int, result.stdout.split())

    assert byte_count == 12
    assert peak_bytes < 1048576, f"{byte_count=} {peak_bytes=}"


def test_ReadConfined_StableGrowthBeforeOpenedStat_ReturnsCompleteBytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.touch()
    payload = b"growth before open"
    monkeypatch.setattr(confined.os, "open", _grow_before_open(path, payload, os.open))

    assert confined.read_confined(tmp_path, path, len(payload)) == payload


def test_ReadConfined_AtLimitMultiChunkFile_ReturnsAllBytes(tmp_path: Path) -> None:
    payload = b"x" * (128 * 1024 + 3)
    path = tmp_path / "state.json"
    path.write_bytes(payload)

    assert confined.read_confined(tmp_path, path, len(payload)) == payload


def test_ReadConfined_OverLimitFile_RefusesBeforeReading(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"12345")

    with pytest.raises(OSError, match="exceeds 4 bytes"):
        confined.read_confined(tmp_path, path, 4)


def test_ReadConfined_GrowsAfterOpenedStat_RaisesChangedSourceError(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"initial")
    monkeypatch.setattr(
        confined,
        "_read_limited_stream",
        _grow_after_read_starts(path, b"-later", confined._read_limited_stream),
    )

    with pytest.raises(OSError, match="source changed during read"):
        confined.read_confined(tmp_path, path, 64)


def test_LimitedStreamRead_NonemptyShortReads_ContinuesUntilEof() -> None:
    stream = _ShortReadStream([b"ab", b"cd", b""])

    data = confined._read_limited_stream(cast(BinaryIO, stream), 128 * 1024)

    assert data == b"abcd"
    assert stream.requests == [65536, 65536, 65536]


def test_LimitedStreamRead_NoneResult_RaisesInsteadOfTreatingAsEof() -> None:
    stream = _ShortReadStream([None])

    with pytest.raises(OSError, match="confined source read returned no data"):
        confined._read_limited_stream(cast(BinaryIO, stream), 10)
