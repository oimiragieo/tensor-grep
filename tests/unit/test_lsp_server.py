from __future__ import annotations

from pathlib import Path

from lsprotocol.types import (
    DefinitionParams,
    DidOpenTextDocumentParams,
    DocumentSymbolParams,
    Position,
    PrepareRenameParams,
    ReferenceContext,
    ReferenceParams,
    RenameParams,
    TextDocumentIdentifier,
    TextDocumentItem,
    WorkspaceSymbolParams,
)

import tensor_grep.cli.lsp_server as lsp_module
from tensor_grep.cli.lsp_server import (
    TensorGrepLSPServer,
    definition,
    did_open,
    document_symbol,
    prepare_rename,
    references,
    rename,
    workspace_symbol,
)


def _open_document(server: TensorGrepLSPServer, path: Path, language_id: str) -> str:
    uri = path.resolve().as_uri()
    did_open(
        server,
        DidOpenTextDocumentParams(
            text_document=TextDocumentItem(
                uri=uri,
                language_id=language_id,
                version=1,
                text=path.read_text(encoding="utf-8"),
            )
        ),
    )
    return uri


def test_lsp_definition_and_references_for_python_symbol(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    server = TensorGrepLSPServer("test", "v1")
    consumer_uri = _open_document(server, consumer_path, "python")
    _open_document(server, service_path, "python")

    definition_locations = definition(
        server,
        DefinitionParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=2, character=10),
        ),
    )
    reference_locations = references(
        server,
        ReferenceParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=2, character=10),
            context=ReferenceContext(include_declaration=True),
        ),
    )

    assert len(definition_locations) == 1
    assert definition_locations[0].uri == service_path.resolve().as_uri()
    assert any(location.uri == consumer_path.resolve().as_uri() for location in reference_locations)


def test_lsp_document_and_workspace_symbols_for_python_repo(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    extra_path = tmp_path / "extra.py"
    module_path.write_text(
        "class Invoice:\n    pass\n\n\ndef create_invoice() -> Invoice:\n    return Invoice()\n",
        encoding="utf-8",
    )
    extra_path.write_text("def close_invoice() -> None:\n    return None\n", encoding="utf-8")

    server = TensorGrepLSPServer("test", "v1")
    module_uri = _open_document(server, module_path, "python")
    _open_document(server, extra_path, "python")

    symbols = document_symbol(
        server,
        DocumentSymbolParams(text_document=TextDocumentIdentifier(uri=module_uri)),
    )
    workspace_matches = workspace_symbol(server, WorkspaceSymbolParams(query="invoice"))

    assert {symbol.name for symbol in symbols} >= {"Invoice", "create_invoice"}
    assert {symbol.name for symbol in workspace_matches} >= {
        "Invoice",
        "create_invoice",
        "close_invoice",
    }


def test_lsp_definition_and_references_for_rust_symbol(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = 'demo'\nversion = '0.1.0'\nedition = '2021'\n",
        encoding="utf-8",
    )
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    lib_path = src_dir / "lib.rs"
    consumer_path = src_dir / "consumer.rs"
    lib_path.write_text("pub fn issue_invoice() -> u32 {\n    7\n}\n", encoding="utf-8")
    consumer_path.write_text(
        "use crate::issue_invoice;\n\npub fn consume() -> u32 {\n    issue_invoice()\n}\n",
        encoding="utf-8",
    )

    server = TensorGrepLSPServer("test", "v1")
    consumer_uri = _open_document(server, consumer_path, "rust")
    _open_document(server, lib_path, "rust")

    definition_locations = definition(
        server,
        DefinitionParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=3, character=6),
        ),
    )
    reference_locations = references(
        server,
        ReferenceParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=3, character=6),
            context=ReferenceContext(include_declaration=True),
        ),
    )

    assert len(definition_locations) == 1
    assert definition_locations[0].uri == lib_path.resolve().as_uri()
    assert any(location.uri == consumer_path.resolve().as_uri() for location in reference_locations)


def test_lsp_prepare_rename_and_rename_for_python_symbol(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    server = TensorGrepLSPServer("test", "v1")
    consumer_uri = _open_document(server, consumer_path, "python")
    _open_document(server, service_path, "python")

    prepared = prepare_rename(
        server,
        PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=2, character=10),
        ),
    )
    edit = rename(
        server,
        RenameParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=2, character=10),
            new_name="issue_invoice",
        ),
    )

    assert prepared is not None
    assert prepared.placeholder == "create_invoice"
    assert prepared.range.start.line == 2
    assert edit is not None
    assert edit.document_changes is not None
    edit_map = {change.text_document.uri: change.edits for change in edit.document_changes}
    assert service_path.resolve().as_uri() in edit_map
    assert consumer_path.resolve().as_uri() in edit_map
    assert any(
        current.new_text == "issue_invoice" for current in edit_map[service_path.resolve().as_uri()]
    )
    assert any(
        current.new_text == "issue_invoice"
        for current in edit_map[consumer_path.resolve().as_uri()]
    )


def test_lsp_external_definition_mode_prefers_external_result(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/definition"
            return {
                "uri": module_path.resolve().as_uri(),
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 18},
                },
            }

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setattr(lsp_module, "_external_client_for_uri", lambda ls, uri: _FakeClient())
    module_uri = _open_document(server, module_path, "python")

    definition_locations = definition(
        server,
        DefinitionParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
        ),
    )

    assert len(definition_locations) == 1
    assert definition_locations[0].range.start.character == 4


def test_lsp_hybrid_references_merge_external_and_native(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    class _FakeClient:
        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/references"
            return [
                {
                    "uri": service_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 18},
                    },
                }
            ]

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "hybrid"
    monkeypatch.setattr(lsp_module, "_external_client_for_uri", lambda ls, uri: _FakeClient())
    consumer_uri = _open_document(server, consumer_path, "python")
    _open_document(server, service_path, "python")

    reference_locations = references(
        server,
        ReferenceParams(
            text_document=TextDocumentIdentifier(uri=consumer_uri),
            position=Position(line=2, character=10),
            context=ReferenceContext(include_declaration=True),
        ),
    )

    uris = {location.uri for location in reference_locations}
    assert service_path.resolve().as_uri() in uris
    assert consumer_path.resolve().as_uri() in uris


def test_lsp_external_rename_uses_provider_workspace_edit(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/rename"
            return {
                "documentChanges": [
                    {
                        "textDocument": {"uri": module_path.resolve().as_uri(), "version": None},
                        "edits": [
                            {
                                "range": {
                                    "start": {"line": 0, "character": 4},
                                    "end": {"line": 0, "character": 18},
                                },
                                "newText": "issue_invoice",
                            }
                        ],
                    }
                ]
            }

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setattr(lsp_module, "_external_client_for_uri", lambda ls, uri: _FakeClient())
    module_uri = _open_document(server, module_path, "python")

    edit = rename(
        server,
        RenameParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
            new_name="issue_invoice",
        ),
    )

    assert edit is not None
    assert edit.document_changes is not None
    assert edit.document_changes[0].edits[0].new_text == "issue_invoice"
