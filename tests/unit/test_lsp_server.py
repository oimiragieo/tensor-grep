from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lsprotocol.types")
pytest.importorskip("pygls.lsp.server")

from lsprotocol.types import (
    ClientCapabilities,
    DefinitionParams,
    DidOpenTextDocumentParams,
    DocumentSymbolParams,
    GeneralClientCapabilities,
    InitializeParams,
    InitializeResult,
    Position,
    PositionEncodingKind,
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
    initialize,
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


def _drive_initialize(server: TensorGrepLSPServer, params: InitializeParams) -> InitializeResult:
    """Pump pygls' generator-based ``lsp_initialize`` protocol method the same way
    ``pygls.protocol.json_rpc.JsonRPCProtocol._run_generator`` does over real
    transport (send each yielded handler's return value back into the generator
    until it raises ``StopIteration``), without needing a live client/asyncio
    event loop.

    This drives pygls' REAL initialize path -- including pygls' own
    position-encoding negotiation and (if registered on *this* server
    instance) the ``@server.feature(INITIALIZE)`` handler under test -- so it
    is the only way to observe the actual ``InitializeResult`` pygls would
    send back to a client (audit B13 regression coverage).
    """
    gen = server.protocol.lsp_initialize(params)
    send_value = None
    while True:
        try:
            handler, args, kwargs = gen.send(send_value)
        except StopIteration as stop:
            return stop.value  # type: ignore[no-any-return]
        send_value = handler(*(args or ()), **(kwargs or {}))


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
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
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


def test_lsp_external_definition_request_is_operation_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    observed_timeouts: list[tuple[float, float]] = []

    class _FakeClient:
        request_timeout_seconds = 20.0
        initialize_timeout_seconds = 20.0

        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/definition"
            observed_timeouts.append((
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            return {
                "uri": module_path.resolve().as_uri(),
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 18},
                },
            }

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setenv("TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS", "0.25")
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
    module_uri = _open_document(server, module_path, "python")

    definition(
        server,
        DefinitionParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
        ),
    )

    assert observed_timeouts
    request_timeout, initialize_timeout = observed_timeouts[0]
    assert request_timeout <= 0.25
    assert initialize_timeout <= 0.25


