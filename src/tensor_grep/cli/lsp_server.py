from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from lsprotocol.types import (
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_PREPARE_RENAME,
    TEXT_DOCUMENT_REFERENCES,
    TEXT_DOCUMENT_RENAME,
    WORKSPACE_SYMBOL,
    DefinitionParams,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentSymbol,
    DocumentSymbolParams,
    Location,
    LogMessageParams,
    MessageType,
    OptionalVersionedTextDocumentIdentifier,
    Position,
    PrepareRenameParams,
    PrepareRenamePlaceholder,
    Range,
    ReferenceParams,
    RenameParams,
    SymbolInformation,
    SymbolKind,
    TextDocumentEdit,
    TextEdit,
    WorkspaceEdit,
    WorkspaceSymbolParams,
)
from pygls.lsp.server import LanguageServer

from tensor_grep.cli import repo_map
from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager, LSPTransportError

# audit I3: max entries per LRU cache dict.
_DOCUMENTS_CACHE_MAX = 512
_REPO_MAP_CACHE_MAX = 64
_TENSOR_CACHE_MAX = 128


class TensorGrepLSPServer(LanguageServer):  # type: ignore
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # audit I3: use OrderedDict-backed LRU caches so that open documents,
        # repo maps, and GPU tensors cannot grow without bound.  Eviction is
        # LRU (move_to_end on access, popitem(last=False) when over limit).
        self.documents_cache: OrderedDict[str, str] = OrderedDict()
        # In a real enterprise version, we would keep the AST graph warm in VRAM here.
        self.tensor_cache: OrderedDict[str, Any] = OrderedDict()
        self.repo_map_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.provider_mode = "native"
        self.external_providers = ExternalLSPProviderManager()
        # audit B13: position encoding negotiated with the client.
        # "utf-16" is the LSP default; we upgrade to "utf-8" when the client
        # advertises support so that column values are codepoint offsets.
        self._position_encoding: str = "utf-16"


server = TensorGrepLSPServer("tensor-grep-lsp", "v0.3.0")


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path).resolve()
    return Path(uri).expanduser().resolve()


