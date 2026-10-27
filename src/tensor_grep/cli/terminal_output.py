"""Safety helpers for text written directly to a user's terminal."""

from __future__ import annotations

import sys

_TERMINAL_CONTROL_ALLOWED = frozenset({0x09, 0x0A})


def _terminal_code(char: str) -> int:
    code = ord(char)
    return code - 0xDC00 if 0xDC80 <= code <= 0xDCFF else code


def sanitize_terminal_controls(text: str) -> str:
    """Remove active terminal controls while retaining SGR color and line breaks."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        code = _terminal_code(char)
        if char == "\x1b":
            index += 1
            if index >= length:
                break
            introducer = text[index]
            if introducer == "[":
                end = index + 1
                while end < length and not ("@" <= text[end] <= "~"):
                    end += 1
                if end < length:
                    params = text[index + 1 : end]
                    if text[end] == "m" and all(
                        char.isascii() and (char.isdigit() or char in ";:") for char in params
                    ):
                        out.append(text[index - 1 : end + 1])
                    index = end + 1
                    continue
                break
            if introducer in "]PX^_":
                end = index + 1
                while end < length:
                    next_code = _terminal_code(text[end])
                    if next_code == 0x9C or (introducer == "]" and next_code == 0x07):
                        break
                    if text[end] == "\x1b" and end + 1 < length and text[end + 1] == "\\":
                        end += 1
                        break
                    end += 1
                index = min(end + 1, length)
                continue
            index += 1
            continue
        if code == 0x9B:
            end = index + 1
            while end < length and not (0x40 <= _terminal_code(text[end]) <= 0x7E):
                end += 1
            if end < length:
                params = text[index + 1 : end]
                if _terminal_code(text[end]) == ord("m") and all(
                    char.isascii() and (char.isdigit() or char in ";:") for char in params
                ):
                    out.append(text[index : end + 1])
                index = end + 1
                continue
            break
        if code in {0x90, 0x98, 0x9D, 0x9E, 0x9F}:
            end = index + 1
            while end < length:
                next_code = _terminal_code(text[end])
                if next_code == 0x9C or (code == 0x9D and next_code == 0x07):
                    break
                if text[end] == "\x1b" and end + 1 < length and text[end + 1] == "\\":
                    end += 1
                    break
                end += 1
            index = min(end + 1, length)
            continue
        if char == "\r":
            if index + 1 < length and text[index + 1] == "\n":
                out.append(char)
            index += 1
            continue
        if (
            (code < 0x20 and code not in _TERMINAL_CONTROL_ALLOWED)
            or code == 0x7F
            or 0x80 <= code <= 0x9F
        ):
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def stdout_is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return False


def safe_stdout_line(text: str) -> None:
    if stdout_is_tty(sys.stdout):
        text = sanitize_terminal_controls(text)
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None and encoding and "utf" not in encoding and not text.isascii():
        buffer.write(f"{text}\n".encode("utf-8", errors="replace"))
        flush = getattr(buffer, "flush", None)
        if callable(flush):
            flush()
        return
    try:
        print(text)
    except UnicodeEncodeError:
        payload = f"{text}\n".encode("utf-8", errors="replace")
        if buffer is not None:
            buffer.write(payload)
            flush = getattr(buffer, "flush", None)
            if callable(flush):
                flush()
            return
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        escaped_text = f"{text}\n".encode(encoding, errors="backslashreplace").decode(
            encoding, errors="ignore"
        )
        sys.stdout.write(escaped_text)
        flush = getattr(sys.stdout, "flush", None)
        if callable(flush):
            flush()
