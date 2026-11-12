"""Bound JSON serialization before allocating its complete encoded representation."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any


class JsonByteLimitError(ValueError):
    pass


def _check_deadline(deadline_end: float | None) -> None:
    if deadline_end is not None and time.monotonic() >= deadline_end:
        raise TimeoutError("batch JSON deadline exceeded")


def _string_size(value: str, remaining: int, deadline_end: float | None) -> int:
    if len(value) + 2 > remaining:
        raise JsonByteLimitError("batch JSON byte limit exceeded")
    size = 2
    for offset in range(0, len(value), 4096):
        _check_deadline(deadline_end)
        size += len(json.dumps(value[offset : offset + 4096], ensure_ascii=False)[1:-1].encode())
        if size > remaining:
            raise JsonByteLimitError("batch JSON byte limit exceeded")
    return size


def check_json_size(value: object, limit: int, deadline_end: float | None = None) -> None:
    remaining = limit
    active: set[int] = set()
    stack: list[tuple[Iterator[Any], int | None]] = [(iter((value,)), None)]
    while stack:
        _check_deadline(deadline_end)
        iterator, identity = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            if identity is not None:
                active.remove(identity)
            continue
        if isinstance(item, str):
            remaining -= _string_size(item, remaining, deadline_end)
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError("Circular reference detected")
            active.add(identity)
            remaining -= 2 + max(len(item) - 1, 0)
            children: Iterator[Any]
            if isinstance(item, dict):
                remaining -= len(item)
                children = _object_values(item)
            else:
                children = iter(item)
            stack.append((children, identity))
        else:
            remaining -= len(json.dumps(item, allow_nan=False).encode())
        if remaining < 0:
            raise JsonByteLimitError("batch JSON byte limit exceeded")


def _object_values(value: dict[Any, Any]) -> Iterator[Any]:
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("batch JSON object keys must be strings")
        yield key
        yield item


def iter_json_bytes(
    value: object, limit: int, deadline_end: float | None = None
) -> Iterator[bytes]:
    check_json_size(value, limit, deadline_end)
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    remaining = limit
    for text in encoder.iterencode(value):
        for offset in range(0, len(text), 4096):
            _check_deadline(deadline_end)
            chunk = text[offset : offset + 4096].encode()
            remaining -= len(chunk)
            if remaining < 0:
                raise JsonByteLimitError("batch JSON byte limit exceeded")
            yield chunk
