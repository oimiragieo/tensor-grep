from __future__ import annotations

import json
import math
import os
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tensor_grep.cli import state_json

_EXPECTED_STATE_JSON_MAXIMUM = sys.maxsize - 1
_EXPECTED_STATE_JSON_FIRST_OUT_OF_RANGE = sys.maxsize
_EXPECTED_STATE_JSON_RANGE_ERROR = (
    f"state JSON byte limit must be between 1 and {_EXPECTED_STATE_JSON_MAXIMUM}"
)


@pytest.fixture
def _python_json_scanner_at_recursion_limit_1000(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    original_recursion_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1000)
        decoder = json.JSONDecoder()
        decoder.scan_once = json.scanner.py_make_scanner(decoder)
        monkeypatch.setattr(json, "_default_decoder", decoder)
        yield
    finally:
        sys.setrecursionlimit(original_recursion_limit)


@pytest.fixture
def _bounded_integer_conversion() -> Iterator[None]:
    original = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(original)


def _lstat_with_postread_disappearance(
    path: Path,
    postread_started: threading.Event,
    allow_unlink: threading.Event,
    real_lstat: Callable[[Path], os.stat_result],
) -> Callable[[Path], os.stat_result]:
    leaf_observed = False

    def lstat(target: Path) -> os.stat_result:
        nonlocal leaf_observed
        if target != path:
            return real_lstat(target)
        if not leaf_observed:
            leaf_observed = True
            return real_lstat(target)
        postread_started.set()
        assert allow_unlink.wait(timeout=5)
        target.unlink()
        return real_lstat(target)

    return lstat


@pytest.mark.parametrize("limit", [0, -1, _EXPECTED_STATE_JSON_FIRST_OUT_OF_RANGE])
def test_Reader_InvalidLimit_IsRejectedBeforeFilesystemAccess(tmp_path: Path, limit: int) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(missing, trusted_root=tmp_path, limit=limit, kind="session")

    assert str(error.value) == _EXPECTED_STATE_JSON_RANGE_ERROR
    assert not missing.exists()


def test_Reader_EmptyDocument_RaisesStateJsonError(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_bytes(b"")

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=16, kind="session")

    assert str(error.value) == "invalid session JSON state"


def test_Reader_NaNConstant_PreservesStdlibJsonBehavior(tmp_path: Path) -> None:
    path = tmp_path / "nan.json"
    path.write_text('{"value": NaN}', encoding="utf-8")

    payload = state_json.read_state_json(path, trusted_root=tmp_path, limit=32, kind="metadata")

    observed_nan = math.isnan(payload["value"])

    assert observed_nan is True


def test_Reader_DuplicateKey_PreservesLastValue(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"value": 1, "value": 2}', encoding="utf-8")

    payload = state_json.read_state_json(path, trusted_root=tmp_path, limit=32, kind="metadata")

    assert payload == {"value": 2}


@pytest.mark.parametrize(("contents", "kind"), [(b"\xff", "checkpoint"), (b"{", "checkpoint")])
def test_Reader_InvalidUtf8OrJson_RaisesStateJsonError(
    tmp_path: Path, contents: bytes, kind: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(contents)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=16, kind=kind)

    assert str(error.value) == "invalid checkpoint JSON state"


def test_Reader_DeepJson_RefusesWithStateJsonError(
    tmp_path: Path, _python_json_scanner_at_recursion_limit_1000: None
) -> None:
    path = tmp_path / "deep.json"
    path.write_bytes(b"[" * 5000 + b"0" + b"]" * 5000)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=12000, kind="checkpoint")

    assert str(error.value) == "invalid checkpoint JSON state"
    assert isinstance(error.value.__cause__, RecursionError)


def test_Reader_ShallowJson_AcceptsWithPythonScanner(
    tmp_path: Path, _python_json_scanner_at_recursion_limit_1000: None
) -> None:
    path = tmp_path / "shallow.json"
    path.write_bytes(b'{"value": 7}')

    payload = state_json.read_state_json(path, trusted_root=tmp_path, limit=16, kind="checkpoint")

    assert payload == {"value": 7}


def test_Reader_OversizedFile_RaisesStateJsonError(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b'{"value": 12345}')

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=8, kind="checkpoint")

    assert str(error.value) == "could not read confined checkpoint JSON state"


def test_Reader_InitialLeafAbsence_RemainsFileNotFound(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError) as error:
        state_json.read_state_json(missing, trusted_root=tmp_path, limit=16, kind="session")

    assert type(error.value) is FileNotFoundError
    assert not missing.exists()


def test_Reader_InitialTrustedRootAbsence_RemainsFileNotFound(tmp_path: Path) -> None:
    trusted_root = tmp_path / "missing-root"
    missing = trusted_root / "state.json"

    with pytest.raises(FileNotFoundError) as error:
        state_json.read_state_json(missing, trusted_root=trusted_root, limit=16, kind="session")

    assert type(error.value) is FileNotFoundError
    assert not trusted_root.exists()
    assert not missing.exists()


def test_Reader_ObservedLeafDisappearance_RaisesStateJsonError(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    postread_started = threading.Event()
    allow_unlink = threading.Event()
    real_lstat = Path.lstat
    lstat = _lstat_with_postread_disappearance(path, postread_started, allow_unlink, real_lstat)
    monkeypatch.setattr(Path, "lstat", lstat)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            state_json.read_state_json, path, trusted_root=tmp_path, limit=16, kind="session"
        )
        handshake_observed = postread_started.wait(timeout=5)
        allow_unlink.set()
        with pytest.raises(state_json.StateJsonError) as error:
            future.result(timeout=5)

    assert handshake_observed is True
    assert str(error.value) == "could not read confined session JSON state"
    assert not path.exists()


def test_Reader_IntegerBeyondDigitLimit_RaisesStateJsonError(
    tmp_path: Path, _bounded_integer_conversion: None
) -> None:
    path = tmp_path / "integer.json"
    path.write_bytes(b"1" * 1000)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=1200, kind="metadata")

    assert str(error.value) == "invalid metadata JSON state"


def test_Reader_OutsideTrustedRoot_RaisesStateJsonError(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(outside, trusted_root=trusted, limit=16, kind="metadata")

    assert str(error.value) == "could not read confined metadata JSON state"
    assert outside.read_bytes() == b"{}"
