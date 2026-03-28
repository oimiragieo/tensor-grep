from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from lsprotocol.types import (
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_REFERENCES,
    WORKSPACE_SYMBOL,
    DefinitionParams,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentSymbol,
    DocumentSymbolParams,
    Location,
    LogMessageParams,
    MessageType,
    Position,
    Range,
    ReferenceParams,
    SymbolInformation,
    SymbolKind,
    WorkspaceSymbolParams,
)
from pygls.lsp.server import LanguageServer

from tensor_grep.cli import repo_map


class TensorGrepLSPServer(LanguageServer):  # type: ignore
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.documents_cache: dict[str, str] = {}
        # In a real enterprise version, we would keep the AST graph warm in VRAM here.
        self.tensor_cache: dict[str, Any] = {}
        self.repo_map_cache: dict[str, dict[str, Any]] = {}


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


def _invalidate_repo_map_cache(ls: TensorGrepLSPServer, uri: str) -> None:
    try:
        repo_root = _resolve_repo_root(_uri_to_path(uri))
    except Exception:
        return
    ls.repo_map_cache.pop(str(repo_root), None)


def _get_repo_map(ls: TensorGrepLSPServer, uri: str) -> dict[str, Any]:
    repo_root = _resolve_repo_root(_uri_to_path(uri))
    cache_key = str(repo_root)
    cached = ls.repo_map_cache.get(cache_key)
    if cached is not None:
        return cached
    current = repo_map.build_repo_map(repo_root)
    ls.repo_map_cache[cache_key] = current
    return current


def _document_text(ls: TensorGrepLSPServer, uri: str) -> str:
    cached = ls.documents_cache.get(uri)
    if cached is not None:
        return cached
    path = _uri_to_path(uri)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        ls.documents_cache[uri] = text
        return text
    return ""


def _infer_language(uri: str) -> str:
    normalized = uri.lower()
    if normalized.endswith(".js") or normalized.endswith(".ts"):
        return "javascript"
    if normalized.endswith(".rs"):
        return "rust"
    return "python"


def _word_range_at_position(text: str, position: Position) -> tuple[str, Range] | None:
    lines = text.splitlines()
    if position.line < 0 or position.line >= len(lines):
        return None
    line = lines[position.line]
    if not line:
        return None
    character = max(0, min(int(position.character), len(line)))
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
    return (
        symbol,
        Range(
            start=Position(line=position.line, character=start),
            end=Position(line=position.line, character=end),
        ),
    )


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


def _document_symbols_for_uri(ls: TensorGrepLSPServer, uri: str) -> list[DocumentSymbol]:
    path = _uri_to_path(uri)
    current_repo_map = _get_repo_map(ls, uri)
    symbols = [
        dict(current)
        for current in current_repo_map.get("symbols", [])
        if str(Path(str(current.get("file", ""))).resolve()) == str(path)
    ]
    symbols.sort(key=lambda item: (int(item.get("line", 0)), str(item.get("name", ""))))
    result: list[DocumentSymbol] = []
    for current in symbols:
        location = _location_from_entry(current)
        result.append(
            DocumentSymbol(
                name=str(current.get("name", "")),
                kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
                range=location.range,
                selection_range=location.range,
                detail=str(current.get("kind", "")) or None,
                children=None,
            )
        )
    return result


def _workspace_symbols(ls: TensorGrepLSPServer, query: str, path_hint: str | None = None) -> list[SymbolInformation]:
    repo_root = None
    if path_hint:
        repo_root = _resolve_repo_root(_uri_to_path(path_hint))
    elif ls.documents_cache:
        repo_root = _resolve_repo_root(_uri_to_path(next(iter(ls.documents_cache))))
    if repo_root is None:
        return []
    current_repo_map = ls.repo_map_cache.get(str(repo_root)) or repo_map.build_repo_map(repo_root)
    ls.repo_map_cache[str(repo_root)] = current_repo_map

    normalized_query = query.strip().lower()
    matches: list[dict[str, Any]] = []
    for current in current_repo_map.get("symbols", []):
        name = str(current.get("name", ""))
        if not normalized_query or normalized_query in name.lower():
            matches.append(dict(current))
    matches.sort(key=lambda item: (str(item.get("name", "")), str(item.get("file", "")), int(item.get("line", 0))))
    return [
        SymbolInformation(
            name=str(current.get("name", "")),
            kind=_kind_to_symbol_kind(str(current.get("kind", "symbol"))),
            location=_location_from_entry(current),
            container_name=str(Path(str(current.get("file", ""))).name),
        )
        for current in matches
    ]