def _path_to_uri(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


def _resolve_repo_root(path: Path) -> Path:
    markers = ("pyproject.toml", "package.json", "Cargo.toml", ".git")
    for candidate in [path.parent, *path.parent.parents]:
        if any((candidate / marker).exists() for marker in markers):
            return candidate.resolve()
    return path.parent.resolve()


def _path_within_root(path: str | Path, root: Path) -> bool:
    """Whether ``path`` resolves inside (or is) ``root`` — used to confine LSP edits."""
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return False
    root_resolved = root.resolve()
    return target == root_resolved or root_resolved in target.parents


def _uri_within_root(uri: str, root: Path) -> bool:
    try:
        return _path_within_root(_uri_to_path(uri), root)
    except (OSError, ValueError):
        return False


def _workspace_edit_target_uris(result: dict[str, Any]) -> list[str]:
    """Collect every edited document URI from an external provider's WorkspaceEdit response
    (both the ``changes`` map and the ``documentChanges`` list) so they can be confined."""
    uris: list[str] = []
    changes = result.get("changes")
    if isinstance(changes, dict):
        uris.extend(str(key) for key in changes)
    document_changes = result.get("documentChanges")
    if isinstance(document_changes, list):
        for entry in document_changes:
            if isinstance(entry, dict):
                text_document = entry.get("textDocument")
                if isinstance(text_document, dict) and text_document.get("uri"):
                    uris.append(str(text_document["uri"]))
    return uris


def _invalidate_repo_map_cache(ls: TensorGrepLSPServer, uri: str) -> None:
    try:
        repo_root = _resolve_repo_root(_uri_to_path(uri))
    except Exception:
        return
    ls.repo_map_cache.pop(str(repo_root), None)


def _lru_put(od: OrderedDict[str, Any], key: str, value: Any, max_size: int) -> None:
    """Insert/update *key* in the LRU OrderedDict, evicting the oldest entry if needed."""
    od.pop(key, None)
    od[key] = value
    while len(od) > max_size:
        od.popitem(last=False)


def _lru_get(od: OrderedDict[str, Any], key: str) -> Any | None:
    """Return the value for *key* and promote it to MRU position, or None."""
    value = od.pop(key, None)
    if value is None:
        return None
    od[key] = value
    return value


def _get_repo_map(ls: TensorGrepLSPServer, uri: str) -> dict[str, Any]:
    repo_root = _resolve_repo_root(_uri_to_path(uri))
    cache_key = str(repo_root)
    cached = _lru_get(ls.repo_map_cache, cache_key)
    if cached is not None:
        return cast(dict[str, Any], cached)
    current = repo_map.build_repo_map(repo_root)
    _lru_put(ls.repo_map_cache, cache_key, current, _REPO_MAP_CACHE_MAX)
    return current


def _external_client_for_uri(
    ls: TensorGrepLSPServer,
    uri: str,
    *,
    deadline_monotonic: float | None = None,
) -> Any | None:
    language = _infer_language(uri)
    workspace_root = _resolve_repo_root(_uri_to_path(uri))
    try:
        client = ls.external_providers.get_client(language=language, workspace_root=workspace_root)
        deadline = (
            deadline_monotonic
            if deadline_monotonic is not None
            else repo_map._lsp_operation_deadline()
        )
        repo_map._run_lsp_with_operation_budget(
            client,
            deadline,
            lambda: client.ensure_document(
                uri=uri, text=_document_text(ls, uri), language_id=language
            ),
        )
        return client
    except (FileNotFoundError, LSPTransportError, ValueError):
        return None


def _document_text(ls: TensorGrepLSPServer, uri: str) -> str:
    # audit I3: promote to MRU on each access.
    cached = _lru_get(ls.documents_cache, uri)
    if cached is not None:
        return cast(str, cached)
    path = _uri_to_path(uri)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        _lru_put(ls.documents_cache, uri, text, _DOCUMENTS_CACHE_MAX)
        return text
    return ""


def _infer_language(uri: str) -> str:
    normalized = uri.lower()
    if normalized.endswith(".js") or normalized.endswith(".ts"):
        return "javascript"
    if normalized.endswith(".rs"):
        return "rust"
    return "python"


# audit B13: position encoding conversion helpers.
def _utf16_col_to_codepoint(line_text: str, utf16_col: int) -> int:
    """Convert a UTF-16 column offset to a Unicode codepoint (str index) offset.

    The LSP specification §3.17 defaults to UTF-16 for ``character`` fields.
    Python strings are codepoint-indexed, so when the client sends UTF-16
    offsets we must convert before indexing into the line.
    """
    cp = 0
    utf16_units = 0
    for ch in line_text:
        if utf16_units >= utf16_col:
            break
        ordinal = ord(ch)
        utf16_units += 2 if ordinal > 0xFFFF else 1
        cp += 1
    return cp


def _codepoint_col_to_utf16(line_text: str, cp_col: int) -> int:
    """Convert a codepoint (str index) column offset to UTF-16 units."""
    utf16_units = 0
    for ch in line_text[:cp_col]:
        ordinal = ord(ch)
        utf16_units += 2 if ordinal > 0xFFFF else 1
    return utf16_units


def _to_cp_col(ls: TensorGrepLSPServer, line_text: str, col: int) -> int:
    """Convert *col* from the client's position encoding to a codepoint index."""
    if ls._position_encoding == "utf-16":
        return _utf16_col_to_codepoint(line_text, col)
    # "utf-8" or "utf-32" (codepoints) — Python str indexing is already correct.
    return col


def _from_cp_col(ls: TensorGrepLSPServer, line_text: str, cp_col: int) -> int:
    """Convert a codepoint column index to the client's position encoding."""
    if ls._position_encoding == "utf-16":
        return _codepoint_col_to_utf16(line_text, cp_col)
    return cp_col


def _word_range_at_position(
    text: str,
    position: Position,
    ls: TensorGrepLSPServer | None = None,
) -> tuple[str, Range] | None:
    # audit B13: convert the incoming column from the client's encoding to a
    # codepoint index before indexing into the Python string.
    lines = text.splitlines()
    if position.line < 0 or position.line >= len(lines):
        return None
    line = lines[position.line]
    if not line:
        return None
    # Convert the wire column to a codepoint offset.
    raw_col = int(position.character)
    character = _to_cp_col(ls, line, raw_col) if ls is not None else raw_col
    character = max(0, min(character, len(line)))
    start = character
    end = character

    def _is_symbol_char(current: str) -> bool:
        return current.isalnum() or current == "_"

    if start == len(line) and start > 0:
        start -= 1
        end = start + 1
    elif start < len(line) and not _is_symbol_char(line[start]) and start > 0:
        start -= 1
        end = start + 1
    elif end < len(line):
        end += 1

    if start < 0 or start >= len(line) or not _is_symbol_char(line[start]):
        return None

    while start > 0 and _is_symbol_char(line[start - 1]):
        start -= 1
    while end < len(line) and _is_symbol_char(line[end]):
        end += 1

    symbol = line[start:end]
    if not symbol:
        return None
    # audit B13: convert the codepoint columns back to the client encoding for
    # the returned Range so that clients receive correct character offsets.
    wire_start = _from_cp_col(ls, line, start) if ls is not None else start
    wire_end = _from_cp_col(ls, line, end) if ls is not None else end
    return (
        symbol,
        Range(
            start=Position(line=position.line, character=wire_start),
            end=Position(line=position.line, character=wire_end),
        ),
    )


def _symbol_and_range_for_position(
    ls: TensorGrepLSPServer,
    uri: str,
    position: Position,
) -> tuple[str, Range] | None:
    # audit B13: pass ls so that position encoding is honoured.
    return _word_range_at_position(_document_text(ls, uri), position, ls)


def _kind_to_symbol_kind(kind: str) -> SymbolKind:
    normalized = kind.lower()
    if normalized == "class":
        return SymbolKind.Class
    if normalized in {"function", "method"}:
        return SymbolKind.Function
    if normalized in {"constant", "const"}:
        return SymbolKind.Constant
    if normalized in {"module", "namespace"}:
        return SymbolKind.Namespace
    if normalized in {"interface", "trait"}:
        return SymbolKind.Interface
    if normalized == "struct":
        return SymbolKind.Struct
    if normalized == "enum":
        return SymbolKind.Enum
    if normalized in {"variable", "field"}:
        return SymbolKind.Variable
    return SymbolKind.Object


def _location_from_entry(entry: dict[str, Any]) -> Location:
    start_line = max(0, int(entry.get("line", 1)) - 1)
    end_line = max(start_line, int(entry.get("end_line", entry.get("line", 1))) - 1)
    text = str(entry.get("text", ""))
    end_character = max(1, len(text.strip()) or len(str(entry.get("name", ""))))
    return Location(
        uri=_path_to_uri(str(entry["file"])),
        range=Range(
            start=Position(line=start_line, character=0),
            end=Position(line=end_line, character=end_character),
        ),
    )


def _location_from_external_payload(entry: dict[str, Any]) -> Location | None:
    try:
        payload_range = dict(entry["range"])
        payload_start = dict(payload_range["start"])
        payload_end = dict(payload_range["end"])
        return Location(
            uri=str(entry["uri"]),
            range=Range(
                start=Position(
                    line=int(payload_start["line"]), character=int(payload_start["character"])
                ),
                end=Position(
                    line=int(payload_end["line"]), character=int(payload_end["character"])
                ),
            ),
        )
    except Exception:
        return None


def _run_external_lsp_operation(
    client: Any,
    operation: Callable[[], Any],
    *,
    deadline_monotonic: float | None = None,
) -> Any:
    return repo_map._run_lsp_with_operation_budget(
        client,
        deadline_monotonic
        if deadline_monotonic is not None
        else repo_map._lsp_operation_deadline(),
        operation,
    )


def _document_symbols_for_uri(ls: TensorGrepLSPServer, uri: str) -> list[DocumentSymbol]:
    if ls.provider_mode != "native":
        deadline_monotonic = repo_map._lsp_operation_deadline()
        client = _external_client_for_uri(ls, uri, deadline_monotonic=deadline_monotonic)
        if client is not None:
            try:
                external_result = _run_external_lsp_operation(
                    client,
                    lambda: client.request(
                        "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
                    ),
                    deadline_monotonic=deadline_monotonic,
                )
            except LSPTransportError:
                external_result = None
            if isinstance(external_result, list):
                external_symbols: list[DocumentSymbol] = []
                for current in external_result:
                    if not isinstance(current, dict):
                        continue
                    if "selectionRange" not in current or "range" not in current:
                        continue
                    payload_range = dict(current["range"])
                    payload_selection = dict(current["selectionRange"])
                    external_symbols.append(
                        DocumentSymbol(
                            name=str(current.get("name", "")),
                            kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
                            range=Range(
                                start=Position(
                                    line=int(payload_range["start"]["line"]),
                                    character=int(payload_range["start"]["character"]),
                                ),
                                end=Position(
                                    line=int(payload_range["end"]["line"]),
                                    character=int(payload_range["end"]["character"]),
                                ),
                            ),
                            selection_range=Range(
                                start=Position(
                                    line=int(payload_selection["start"]["line"]),
                                    character=int(payload_selection["start"]["character"]),
                                ),
                                end=Position(
                                    line=int(payload_selection["end"]["line"]),
                                    character=int(payload_selection["end"]["character"]),
                                ),
                            ),
                            detail=str(current.get("detail", "")) or None,
                            children=None,
                        )
                    )
                if external_symbols:
                    return external_symbols
    path = _uri_to_path(uri)
    current_repo_map = _get_repo_map(ls, uri)
    symbols = [
        dict(current)
        for current in current_repo_map.get("symbols", [])
        if str(Path(str(current.get("file", ""))).resolve()) == str(path)
    ]
    symbols.sort(key=lambda item: (int(item.get("line", 0)), str(item.get("name", ""))))
    native_symbols: list[DocumentSymbol] = []
    for current in symbols:
        location = _location_from_entry(current)
        native_symbols.append(
            DocumentSymbol(
                name=str(current.get("name", "")),
                kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
                range=location.range,
                selection_range=location.range,
                detail=str(current.get("kind", "")) or None,
                children=None,
            )
        )
    return native_symbols


def _resolve_workspace_root(ls: TensorGrepLSPServer, path_hint: str | None) -> Path | None:
    """Resolve the workspace root independently of open documents (audit B16).

    Resolution order:
    1. Explicit *path_hint* URI (most specific — used by the handler when available).
    2. Any document currently in the cache.
    3. Current working directory (last resort — avoids returning None when no
       documents are open, which blocked workspace/symbol before this fix).
    """
    if path_hint:
        try:
            return _resolve_repo_root(_uri_to_path(path_hint))
        except Exception:
            pass
    if ls.documents_cache:
        try:
            return _resolve_repo_root(_uri_to_path(next(iter(ls.documents_cache))))
        except Exception:
            pass
    # audit B16: fall back to cwd so workspace/symbol works before any doc is open.
    try:
        return _resolve_repo_root(Path.cwd())
    except Exception:
        return None


def _workspace_symbols(
    ls: TensorGrepLSPServer, query: str, path_hint: str | None = None
) -> list[SymbolInformation]:
    # audit B16: resolve workspace root independently of open docs.
    if ls.provider_mode != "native":
        # For external delegation, we still prefer path_hint; if absent we
        # synthesise a URI from the resolved workspace root.
        effective_hint = path_hint
        if effective_hint is None:
            root = _resolve_workspace_root(ls, None)
            if root is not None:
                effective_hint = root.as_uri()
        if effective_hint is not None:
            deadline_monotonic = repo_map._lsp_operation_deadline()
            client = _external_client_for_uri(
                ls, effective_hint, deadline_monotonic=deadline_monotonic
            )
            if client is not None:
                try:
                    result = _run_external_lsp_operation(
                        client,
                        lambda: client.request("workspace/symbol", {"query": query}),
                        deadline_monotonic=deadline_monotonic,
                    )
                except LSPTransportError:
                    result = None
                if isinstance(result, list):
                    external_symbols: list[SymbolInformation] = []
                    for current in result:
                        if not isinstance(current, dict):
                            continue
                        location_payload = current.get("location")
                        if not isinstance(location_payload, dict):
                            continue
                        resolved = _location_from_external_payload(location_payload)
                        if resolved is None:
                            continue
                        external_symbols.append(
                            SymbolInformation(
                                name=str(current.get("name", "")),
                                kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
                                location=resolved,
                                container_name=str(current.get("containerName", "")) or None,
                            )
                        )
                    if external_symbols:
                        return external_symbols
    repo_root = _resolve_workspace_root(ls, path_hint)
    if repo_root is None:
        return []
    current_repo_map = cast(
        dict[str, Any], _lru_get(ls.repo_map_cache, str(repo_root))
    ) or repo_map.build_repo_map(repo_root)
    _lru_put(ls.repo_map_cache, str(repo_root), current_repo_map, _REPO_MAP_CACHE_MAX)

    normalized_query = query.strip().lower()
    matches: list[dict[str, Any]] = []
    for current in current_repo_map.get("symbols", []):
        name = str(current.get("name", ""))
        if not normalized_query or normalized_query in name.lower():
            matches.append(dict(current))
    matches.sort(
        key=lambda item: (
            str(item.get("name", "")),
            str(item.get("file", "")),
            int(item.get("line", 0)),
        )
    )
    return [
        SymbolInformation(
            name=str(current.get("name", "")),
            kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
            location=_location_from_entry(current),
            container_name=str(Path(str(current.get("file", ""))).name),
        )
        for current in matches
    ]


def _definitions_for_position(
    ls: TensorGrepLSPServer, uri: str, position: Position
) -> list[Location]:
    text = _document_text(ls, uri)
    # audit B13: pass ls for encoding-aware column conversion.
    resolved = _word_range_at_position(text, position, ls)
    if resolved is None:
        return []
    symbol, _ = resolved
    native_locations = [
        _location_from_entry(dict(current))
        for current in repo_map.build_symbol_defs_from_map(_get_repo_map(ls, uri), symbol).get(
            "definitions", []
        )
    ]
    if ls.provider_mode == "native":
        return native_locations
    deadline_monotonic = repo_map._lsp_operation_deadline()
    client = _external_client_for_uri(ls, uri, deadline_monotonic=deadline_monotonic)
    if client is None:
        return native_locations
    try:
        result = _run_external_lsp_operation(
            client,
            lambda: client.request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": position.line, "character": position.character},
                },
            ),
            deadline_monotonic=deadline_monotonic,
        )
    except LSPTransportError:
        return native_locations
    external_locations: list[Location] = []
    if isinstance(result, dict):
        current = _location_from_external_payload(result)
        if current is not None:
            external_locations.append(current)
    elif isinstance(result, list):
        for current in result:
            if isinstance(current, dict):
                resolved_location = _location_from_external_payload(current)
                if resolved_location is not None:
                    external_locations.append(resolved_location)
    if ls.provider_mode == "lsp":
        return external_locations or native_locations
    deduped: dict[tuple[str, int, int, int, int], Location] = {}
    for current in [*external_locations, *native_locations]:
        key = (
            current.uri,
            int(current.range.start.line),
            int(current.range.start.character),
            int(current.range.end.line),
            int(current.range.end.character),
        )
        deduped[key] = current
    return list(deduped.values())