def test_lsp_external_document_open_uses_same_operation_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    observed: list[tuple[str, float, float]] = []

    class _FakeClient:
        request_timeout_seconds = 30.0
        initialize_timeout_seconds = 30.0

        def __init__(self) -> None:
            self.opened: set[str] = set()

        def ensure_document(self, *, uri: str, **kwargs: object) -> None:
            if uri in self.opened:
                return None
            self.opened.add(uri)
            observed.append((
                "ensure_document",
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            assert method == "textDocument/definition"
            observed.append((
                method,
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            return {
                "uri": module_path.resolve().as_uri(),
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 18},
                },
            }

    fake_client = _FakeClient()

    class _FakeProviders:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return fake_client

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    server.external_providers = _FakeProviders()  # type: ignore[assignment]
    monkeypatch.setenv("TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS", "0.25")
    module_uri = _open_document(server, module_path, "python")

    definition(
        server,
        DefinitionParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
        ),
    )

    assert [current[0] for current in observed] == ["ensure_document", "textDocument/definition"]
    assert all(request_timeout <= 0.25 for _, request_timeout, _ in observed)
    assert all(initialize_timeout <= 0.25 for _, _, initialize_timeout in observed)


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
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
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


def test_lsp_external_references_and_rename_are_operation_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    observed: list[tuple[str, float, float]] = []

    class _FakeClient:
        request_timeout_seconds = 30.0
        initialize_timeout_seconds = 30.0

        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> object:
            observed.append((
                method,
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            if method == "textDocument/references":
                return [
                    {
                        "uri": module_path.resolve().as_uri(),
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 18},
                        },
                    }
                ]
            if method == "textDocument/rename":
                return {
                    "documentChanges": [
                        {
                            "textDocument": {
                                "uri": module_path.resolve().as_uri(),
                                "version": None,
                            },
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
            raise AssertionError(method)

    server = TensorGrepLSPServer("test", "v1")
    server.provider_mode = "lsp"
    monkeypatch.setenv("TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS", "0.5")
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
    module_uri = _open_document(server, module_path, "python")

    references(
        server,
        ReferenceParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
            context=ReferenceContext(include_declaration=True),
        ),
    )
    rename(
        server,
        RenameParams(
            text_document=TextDocumentIdentifier(uri=module_uri),
            position=Position(line=0, character=8),
            new_name="issue_invoice",
        ),
    )

    assert {current[0] for current in observed} == {
        "textDocument/references",
        "textDocument/rename",
    }
    assert all(request_timeout <= 0.5 for _, request_timeout, _ in observed)
    assert all(initialize_timeout <= 0.5 for _, _, initialize_timeout in observed)


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
    monkeypatch.setattr(
        lsp_module, "_external_client_for_uri", lambda ls, uri, **kwargs: _FakeClient()
    )
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


# --- audit B13: position encoding negotiation (previously dead code) --------------


@pytest.mark.parametrize(
    ("position_encodings", "expected_encoding"),
    [
        pytest.param(
            [PositionEncodingKind.Utf8, PositionEncodingKind.Utf16],
            "utf-8",
            id="client_prefers_utf8",
        ),
        pytest.param(
            [PositionEncodingKind.Utf16, PositionEncodingKind.Utf8],
            "utf-16",
            id="client_prefers_utf16_over_utf8",
        ),
        pytest.param([PositionEncodingKind.Utf32], "utf-32", id="utf32_only"),
        pytest.param([PositionEncodingKind.Utf16], "utf-16", id="utf16_only"),
        pytest.param([], "utf-16", id="empty_list_defaults_utf16"),
        pytest.param(None, "utf-16", id="missing_general_capability_defaults_utf16"),
    ],
)
def test_lsp_initialize_negotiates_and_reports_position_encoding(
    position_encodings: list[PositionEncodingKind] | None,
    expected_encoding: str,
) -> None:
    """audit B13 regression: on origin/main, ``_negotiate_position_encoding`` was
    dead code (zero call sites) so ``ls._position_encoding`` stayed stuck at
    "utf-16" no matter what the client advertised. The fix registers
    ``initialize`` as a real ``@server.feature(INITIALIZE)`` handler, which
    must (a) set ``ls._position_encoding`` to the value pygls itself negotiated
    from ``general.positionEncodings`` (respecting the client's *own*
    preference order -- "client_prefers_utf16_over_utf8" would have picked
    "utf-8" under the old dead code's order-blind ``if "utf-8" in supported``
    check, which is exactly the kind of second, disagreeing negotiation this
    fix avoids), and (b) that the SAME value is what the server actually
    reports back in ``InitializeResult.capabilities.positionEncoding`` -- the
    only thing a real client trusts. Absent/empty capabilities must preserve
    today's "utf-16" default (no behavior change for existing clients).
    """
    server = TensorGrepLSPServer("test", "v1")
    server.feature(lsp_module.INITIALIZE)(initialize)
    general = (
        None
        if position_encodings is None
        else GeneralClientCapabilities(position_encodings=position_encodings)
    )
    params = InitializeParams(
        capabilities=ClientCapabilities(general=general),
        process_id=None,
        root_uri=None,
    )

    result = _drive_initialize(server, params)

    assert server._position_encoding == expected_encoding
    assert result.capabilities.position_encoding == expected_encoding


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _utf8_units(text: str) -> int:
    return len(text.encode("utf-8"))


@pytest.mark.parametrize(
    "encoding",
    [
        pytest.param("utf-16", id="utf-16"),
        pytest.param("utf-8", id="utf-8"),
        pytest.param("utf-32", id="utf-32"),
    ],
)
def test_lsp_column_conversion_correct_under_each_encoding_for_non_ascii_prefix(
    tmp_path: Path, encoding: str
) -> None:
    """audit B13 behavioral regression: a line with a non-ASCII (astral -- i.e. a
    UTF-16 surrogate pair AND a 4-byte UTF-8 sequence) character before the
    target symbol must resolve to the CORRECT symbol and round-trip to the
    CORRECT wire column under every negotiated encoding, not just "utf-16".

    On origin/main this already passed for "utf-16" (the only encoding the
    dead negotiation ever produced) but was silently WRONG for "utf-8":
    ``_to_cp_col``/``_from_cp_col`` treated any non-"utf-16" encoding as
    "already codepoints", which only holds for utf-32 (fixed-width, 1 unit per
    codepoint) -- UTF-8 is a *variable-width* byte encoding (1-4 bytes per
    codepoint), so that passthrough silently mis-converted utf-8 columns on
    any non-ASCII line, exactly the "wrong columns on definitions/references/
    rename" bug this fix closes.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    # U+1F600 is beyond the Basic Multilingual Plane: 1 codepoint, 2 UTF-16
    # code units (surrogate pair), 4 UTF-8 bytes, 1 UTF-32 code unit -- so
    # codepoint/utf-16/utf-8 wire columns for anything after it all diverge.
    prefix = "\U0001f600"
    symbol = "create_invoice"
    line = f"{prefix}{symbol}(3)\n"
    module_path.write_text(line, encoding="utf-8")

    cp_col = len(prefix)
    wire_columns = {"utf-16": _utf16_units(prefix), "utf-8": _utf8_units(prefix), "utf-32": cp_col}
    assert wire_columns == {"utf-16": 2, "utf-8": 4, "utf-32": 1}  # sanity-check the fixture

    server = TensorGrepLSPServer("test", "v1")
    server._position_encoding = encoding
    uri = _open_document(server, module_path, "python")

    prepared = prepare_rename(
        server,
        PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=0, character=wire_columns[encoding]),
        ),
    )

    assert prepared is not None
    assert prepared.placeholder == symbol
    assert prepared.range.start.character == wire_columns[encoding]
    assert prepared.range.end.character == wire_columns[encoding] + len(symbol)


def test_lsp_same_position_yields_different_wire_columns_per_encoding(tmp_path: Path) -> None:
    """audit B13: proves the negotiated encoding actually CHANGES the conversion
    result (not merely that each encoding independently "works" in isolation).
    The same logical symbol position must be reported at three different wire
    column numbers depending on ``ls._position_encoding``.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    line = "\U0001f600create_invoice(3)\n"
    module_path.write_text(line, encoding="utf-8")

    reported_columns: dict[str, int] = {}
    for encoding in ("utf-16", "utf-8", "utf-32"):
        server = TensorGrepLSPServer("test", "v1")
        server._position_encoding = encoding
        uri = _open_document(server, module_path, "python")
        prepared = prepare_rename(
            server,
            PrepareRenameParams(
                text_document=TextDocumentIdentifier(uri=uri),
                # codepoint index 1 is unambiguous under every encoding for
                # this fixture line (see the parametrized test above) -- feed
                # it in raw here just to pick a location; what we assert on
                # is the OUTPUT wire column, which _from_cp_col computes.
                position=Position(line=0, character=1),
            ),
        )
        assert prepared is not None
        reported_columns[encoding] = prepared.range.start.character

    assert reported_columns == {"utf-16": 2, "utf-8": 4, "utf-32": 1}
    assert len(set(reported_columns.values())) == 3  # all three differ


def test_lsp_initialize_to_prepare_rename_end_to_end_utf8(tmp_path: Path) -> None:
    """audit B13 end-to-end smoke test: negotiate utf-8 via a real ``initialize``
    call, then prove ``prepare_rename`` actually uses the negotiated encoding
    (not just that ``ls._position_encoding`` was set in isolation, as the
    other tests above check separately)."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8"
    )
    module_path = tmp_path / "module.py"
    line = "\U0001f600create_invoice(3)\n"
    module_path.write_text(line, encoding="utf-8")

    server = TensorGrepLSPServer("test", "v1")
    server.feature(lsp_module.INITIALIZE)(initialize)
    result = _drive_initialize(
        server,
        InitializeParams(
            capabilities=ClientCapabilities(
                general=GeneralClientCapabilities(position_encodings=[PositionEncodingKind.Utf8])
            ),
            process_id=None,
            root_uri=None,
        ),
    )
    assert result.capabilities.position_encoding == "utf-8"
    assert server._position_encoding == "utf-8"

    uri = _open_document(server, module_path, "python")
    prepared = prepare_rename(
        server,
        PrepareRenameParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=0, character=4),  # UTF-8 byte offset of 'c'
        ),
    )

    assert prepared is not None
    assert prepared.placeholder == "create_invoice"
    assert prepared.range.start.character == 4
    assert prepared.range.end.character == 4 + len("create_invoice")