def _definitions_for_position(ls: TensorGrepLSPServer, uri: str, position: Position) -> list[Location]:
    text = _document_text(ls, uri)
    resolved = _word_range_at_position(text, position)
    if resolved is None:
        return []
    symbol, _ = resolved
    payload = repo_map.build_symbol_defs_from_map(_get_repo_map(ls, uri), symbol)
    return [_location_from_entry(dict(current)) for current in payload.get("definitions", [])]


def _references_for_position(ls: TensorGrepLSPServer, uri: str, position: Position) -> list[Location]:
    text = _document_text(ls, uri)
    resolved = _word_range_at_position(text, position)
    if resolved is None:
        return []
    symbol, _ = resolved
    payload = repo_map.build_symbol_refs_from_map(_get_repo_map(ls, uri), symbol)
    return [_location_from_entry(dict(current)) for current in payload.get("references", [])]


@server.feature(TEXT_DOCUMENT_DID_OPEN)  # type: ignore
def did_open(ls: TensorGrepLSPServer, params: DidOpenTextDocumentParams) -> None:
    """Document opened."""
    ls.documents_cache[params.text_document.uri] = params.text_document.text
    _invalidate_repo_map_cache(ls, params.text_document.uri)
    _update_ast_tensor(ls, params.text_document.uri, params.text_document.text)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)  # type: ignore
def did_change(ls: TensorGrepLSPServer, params: DidChangeTextDocumentParams) -> None:
    """Document changed."""
    if params.content_changes:
        new_text = cast(Any, params.content_changes[0]).text
        ls.documents_cache[params.text_document.uri] = new_text
        _invalidate_repo_map_cache(ls, params.text_document.uri)


@server.feature(TEXT_DOCUMENT_DID_SAVE)  # type: ignore
def did_save(ls: TensorGrepLSPServer, params: DidSaveTextDocumentParams) -> None:
    """Document saved."""
    text = ls.documents_cache.get(params.text_document.uri, "")
    _invalidate_repo_map_cache(ls, params.text_document.uri)
    _update_ast_tensor(ls, params.text_document.uri, text)


@server.feature(TEXT_DOCUMENT_DEFINITION)  # type: ignore
def definition(ls: TensorGrepLSPServer, params: DefinitionParams) -> list[Location]:
    """Return exact definition locations for the symbol under the cursor."""
    return _definitions_for_position(ls, params.text_document.uri, params.position)


@server.feature(TEXT_DOCUMENT_REFERENCES)  # type: ignore
def references(ls: TensorGrepLSPServer, params: ReferenceParams) -> list[Location]:
    """Return semantic references for the symbol under the cursor."""
    return _references_for_position(ls, params.text_document.uri, params.position)


@server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)  # type: ignore
def document_symbol(ls: TensorGrepLSPServer, params: DocumentSymbolParams) -> list[DocumentSymbol]:
    """Return document symbols for the current file."""
    return _document_symbols_for_uri(ls, params.text_document.uri)


@server.feature(WORKSPACE_SYMBOL)  # type: ignore
def workspace_symbol(ls: TensorGrepLSPServer, params: WorkspaceSymbolParams) -> list[SymbolInformation]:
    """Return workspace symbols matching the query."""
    path_hint = next(iter(ls.documents_cache), None)
    return _workspace_symbols(ls, params.query, path_hint=path_hint)


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

        ls.tensor_cache[uri] = {
            "edge_index": edge_index.to(device),
            "x": x.to(device),
            "line_numbers": line_numbers,
            "text": text,
            "language": lang,
        }
    except Exception as e:
        ls.window_log_message(
            LogMessageParams(
                type=MessageType.Error,
                message=f"Failed to update AST Tensor for {uri}: {e}",
            )
        )


def run_lsp() -> None:
    """Start the pygls language server on standard IO."""
    server.start_io()


if __name__ == "__main__":
    run_lsp()