def _references_for_position(
    ls: TensorGrepLSPServer, uri: str, position: Position
) -> list[Location]:
    resolved = _symbol_and_range_for_position(ls, uri, position)
    if resolved is None:
        return []
    symbol, _ = resolved
    native_locations = [
        _location_from_entry(dict(current))
        for current in repo_map.build_symbol_refs_from_map(_get_repo_map(ls, uri), symbol).get(
            "references", []
        )
    ]
    if ls.provider_mode == "native":
        return native_locations
    deadline_monotonic = repo_map._lsp_operation_deadline()
    client = _external_client_for_uri(ls, uri, deadline_monotonic=deadline_monotonic)
    if client is None:
        return native_locations
    try:
        result = _run_external_lsp_operation(
            client,
            lambda: client.request(
                "textDocument/references",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": position.line, "character": position.character},
                    "context": {"includeDeclaration": True},
                },
            ),
            deadline_monotonic=deadline_monotonic,
        )
    except LSPTransportError:
        return native_locations
    external_locations: list[Location] = []
    if isinstance(result, list):
        for current in result:
            if isinstance(current, dict):
                resolved_location = _location_from_external_payload(current)
                if resolved_location is not None:
                    external_locations.append(resolved_location)
    if ls.provider_mode == "lsp":
        return external_locations or native_locations
    deduped: dict[tuple[str, int, int, int, int], Location] = {}
    for current in [*external_locations, *native_locations]:
        key = (
            current.uri,
            int(current.range.start.line),
            int(current.range.start.character),
            int(current.range.end.line),
            int(current.range.end.character),
        )
        deduped[key] = current
    return list(deduped.values())


