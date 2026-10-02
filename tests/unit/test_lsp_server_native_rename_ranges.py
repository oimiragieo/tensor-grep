"""The native rename must edit the SYMBOL, not the line it sits on.

`_workspace_edit_for_symbol`'s native branch built every `TextEdit` from `_location_from_entry`, a
range helper written for navigation: columns `0 .. len(line.strip())` of the entry's line (and, for a
definition, `end_line` -- the last line of the whole body). Using that as a rename edit range, with
`new_text=new_name`, REPLACED THE ENTIRE STATEMENT with the new name:
`result = create_invoice(3)` became `issue_invoice`, and the `def` line was cut mid-identifier into
the next line. The existing rename test only asserted `new_text`, never a range, so it passed.

These tests APPLY the returned edits to the real source text (the only oracle that cannot be fooled
by a plausible-looking range) and check the resulting program text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("lsprotocol.types")
pytest.importorskip("pygls.lsp.server")

from lsprotocol.types import (
    DidOpenTextDocumentParams,
    Position,
    RenameParams,
    TextDocumentIdentifier,
    TextDocumentItem,
    WorkspaceEdit,
)

from tensor_grep.cli.lsp_server import TensorGrepLSPServer, did_open, rename


def _open(server: TensorGrepLSPServer, path: Path) -> str:
    uri = path.resolve().as_uri()
    did_open(
        server,
        DidOpenTextDocumentParams(
            text_document=TextDocumentItem(
                uri=uri,
                language_id="python",
                version=1,
                text=path.read_text(encoding="utf-8"),
            )
        ),
    )
    return uri


def _cp(line: str, utf16_col: int) -> int:
    """Codepoint index of a UTF-16 column (the default encoding the server negotiates)."""
    units = 0
    for index, char in enumerate(line):
        if units >= utf16_col:
            return index
        units += len(char.encode("utf-16-le")) // 2
    return len(line)


def _apply(edit: WorkspaceEdit, originals: dict[str, str]) -> dict[str, str]:
    """Apply a WorkspaceEdit's text edits to `originals` (uri -> text); columns are UTF-16 units."""
    results = dict(originals)
    for change in edit.document_changes or []:
        uri = change.text_document.uri  # type: ignore[union-attr]
        lines = results[uri].split("\n")
        edits: list[Any] = sorted(
            change.edits,  # type: ignore[union-attr]
            key=lambda e: (e.range.start.line, e.range.start.character),
            reverse=True,
        )
        for current in edits:
            start, end = current.range.start, current.range.end
            head = lines[start.line][: _cp(lines[start.line], start.character)]
            tail = lines[end.line][_cp(lines[end.line], end.character) :]
            lines[start.line : end.line + 1] = [head + current.new_text + tail]
        results[uri] = "\n".join(lines)
    return results


def _rename(
    tmp_path: Path, files: dict[str, str], at: tuple[str, int, int], new: str
) -> dict[str, str]:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", "utf-8")
    paths = {name: tmp_path / name for name in files}
    for name, text in files.items():
        paths[name].write_text(text, encoding="utf-8")
    server = TensorGrepLSPServer("test", "v1")
    uris = {name: _open(server, path) for name, path in paths.items()}
    name, line, character = at
    edit = rename(
        server,
        RenameParams(
            text_document=TextDocumentIdentifier(uri=uris[name]),
            position=Position(line=line, character=character),
            new_name=new,
        ),
    )
    assert edit is not None
    applied = _apply(edit, {uris[n]: files[n] for n in files})
    return {n: applied[uris[n]] for n in files}


SERVICE = "def create_invoice(total: int) -> int:\n    return total + 1\n"
CONSUMER = "from service import create_invoice\n\nresult = create_invoice(3)\n"


def test_native_rename_replaces_only_the_symbol_in_definition_and_reference(
    tmp_path: Path,
) -> None:
    out = _rename(
        tmp_path,
        {"service.py": SERVICE, "consumer.py": CONSUMER},
        ("consumer.py", 2, 10),
        "issue_invoice",
    )

    assert out["service.py"] == "def issue_invoice(total: int) -> int:\n    return total + 1\n"
    # The repo map's references do not include IMPORT statements (a pre-existing coverage limit,
    # tracked separately), so the import line is left alone; what must hold is that no line is
    # MANGLED: the import is byte-identical and the call is renamed in place.
    assert out["consumer.py"].startswith("from service import create_invoice\n\n")
    assert "result = issue_invoice(3)" in out["consumer.py"]


def test_native_rename_does_not_touch_a_longer_identifier_on_the_same_line(
    tmp_path: Path,
) -> None:
    # CONTROL for identifier boundaries: `create_invoice_total` is a different name.
    consumer = "from service import create_invoice\n\ncreate_invoice_total = create_invoice(3)\n"
    out = _rename(
        tmp_path,
        {"service.py": SERVICE, "consumer.py": consumer},
        ("consumer.py", 2, 25),
        "issue_invoice",
    )

    assert "create_invoice_total = issue_invoice(3)" in out["consumer.py"]
    assert "issue_invoice_total" not in out["consumer.py"]


def test_native_rename_with_non_ascii_text_before_the_symbol_keeps_the_line_intact(
    tmp_path: Path,
) -> None:
    # CONTROL for column encoding: a non-BMP character before the symbol shifts utf-16 columns.
    consumer = (
        "from service import create_invoice\n\nlabel = '\U0001f600'; result = create_invoice(3)\n"
    )
    out = _rename(
        tmp_path,
        {"service.py": SERVICE, "consumer.py": consumer},
        ("consumer.py", 2, 25),
        "issue_invoice",
    )

    assert "label = '\U0001f600'; result = issue_invoice(3)" in out["consumer.py"]
