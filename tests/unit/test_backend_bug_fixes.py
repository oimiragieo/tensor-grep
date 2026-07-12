"""
Tests for audit findings B3, B6, O1, D3.

All tests in this file import only lightweight modules (no compiled rust_core, no
CUDA) so they run in the standard CI environment.
"""

from __future__ import annotations

import sys
import types
from typing import Any

# ---------------------------------------------------------------------------
# B3 — AstBackend: no RecursionError on deeply-nested trees
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal tree-sitter node stub."""

    def __init__(self, node_type: str, line: int, children: list[_FakeNode] | None = None):
        self.type = node_type
        self.start_point = (line, 0)
        self.children: list[_FakeNode] = children or []


def _build_chain(depth: int) -> _FakeNode:
    """Return a linearly-chained tree depth nodes deep (worst case for recursion)."""
    node = _FakeNode("leaf", depth)
    for i in range(depth - 1, -1, -1):
        node = _FakeNode("expr", i, [node])
    return node


def test_build_node_type_index_deep_tree_does_not_raise_recursion_error() -> None:
    """_build_node_type_index must survive trees deeper than Python's default recursion limit."""
    from tensor_grep.backends.ast_backend import AstBackend

    backend = AstBackend()
    # Build a chain that would previously blow the 1 000-frame default stack.
    depth = sys.getrecursionlimit() + 500
    root = _build_chain(depth)

    # Must not raise RecursionError
    index = backend._build_node_type_index(root)

    assert "expr" in index
    assert "leaf" in index
    # Every depth level appears as a line number
    assert len(index["expr"]) > 0


def test_build_node_type_index_correct_line_mapping() -> None:
    """Line numbers in the index must match node.start_point[0] + 1."""
    from tensor_grep.backends.ast_backend import AstBackend

    backend = AstBackend()

    # Tree: root (line 0)
    #         ├─ child_a (line 1)
    #         └─ child_b (line 2)
    #               └─ grandchild (line 3)
    root = _FakeNode(
        "root",
        0,
        [
            _FakeNode("child_a", 1),
            _FakeNode("child_b", 2, [_FakeNode("grandchild", 3)]),
        ],
    )
    index = backend._build_node_type_index(root)

    assert index["root"] == [1]
    assert index["child_a"] == [2]
    assert index["child_b"] == [3]
    assert index["grandchild"] == [4]


# ---------------------------------------------------------------------------
# delete-dead-lsp-tensor-gnn: the GNN/tensor path (_ast_to_graph, torch_geometric,
# the LSP tensor cache) was audited as dead -- _ast_to_graph's only caller was the
# LSP's tensor-cache updater, whose output nothing ever read back. Both were
# deleted in the same change that made AstBackend.is_available() tree-sitter-only
# (see test_ast_backend.py). These hygiene assertions pin the deletion so the
# dead path cannot silently grow back.
# ---------------------------------------------------------------------------


def test_ast_to_graph_and_torch_geometric_are_fully_removed() -> None:
    """_ast_to_graph (the dead GNN/tensor-graph conversion helper) must be gone from
    AstBackend, and neither ast_backend.py nor lsp_server.py may import torch_geometric
    anymore -- AstBackend.search() is pure tree-sitter query matching and never touches it.
    """
    from pathlib import Path

    import tensor_grep.backends.ast_backend as ast_backend_module
    from tensor_grep.backends.ast_backend import AstBackend

    assert not hasattr(AstBackend, "_ast_to_graph"), "_ast_to_graph must be deleted (dead GNN path)"

    ast_backend_source = Path(ast_backend_module.__file__).read_text(encoding="utf-8")
    assert "torch_geometric" not in ast_backend_source

    import tensor_grep.cli.lsp_server as lsp_server_module

    lsp_server_source = Path(lsp_server_module.__file__).read_text(encoding="utf-8")
    assert "torch_geometric" not in lsp_server_source
    assert "tensor_cache" not in lsp_server_source, "the dead LSP tensor cache must be removed"
    assert "_update_ast_tensor" not in lsp_server_source


# ---------------------------------------------------------------------------
# D3 — StringZillaBackend: traceback is not swallowed by bare re-raise
# audit #10 (supersedes D3's "propagate the raw type" stance): base.py's Backend
# Fail-Closed Contract explicitly names "encoding/IO errors" as faults backends MUST
# raise as BackendExecutionError instead of letting escape raw -- D3 predates that
# contract clause and left search() with no try/except at all, so an IO fault (like a
# TOCTOU-deleted file) fell into main.py's per-file loop's broad `except Exception`
# and crashed the whole search instead of being retried on the CPU fallback (`except
# BackendExecutionError`). The original exception is NOT swallowed: it is chained via
# `raise ... from e`, so its type and traceback stay inspectable as __cause__.
# ---------------------------------------------------------------------------