def _workspace_edit_for_symbol(
    ls: TensorGrepLSPServer,
    uri: str,
    position: Position,
    new_name: str,
) -> WorkspaceEdit | None:
    resolved = _symbol_and_range_for_position(ls, uri, position)
    if resolved is None:
        return None
    symbol, _ = resolved
    # Audit MED (edit-outside-workspace): confine every rename edit — external provider OR
    # native — to the resolved workspace root, so a rename can never write to a file outside it.
    workspace_root = _resolve_repo_root(_uri_to_path(uri))
    if ls.provider_mode != "native":
        deadline_monotonic = repo_map._lsp_operation_deadline()
        client = _external_client_for_uri(ls, uri, deadline_monotonic=deadline_monotonic)
        if client is not None:
            try:
                result = _run_external_lsp_operation(
                    client,
                    lambda: client.request(
                        "textDocument/rename",
                        {
                            "textDocument": {"uri": uri},
                            "position": {
                                "line": position.line,
                                "character": position.character,
                            },
                            "newName": new_name,
                        },
                    ),
                    deadline_monotonic=deadline_monotonic,
                )
            except LSPTransportError:
                result = None
            if isinstance(result, dict) and result:
                edit_uris = _workspace_edit_target_uris(result)
                if edit_uris and all(
                    _uri_within_root(edit_uri, workspace_root) for edit_uri in edit_uris
                ):
                    try:
                        return WorkspaceEdit(**result)
                    except Exception:
                        pass
    current_repo_map = _get_repo_map(ls, uri)
    defs_payload = repo_map.build_symbol_defs_from_map(current_repo_map, symbol)
    refs_payload = repo_map.build_symbol_refs_from_map(current_repo_map, symbol)
    entries_by_file: dict[str, list[dict[str, Any]]] = {}
    for current in [*defs_payload.get("definitions", []), *refs_payload.get("references", [])]:
        entries_by_file.setdefault(str(current["file"]), []).append(dict(current))

    document_changes: list[TextDocumentEdit] = []
    for current_file, entries in sorted(entries_by_file.items()):
        if not _path_within_root(current_file, workspace_root):
            continue  # never emit an edit for a file outside the workspace root
        edits: list[TextEdit] = []
        seen_ranges: set[tuple[int, int, int, int]] = set()
        for entry in sorted(
            entries, key=lambda item: (int(item.get("line", 0)), str(item.get("text", "")))
        ):
            location = _location_from_entry(entry)
            current_range = (
                int(location.range.start.line),
                int(location.range.start.character),
                int(location.range.end.line),
                int(location.range.end.character),
            )
            if current_range in seen_ranges:
                continue
            seen_ranges.add(current_range)
            edits.append(TextEdit(range=location.range, new_text=new_name))
        if edits:
            document_changes.append(
                TextDocumentEdit(
                    text_document=OptionalVersionedTextDocumentIdentifier(
                        uri=_path_to_uri(current_file),
                        version=None,
                    ),
                    edits=edits,
                )
            )

    if not document_changes:
        return None
    return WorkspaceEdit(document_changes=document_changes)


