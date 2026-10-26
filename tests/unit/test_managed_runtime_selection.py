from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from functools import partial
from typing import Any, cast

import pytest

from tensor_grep.cli.managed_runtime_selection import (
    ManagedRuntimeSelection,
    decode_selection,
    encode_selection,
)

_ORIGINAL_JSON_DUMPS = json.dumps
_VALID_PAYLOAD = (
    b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3"}\n'
)


def _record_json_dumps(*args: Any, calls: list[None], **kwargs: Any) -> str:
    calls.append(None)
    return _ORIGINAL_JSON_DUMPS(*args, **kwargs)


def test_DecodeSelection_ValidRecord_ReturnsExactGenerationAndVersion() -> None:
    selection = decode_selection(_VALID_PAYLOAD)

    assert selection == ManagedRuntimeSelection(
        generation="0123456789abcdef0123456789abcdef", version="1.2.3"
    )
    assert selection.generation == "0123456789abcdef0123456789abcdef"
    assert selection.version == "1.2.3"


def test_EncodeSelection_ValidRecord_ReturnsCanonicalBytes() -> None:
    selection = ManagedRuntimeSelection(
        generation="0123456789abcdef0123456789abcdef", version="1.2.3"
    )

    encoded = encode_selection(selection)

    assert encoded == _VALID_PAYLOAD


def test_ManagedRuntimeSelection_DecodedValue_IsFrozen() -> None:
    selection = decode_selection(_VALID_PAYLOAD)

    with pytest.raises(FrozenInstanceError):
        selection.version = "1.2.4"  # type: ignore[misc]


def test_DecodeSelection_MaximumRecordLength_Accepts512Bytes() -> None:
    payload = _VALID_PAYLOAD + b" " * (512 - len(_VALID_PAYLOAD))

    selection = decode_selection(payload)

    assert selection.generation == "0123456789abcdef0123456789abcdef"
    assert selection.version == "1.2.3"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(b"\xff", "selection record must be valid UTF-8", id="invalid-utf8"),
        pytest.param(b"{", "selection record is not valid JSON", id="invalid-json"),
        pytest.param(b"[]", "selection record must be a JSON object", id="non-object"),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3"}',
            "selection record has duplicate keys",
            id="duplicate-keys",
        ),
        pytest.param(
            b'{"schema_version":true,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection schema_version must be the integer 1",
            id="boolean-schema",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef"}',
            "selection record fields must be exactly schema_version, generation, and version",
            id="missing-field",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3","path":"elsewhere"}',
            "selection record fields must be exactly schema_version, generation, and version",
            id="unknown-field",
        ),
        pytest.param(
            b'{"schema_version":1.0,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection schema_version must be the integer 1",
            id="float-schema",
        ),
        pytest.param(
            b'{"schema_version":2,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection schema_version must be the integer 1",
            id="unsupported-schema",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":7,"version":"1.2.3"}',
            "selection generation and version must be strings",
            id="non-string-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":7}',
            "selection generation and version must be strings",
            id="non-string-version",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"/0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="absolute-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"C:\\\\0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="windows-absolute-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"../0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="traversal-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789ABCDEF0123456789ABCDEF",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="uppercase-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcde\\u0661",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="unicode-digit-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef\\n",'
            b'"version":"1.2.3"}',
            "selection generation must be 32 lowercase hexadecimal characters",
            id="trailing-newline-generation",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.02.3"}',
            "selection version must be an exact X.Y.Z release",
            id="leading-zero-version",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2"}',
            "selection version must be an exact X.Y.Z release",
            id="short-version",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3rc1"}',
            "selection version must be an exact X.Y.Z release",
            id="prerelease-version",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3.post1"}',
            "selection version must be an exact X.Y.Z release",
            id="postrelease-version",
        ),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"version":"1.2.3+local"}',
            "selection version must be an exact X.Y.Z release",
            id="local-version",
        ),
        pytest.param(b" " * 513, "selection record exceeds 512 bytes", id="oversized"),
    ],
)
def test_DecodeSelection_InvalidRecord_RaisesSanitizedValueError(
    payload: bytes, message: str
) -> None:
    with pytest.raises(ValueError) as error:
        decode_selection(payload)

    assert str(error.value) == message
    assert payload.decode("utf-8", errors="replace") not in str(error.value)


@pytest.mark.parametrize(
    ("generation", "version", "message"),
    [
        pytest.param(
            "../0123456789abcdef0123456789abcdef",
            "1.2.3",
            "selection generation must be 32 lowercase hexadecimal characters",
            id="traversal-generation",
        ),
        pytest.param(
            "0123456789abcdef0123456789abcdef",
            "1.02.3",
            "selection version must be an exact X.Y.Z release",
            id="malformed-version",
        ),
        pytest.param(
            "0123456789abcdef0123456789abcdef",
            cast(str, 12),
            "selection generation and version must be strings",
            id="non-string-version",
        ),
        pytest.param(
            cast(str, 7),
            "1.2.3",
            "selection generation and version must be strings",
            id="non-string-generation",
        ),
    ],
)
def test_EncodeSelection_InvalidValues_RaisesValueError(
    generation: str, version: str, message: str
) -> None:
    selection = ManagedRuntimeSelection(generation=generation, version=version)

    with pytest.raises(ValueError) as error:
        encode_selection(selection)

    assert str(error.value) == message


def test_EncodeSelection_EncodedRecordOver512Bytes_RaisesValueError() -> None:
    selection = ManagedRuntimeSelection(
        generation="0123456789abcdef0123456789abcdef", version=f"1.{'1' * 500}.0"
    )

    with pytest.raises(ValueError) as error:
        encode_selection(selection)

    assert str(error.value) == "selection record exceeds 512 bytes"


def test_EncodeSelection_EncodedRecordOver512Bytes_RefusesBeforeJsonSerialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    monkeypatch.setattr(json, "dumps", partial(_record_json_dumps, calls=calls))
    selection = ManagedRuntimeSelection(
        generation="0123456789abcdef0123456789abcdef", version=f"1.{'1' * 500}.0"
    )

    with pytest.raises(ValueError) as error:
        encode_selection(selection)

    assert str(error.value) == "selection record exceeds 512 bytes"
    assert calls == []


def test_EncodeSelection_MaximumEncodedRecordLength_ReturnsExact512Bytes() -> None:
    selection = ManagedRuntimeSelection(
        generation="0123456789abcdef0123456789abcdef", version=f"1.{'1' * 426}.0"
    )

    encoded = encode_selection(selection)
    expected = (
        b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
        b'"version":"1.'
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"1111111111111111111111111111111111111111"
        b"11111111111111111111111111"
        b'.0"}\n'
    )

    assert encoded == expected
    assert len(encoded) == 512
