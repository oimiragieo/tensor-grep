from __future__ import annotations

import sys
from collections.abc import Iterator

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