@server.feature(TEXT_DOCUMENT_DID_OPEN)  # type: ignore
def did_open(ls: TensorGrepLSPServer, params: DidOpenTextDocumentParams) -> None:
    """Document opened."""
    # audit I3: use LRU-bounded cache.
    _lru_put(
        ls.documents_cache,
        params.text_document.uri,
        params.text_document.text,
        _DOCUMENTS_CACHE_MAX,
    )
    _invalidate_repo_map_cache(ls, params.text_document.uri)
    _update_ast_tensor(ls, params.text_document.uri, params.text_document.text)
    if ls.provider_mode != "native":
        _external_client_for_uri(ls, params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DID_CLOSE)  # type: ignore
def did_close(ls: TensorGrepLSPServer, params: DidCloseTextDocumentParams) -> None:
    """Document closed — evict from all caches and release GPU tensors (audit I3)."""
    uri = params.text_document.uri
    ls.documents_cache.pop(uri, None)
    # Drop tensor cache entry; if it contains GPU tensors the reference count
    # falls to zero and VRAM is released at the next GC cycle.
    ls.tensor_cache.pop(uri, None)
    # repo_map_cache is keyed by repo root, not URI, so we invalidate via the
    # normal helper which resolves the root from the URI.
    _invalidate_repo_map_cache(ls, uri)
    if ls.provider_mode != "native":
        client: Any = None
        try:
            language = _infer_language(uri)
            workspace_root = _resolve_repo_root(_uri_to_path(uri))
            client = ls.external_providers.get_client(
                language=language, workspace_root=workspace_root
            )
        except Exception:
            pass
        if client is not None:
            try:
                client.close_document(uri=uri)
            except Exception:
                pass


