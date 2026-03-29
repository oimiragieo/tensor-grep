from __future__ import annotations

import io
import typing as t


def _stdin_binary() -> io.BytesIO:
    return io.BytesIO(b"stdin")


def _stdout_binary() -> io.BytesIO:
    return io.BytesIO(b"stdout")


def _stderr_binary() -> io.BytesIO:
    return io.BytesIO(b"stderr")


def _stdin_text(encoding: str | None = None, errors: str | None = "strict") -> io.StringIO:
    return io.StringIO("stdin")


def _stdout_text(encoding: str | None = None, errors: str | None = "strict") -> io.StringIO:
    return io.StringIO("stdout")


def _stderr_text(encoding: str | None = None, errors: str | None = "strict") -> io.StringIO:
    return io.StringIO("stderr")


binary_streams: dict[str, t.Callable[[], io.BytesIO]] = {
    "stdin": _stdin_binary,
    "stdout": _stdout_binary,
    "stderr": _stderr_binary,
}

text_streams: dict[str, t.Callable[[str | None, str | None], io.StringIO]] = {
    "stdin": _stdin_text,
    "stdout": _stdout_text,
    "stderr": _stderr_text,
}


def get_binary_stream(name: t.Literal["stdin", "stdout", "stderr"]) -> t.BinaryIO:
    """Derived from click.get_binary_stream for patch benchmarking."""
    opener = text_streams.get(name)
    if opener is None:
        raise TypeError(f"Unknown standard stream '{name}'")
    return opener()
