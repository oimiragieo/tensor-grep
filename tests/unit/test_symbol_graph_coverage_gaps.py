"""Wave-2a Part G1: symbol-graph files must be handled or DISCLOSED, never silently dropped."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import lang_registry, repo_map


def _names(payload: dict[str, Any]) -> list[str]:
    return [d["name"] for d in payload["definitions"]]


# --------------------------------------------------------------------------------------------
# G1.1 shared source reader (BOM, non-UTF-8, form feed)
# --------------------------------------------------------------------------------------------


def test_python_bom_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbfdef bom_target():\n    return 1\n")
    assert _names(repo_map.build_symbol_defs("bom_target", tmp_path)) == ["bom_target"]


@pytest.mark.requires_grammar
def test_non_utf8_go_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.go").write_bytes(b"package m\n// caf\xe9\nfunc LatinTarget() {}\n")
    assert _names(repo_map.build_symbol_defs("LatinTarget", tmp_path)) == ["LatinTarget"]


@pytest.mark.requires_grammar
def test_cp1252_c_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.c").write_bytes(b"/* caf\xe9 */\nint latin_c_target(void) { return 1; }\n")
    assert _names(repo_map.build_symbol_defs("latin_c_target", tmp_path)) == ["latin_c_target"]


def test_python_call_text_survives_form_feed_lines(tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        "def target():\n    return 1\n\n\x0c\ndef caller():\n    return target()\n",
        encoding="utf-8",
        newline="",
    )
    _refs, calls = repo_map._python_references_and_calls(p, "target")
    assert [c["text"] for c in calls] == ["    return target()"]


def test_read_source_text_strips_bom_and_replaces_bad_bytes(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_bytes(b"\xef\xbb\xbfa\xe9b")
    assert lang_registry.read_source_text(p) == "a�b"


def test_split_source_lines_only_splits_on_newline() -> None:
    assert lang_registry.split_source_lines("a\n\x0cb c\n") == ["a", "\x0cb c"]