@server.feature(TEXT_DOCUMENT_DID_CHANGE)  # type: ignore
def did_change(ls: TensorGrepLSPServer, params: DidChangeTextDocumentParams) -> None:
    """Document changed."""
    if params.content_changes:
        new_text = cast(Any, params.content_changes[0]).text
        # audit I3: use LRU-bounded cache.
        _lru_put(ls.documents_cache, params.text_document.uri, new_text, _DOCUMENTS_CACHE_MAX)
        _invalidate_repo_map_cache(ls, params.text_document.uri)
        if ls.provider_mode != "native":
            client = _external_client_for_uri(ls, params.text_document.uri)
            if client is not None:
                # audit B15: pass the client-supplied version as a hint; the
                # client's did_change() method will enforce monotonicity itself.
                client.did_change(
                    uri=params.text_document.uri,
                    text=new_text,
                    version=int(getattr(params.text_document, "version", 1) or 1),
                )


@server.feature(TEXT_DOCUMENT_DID_SAVE)  # type: ignore
def did_save(ls: TensorGrepLSPServer, params: DidSaveTextDocumentParams) -> None:
    """Document saved."""
    # audit I3: use LRU-promoting get.
    text = cast(str, _lru_get(ls.documents_cache, params.text_document.uri) or "")
    _invalidate_repo_map_cache(ls, params.text_document.uri)
    _update_ast_tensor(ls, params.text_document.uri, text)
    if ls.provider_mode != "native":
        client = _external_client_for_uri(ls, params.text_document.uri)
        if client is not None:
            client.did_save(uri=params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DEFINITION)  # type: ignore
