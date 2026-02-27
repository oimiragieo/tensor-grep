from typing import Any

from lsprotocol.types import (
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
)
from pygls.server import LanguageServer


class TensorGrepLSPServer(LanguageServer):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.documents_cache: dict[str, str] = {}
        # In a real enterprise version, we would keep the AST graph warm in VRAM here.
        self.tensor_cache: dict[str, Any] = {}


server = TensorGrepLSPServer("tensor-grep-lsp", "v0.2.0")


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: TensorGrepLSPServer, params: DidOpenTextDocumentParams) -> None:
    """Document opened."""
    ls.documents_cache[params.text_document.uri] = params.text_document.text
    # Pre-parse into AST tensor and cache in VRAM
    _update_ast_tensor(ls, params.text_document.uri, params.text_document.text)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: TensorGrepLSPServer, params: DidChangeTextDocumentParams) -> None:
    """Document changed."""
    # Simplified change tracking; full sync
    if params.content_changes:
        new_text = params.content_changes[0].text
        ls.documents_cache[params.text_document.uri] = new_text


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: TensorGrepLSPServer, params: DidSaveTextDocumentParams) -> None:
    """Document saved."""
    text = ls.documents_cache.get(params.text_document.uri, "")
    _update_ast_tensor(ls, params.text_document.uri, text)


def _update_ast_tensor(ls: TensorGrepLSPServer, uri: str, text: str) -> None:
    try:
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        if not backend.is_available():
            return

        lang = "python"
        if uri.endswith(".js") or uri.endswith(".ts"):
            lang = "javascript"

        parser = backend._get_parser(lang)
        source_bytes = text.encode("utf-8")
        tree = parser.parse(source_bytes)

        # Keep graph warm in VRAM
        edge_index, x, line_numbers = backend._ast_to_graph(tree.root_node, source_bytes)

        import torch

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        ls.tensor_cache[uri] = {
            "edge_index": edge_index.to(device),
            "x": x.to(device),
            "line_numbers": line_numbers,
            "text": text,
        }
    except Exception as e:
        ls.show_message_log(f"Failed to update AST Tensor for {uri}: {e}")


# Add basic AST grep querying via LSP hover/completion logic if needed later
# For now, keeping the document tensor synchronized via `_update_ast_tensor` covers
# the core architectural goal from the paper: "keep the GNN graph perpetually warm in VRAM".


def run_lsp() -> None:
    """Start the pygls language server on standard IO"""
    server.start_io()


if __name__ == "__main__":
    run_lsp()
