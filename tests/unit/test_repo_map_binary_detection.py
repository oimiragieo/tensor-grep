"""Round-8 audit: _looks_like_binary_file must not misclassify UTF-16/32 text as binary.

UTF-16 interleaves a NUL byte after every ASCII char, so the naive `b"\\0" in data` heuristic marked
every UTF-16 text file binary -> invisible to every tg command (Windows-relevant: PowerShell /
redirected output and some editors default to UTF-16). A leading UTF-16/32 BOM means text.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.repo_map import _looks_like_binary_file


def test_utf16_le_text_is_not_binary(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello world\nsecond line\n", encoding="utf-16")  # BOM + interleaved NULs
    assert _looks_like_binary_file(path) is False


def test_utf16_be_text_is_not_binary(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    # UTF-16-BE BOM (0xFE 0xFF) prefixed explicitly, then BE-encoded text.
    path.write_bytes(b"\xfe\xff" + "hello world".encode("utf-16-be"))
    assert _looks_like_binary_file(path) is False


def test_real_binary_still_detected(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")  # NUL, no text BOM
    assert _looks_like_binary_file(path) is True


def test_utf8_text_is_not_binary(tmp_path: Path) -> None:
    path = tmp_path / "code.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert _looks_like_binary_file(path) is False