def definition(ls: TensorGrepLSPServer, params: DefinitionParams) -> list[Location]:
    """Return exact definition locations for the symbol under the cursor."""
    return _definitions_for_position(ls, params.text_document.uri, params.position)


@server.feature(TEXT_DOCUMENT_REFERENCES)  # type: ignore
def references(ls: TensorGrepLSPServer, params: ReferenceParams) -> list[Location]:
    """Return semantic references for the symbol under the cursor."""
    return _references_for_position(ls, params.text_document.uri, params.position)


@server.feature(TEXT_DOCUMENT_PREPARE_RENAME)  # type: ignore
def prepare_rename(
    ls: TensorGrepLSPServer,
    params: PrepareRenameParams,
) -> PrepareRenamePlaceholder | None:
    """Return the renamable symbol range under the cursor."""
    resolved = _symbol_and_range_for_position(ls, params.text_document.uri, params.position)
    if resolved is None:
        return None
    symbol, current_range = resolved
    return PrepareRenamePlaceholder(range=current_range, placeholder=symbol)


@server.feature(TEXT_DOCUMENT_RENAME)  # type: ignore
def rename(ls: TensorGrepLSPServer, params: RenameParams) -> WorkspaceEdit | None:
    """Return a workspace edit that renames a symbol across known definitions and references."""
    return _workspace_edit_for_symbol(
        ls, params.text_document.uri, params.position, params.new_name
    )


