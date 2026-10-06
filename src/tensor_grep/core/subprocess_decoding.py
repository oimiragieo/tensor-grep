from __future__ import annotations

import os


def decode_protocol_output(value: bytes | str | None) -> str:
    """Decode machine-readable subprocess stdout as strict UTF-8.

    A string is accepted for compatibility with callers and tests that already decoded the
    stream. Production subprocesses should use ``text=False`` so malformed protocol bytes are
    rejected here instead of being replaced before parsing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8")


def decode_diagnostic_output(value: bytes | str | None) -> str:
    """Decode human-facing subprocess diagnostics as UTF-8, replacing malformed bytes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def decode_path_record(value: bytes | str) -> str:
    """Decode one filesystem path record with the platform filesystem codec."""
    return os.fsdecode(value)