def test_stringzilla_search_propagates_real_exception(tmp_path: Any) -> None:
    """An IO fault (missing file) must raise BackendExecutionError, per the Backend
    Fail-Closed Contract (base.py) -- not escape raw as D3 originally required, and not
    be swallowed either: the original FileNotFoundError is preserved as __cause__."""
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.backends.stringzilla_backend import StringZillaBackend
    from tensor_grep.core.config import SearchConfig

    backend = StringZillaBackend()
    missing = str(tmp_path / "does_not_exist.txt")

    try:
        backend.search(missing, "anything", config=SearchConfig(fixed_strings=True))
    except BackendExecutionError as exc:
        assert isinstance(exc.__cause__, FileNotFoundError)  # original type preserved as cause
    except Exception as exc:
        raise AssertionError(
            f"Expected BackendExecutionError (caused by FileNotFoundError) but got "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise AssertionError("Expected BackendExecutionError, but search() returned normally")


def test_stringzilla_search_returns_result_without_wrapper(tmp_path: Any) -> None:
    """After removing the try/except wrapper the happy path must still work."""
    from tensor_grep.backends.stringzilla_backend import StringZillaBackend
    from tensor_grep.core.config import SearchConfig

    backend = StringZillaBackend()
    f = tmp_path / "sample.txt"
    f.write_text("hello world\ngoodbye world\n", encoding="utf-8")

    result = backend.search(str(f), "world", config=SearchConfig(fixed_strings=True))
    assert result.total_matches == 2
    assert result.routing_backend == "StringZillaBackend"


# ---------------------------------------------------------------------------
# O1 — TorchBackend: _batch_match_lines_torch correctness (CPU tensors)
# ---------------------------------------------------------------------------


def _make_cpu_torch_stubs() -> types.ModuleType:
    """Return a minimal torch stub that forwards to the real torch on CPU if available,
    otherwise skip.  We exercise _batch_match_lines_torch logic, not CUDA."""
    try:
        import torch

        return torch
    except ImportError:
        return None  # type: ignore[return-value]


def test_batch_match_lines_torch_finds_pattern() -> None:
    """_batch_match_lines_torch must find pattern bytes in the right rows."""
    torch = _make_cpu_torch_stubs()
    if torch is None:
        import pytest

        pytest.skip("torch not installed")

    from tensor_grep.backends.torch_backend import TorchBackend

    pattern = b"hello"
    pattern_tensor = torch.tensor(list(pattern), dtype=torch.uint8)
    encoded = [
        b"say hello world",  # match
        b"goodbye world",  # no match
        b"hello",  # exact match
        b"hel",  # too short
    ]

    results = TorchBackend._batch_match_lines_torch(
        torch, encoded, pattern_tensor, len(pattern), torch.device("cpu")
    )

    assert results[0] is True
    assert results[1] is False
    assert results[2] is True
    assert results[3] is False


def test_batch_match_lines_torch_empty_input() -> None:
    """Empty encoded_lines must return an empty list without error."""
    torch = _make_cpu_torch_stubs()
    if torch is None:
        import pytest

        pytest.skip("torch not installed")

    from tensor_grep.backends.torch_backend import TorchBackend

    pattern = b"x"
    pattern_tensor = torch.tensor(list(pattern), dtype=torch.uint8)

    results = TorchBackend._batch_match_lines_torch(
        torch, [], pattern_tensor, len(pattern), torch.device("cpu")
    )
    assert results == []


def test_batch_match_lines_torch_all_too_short() -> None:
    """Lines all shorter than pattern must all be False."""
    torch = _make_cpu_torch_stubs()
    if torch is None:
        import pytest

        pytest.skip("torch not installed")

    from tensor_grep.backends.torch_backend import TorchBackend

    pattern = b"toolong"
    pattern_tensor = torch.tensor(list(pattern), dtype=torch.uint8)
    encoded = [b"ab", b"c", b""]

    results = TorchBackend._batch_match_lines_torch(
        torch, encoded, pattern_tensor, len(pattern), torch.device("cpu")
    )
    assert all(r is False for r in results)


# ---------------------------------------------------------------------------
# B6 — CuDFBackend: gc.collect() import is present (no cudf needed)
# ---------------------------------------------------------------------------


def test_cudf_backend_imports_gc() -> None:
    """gc must be imported at module level in cudf_backend so the del+gc.collect() works."""
    import pathlib

    src_root = pathlib.Path(__file__).parent.parent.parent / "src"
    cudf_src = (src_root / "tensor_grep" / "backends" / "cudf_backend.py").read_text(
        encoding="utf-8"
    )
    assert "import gc" in cudf_src, "gc must be imported in cudf_backend.py"
    assert "gc.collect()" in cudf_src, "gc.collect() must be called after chunk cleanup"
    assert "acquire_spill_lock()" not in cudf_src, (
        "bare acquire_spill_lock() call must be removed (audit B6)"
    )