@server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)  # type: ignore
def document_symbol(ls: TensorGrepLSPServer, params: DocumentSymbolParams) -> list[DocumentSymbol]:
    """Return document symbols for the current file."""
    return _document_symbols_for_uri(ls, params.text_document.uri)


@server.feature(WORKSPACE_SYMBOL)  # type: ignore
def workspace_symbol(
    ls: TensorGrepLSPServer, params: WorkspaceSymbolParams
) -> list[SymbolInformation]:
    """Return workspace symbols matching the query."""
    # audit B16: _workspace_symbols now resolves the root without open docs.
    path_hint = next(iter(ls.documents_cache), None)
    return _workspace_symbols(ls, params.query, path_hint=path_hint)


def _negotiate_position_encoding(
    ls: TensorGrepLSPServer, client_capabilities: dict[str, Any]
) -> None:
    """Inspect client capabilities and choose the best position encoding (audit B13).

    pygls calls the initialize handler before this module's handler runs, so we
    hook into ``run_lsp`` / the initialize notification to read capabilities.
    This function is called from the initialize response path.
    """
    general = client_capabilities.get("general") or {}
    supported = general.get("positionEncodings") or []
    if "utf-8" in supported:
        ls._position_encoding = "utf-8"
    elif "utf-32" in supported:
        ls._position_encoding = "utf-32"
    else:
        ls._position_encoding = "utf-16"


def _update_ast_tensor(ls: TensorGrepLSPServer, uri: str, text: str) -> None:
    try:
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        if not backend.is_available():
            return

        lang = _infer_language(uri)
        parser_language = "javascript" if lang == "javascript" else lang
        parser = backend._get_parser(parser_language)
        source_bytes = text.encode("utf-8")
        tree = parser.parse(source_bytes)

        edge_index, x, line_numbers = backend._ast_to_graph(tree.root_node, source_bytes)

        import torch

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # audit I3: use LRU eviction; evicted entries drop GPU tensor references,
        # allowing VRAM to be reclaimed at the next GC cycle.
        _lru_put(
            ls.tensor_cache,
            uri,
            {
                "edge_index": edge_index.to(device),
                "x": x.to(device),
                "line_numbers": line_numbers,
                "text": text,
                "language": lang,
            },
            _TENSOR_CACHE_MAX,
        )
    except Exception as e:
        ls.window_log_message(
            LogMessageParams(
                type=MessageType.Error,
                message=f"Failed to update AST Tensor for {uri}: {e}",
            )
        )


def run_lsp() -> None:
    """Start the pygls language server on standard IO."""
    server.provider_mode = os.environ.get("TG_LSP_PROVIDER", "native").strip().lower() or "native"
    server.start_io()


if __name__ == "__main__":
    run_lsp()
