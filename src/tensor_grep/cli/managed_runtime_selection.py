from __future__ import annotations

import json
import re
from dataclasses import dataclass

_MAX_RECORD_BYTES = 512
_FIXED_RECORD_BYTES = len(b'{"schema_version":1,"generation":"","version":""}\n')
_FIELDS = {"schema_version", "generation", "version"}
_GENERATION = re.compile(r"[0-9a-f]{32}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


@dataclass(frozen=True)
class ManagedRuntimeSelection:
    generation: str
    version: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("selection record has duplicate keys")
        record[key] = value
    return record


def _validate(selection: ManagedRuntimeSelection) -> None:
    if not isinstance(selection.generation, str) or not isinstance(selection.version, str):
        raise ValueError("selection generation and version must be strings")
    # Bound regex work and serialization for directly constructed values.
    if _FIXED_RECORD_BYTES + len(selection.generation) + len(selection.version) > _MAX_RECORD_BYTES:
        raise ValueError("selection record exceeds 512 bytes")
    if _GENERATION.fullmatch(selection.generation) is None:
        raise ValueError("selection generation must be 32 lowercase hexadecimal characters")
    if _VERSION.fullmatch(selection.version) is None:
        raise ValueError("selection version must be an exact X.Y.Z release")


def decode_selection(payload: bytes) -> ManagedRuntimeSelection:
    if len(payload) > _MAX_RECORD_BYTES:
        raise ValueError("selection record exceeds 512 bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("selection record must be valid UTF-8") from None
    try:
        record = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError:
        raise ValueError("selection record is not valid JSON") from None
    if not isinstance(record, dict):
        raise ValueError("selection record must be a JSON object")
    if set(record) != _FIELDS:
        raise ValueError(
            "selection record fields must be exactly schema_version, generation, and version"
        )
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("selection schema_version must be the integer 1")
    generation = record["generation"]
    version = record["version"]
    if not isinstance(generation, str) or not isinstance(version, str):
        raise ValueError("selection generation and version must be strings")
    selection = ManagedRuntimeSelection(generation=generation, version=version)
    _validate(selection)
    return selection


def encode_selection(selection: ManagedRuntimeSelection) -> bytes:
    _validate(selection)
    payload = (
        json.dumps(
            {
                "schema_version": 1,
                "generation": selection.generation,
                "version": selection.version,
            },
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    if len(payload) > _MAX_RECORD_BYTES:
        raise ValueError("selection record exceeds 512 bytes")
    return payload
