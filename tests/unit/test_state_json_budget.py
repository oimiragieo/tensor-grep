from __future__ import annotations

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


def test_Config_MaximumAccepted_ReturnsValue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", str(_EXPECTED_STATE_JSON_MAXIMUM))

    actual = state_json.configured_state_json_limit()

    assert actual == _EXPECTED_STATE_JSON_MAXIMUM


@pytest.mark.parametrize(
    ("raw_limit", "expected"),
    [
        ("1", 1),
        ("+1", 1),
        ("00016", 16),
        ("+1_024", 1024),
        ("0_0_1", 1),
        ("+0_001", 1),
        (" 16 ", 16),
        ("\t16\t", 16),
        ("\n16\n", 16),
        ("\r16\r", 16),
        ("\f16\f", 16),
        ("\v16\v", 16),
        (" \t\n\r\f\v+1_024\v\f\r\n\t ", 1024),
    ],
)
def test_Config_AsciiSignedUnderscoredDecimal_ReturnsExpectedLimit(
    monkeypatch: pytest.MonkeyPatch, raw_limit: str, expected: int
) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    actual = state_json.configured_state_json_limit()

    assert actual == expected


@pytest.mark.parametrize(
    "raw_limit",
    [
        "",
        " ",
        "+",
        "-",
        "+ 1",
        "- 1",
        "++1",
        "+-1",
        "--1",
        "1 0",
        "1\t0",
        "1\n0",
        "_1",
        "1_",
        "1__0",
        "1.0",
        "1e3",
        "0x10",
        "1,000",
        "\u0661",
        "\uff11",
        "1_\u0662",
        "\u00a01",
        "\u20031",
        "1\u2028",
        "1\u0085",
        "\ufeff1",
        "\x1c1",
        "\x1d1",
        "\x1e1",
        "\x1f1",
        "\x071",
        "\x7f1",
    ],
)
def test_Config_NonAsciiOrMalformedToken_RaisesDecimalError(
    monkeypatch: pytest.MonkeyPatch, raw_limit: str
) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.configured_state_json_limit()

    assert str(error.value) == "TG_STATE_JSON_MAX_BYTES must be a decimal integer"


@pytest.mark.parametrize(
    "raw_limit",
    [
        "0",
        "+0",
        "-0",
        "0_0",
        "-1",
        "-0001",
        str(_EXPECTED_STATE_JSON_FIRST_OUT_OF_RANGE),
        str(_EXPECTED_STATE_JSON_FIRST_OUT_OF_RANGE + 1),
    ],
)
def test_Config_AsciiNumericOutsideRange_RaisesRangeError(
    monkeypatch: pytest.MonkeyPatch, raw_limit: str
) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.configured_state_json_limit()

    assert str(error.value) == _EXPECTED_STATE_JSON_RANGE_ERROR


@pytest.mark.parametrize("raw_limit", ["0" * 1000 + "1", "+" + "0_" * 1000 + "1"])
def test_Config_LongLeadingZeroTokenWithDigitCeiling_ReturnsOne(
    monkeypatch: pytest.MonkeyPatch, _bounded_integer_conversion: None, raw_limit: str
) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    actual = state_json.configured_state_json_limit()

    assert actual == 1


@pytest.mark.parametrize("raw_limit", ["9" * 1000, "-" + "0" * 1000 + "1", "0" * 1000])
def test_Config_LongNumericTokenWithDigitCeiling_RaisesRangeError(
    monkeypatch: pytest.MonkeyPatch, _bounded_integer_conversion: None, raw_limit: str
) -> None:
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.configured_state_json_limit()

    assert str(error.value) == _EXPECTED_STATE_JSON_RANGE_ERROR


def test_Config_UnsetValue_Uses64MiBDefault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_STATE_JSON_MAX_BYTES", raising=False)

    actual = state_json.configured_state_json_limit()

    assert actual == 67_108_864


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


@pytest.mark.parametrize(
    ("raw_limit", "expected_message"),
    [
        ("0", _EXPECTED_STATE_JSON_RANGE_ERROR),
        ("-1", _EXPECTED_STATE_JSON_RANGE_ERROR),
        (str(_EXPECTED_STATE_JSON_FIRST_OUT_OF_RANGE), _EXPECTED_STATE_JSON_RANGE_ERROR),
        ("", "TG_STATE_JSON_MAX_BYTES must be a decimal integer"),
        ("invalid", "TG_STATE_JSON_MAX_BYTES must be a decimal integer"),
    ],
)
def test_Config_InvalidMaximum_IsRejectedBeforeRead(
    monkeypatch: pytest.MonkeyPatch,
    raw_limit: str,
    expected_message: str,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", raw_limit)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(
            missing,
            trusted_root=tmp_path,
            limit=state_json.configured_state_json_limit(),
            kind="session",
        )

    assert str(error.value) == expected_message
    assert not missing.exists()


def test_Config_UnicodeToken_IsRejectedBeforeStateRead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", "\u0661")

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(
            missing,
            trusted_root=tmp_path,
            limit=state_json.configured_state_json_limit(),
            kind="session",
        )

    assert str(error.value) == "TG_STATE_JSON_MAX_BYTES must be a decimal integer"
    assert not missing.exists()


def test_Config_ValidToken_ReachesConfinedRead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    monkeypatch.setenv("TG_STATE_JSON_MAX_BYTES", "+0_064")

    actual = state_json.read_state_json(
        path,
        trusted_root=tmp_path,
        limit=state_json.configured_state_json_limit(),
        kind="session",
    )

    assert actual == {}


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


def test_Reader_DeepJson_RefusesWithStateJsonError(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_bytes(b"[" * 5000 + b"0" + b"]" * 5000)

    with pytest.raises(state_json.StateJsonError) as error:
        state_json.read_state_json(path, trusted_root=tmp_path, limit=12000, kind="checkpoint")

    assert str(error.value) == "invalid checkpoint JSON state"


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
