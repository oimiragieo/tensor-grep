"""
Tests for audit findings B3, B6, O1, D3.

All tests in this file import only lightweight modules (no compiled rust_core, no
CUDA) so they run in the standard CI environment.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# B3 — AstBackend: no RecursionError on deeply-nested trees
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal tree-sitter node stub (with the byte span real nodes carry)."""

    def __init__(self, node_type: str, line: int, children: list[_FakeNode] | None = None):
        self.type = node_type
        self.start_point = (line, 0)
        # M16: _build_node_type_index indexes by (line, start_byte, end_byte) span;
        # synthetic nodes must carry a deterministic byte span or they are skipped.
        self.start_byte = line * 4
        self.end_byte = line * 4 + 1
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
    """The line + byte span in the index must match the node's own span (line+1)."""
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

    # M16: NodeSpan = (line, start_byte, end_byte); _FakeNode derives byte spans from line.
    assert index["root"] == [(1, 0, 1)]
    assert index["child_a"] == [(2, 4, 5)]
    assert index["child_b"] == [(3, 8, 9)]
    assert index["grandchild"] == [(4, 12, 13)]


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
# O1 - TorchBackend: _contains_literal_torch correctness (CPU tensors)
#
# These three tests used to call `TorchBackend._batch_match_lines_torch`, which
# has never existed in src/ (`git log -S... -- src/` returns zero commits; the
# name only ever appeared here). They were born dead, and the skip guard hid it:
# it keys on whether `import torch` succeeds, NOT on whether the method exists,
# so every CI job -- none of which installs torch for pytest -- skipped them
# green while any developer box WITH torch failed on AttributeError.
#
# Rewritten against the real matcher, `_contains_literal_torch`, which
# `_search_lines_on_device` drives one line at a time. The original intent
# (right rows match / empty input / all-too-short) carries over unchanged.
# ---------------------------------------------------------------------------


def _cpu_torch_or_none() -> Any:
    """Return the real torch if importable, else None so the caller can skip.

    We exercise `_contains_literal_torch` on a CPU device; no CUDA required.
    """
    try:
        import torch

        return torch
    except ImportError:
        return None


def _match_lines(torch: Any, encoded: list[bytes], pattern: bytes) -> list[bool]:
    """Run the real per-line matcher over `encoded`, mirroring _search_lines_on_device."""
    from tensor_grep.backends.torch_backend import TorchBackend

    backend = TorchBackend()
    pattern_tensor = torch.tensor(list(pattern), dtype=torch.uint8)
    device = torch.device("cpu")
    return [
        backend._contains_literal_torch(
            torch=torch,
            line=raw.decode("utf-8"),
            pattern_tensor=pattern_tensor,
            pattern_len=len(pattern),
            device=device,
        )
        for raw in encoded
    ]


def test_contains_literal_torch_finds_pattern_in_the_right_rows() -> None:
    """The matcher must be True for exactly the rows containing the pattern."""
    torch = _cpu_torch_or_none()
    if torch is None:
        pytest.skip("torch not installed")

    results = _match_lines(
        torch,
        [
            b"say hello world",  # match, mid-line
            b"goodbye world",  # no match
            b"hello",  # match, exact
            b"hel",  # no match, shorter than the pattern
        ],
        b"hello",
    )

    assert results == [True, False, True, False]


def test_contains_literal_torch_empty_input_returns_empty_without_error() -> None:
    """No lines in means no verdicts out -- and no exception."""
    torch = _cpu_torch_or_none()
    if torch is None:
        pytest.skip("torch not installed")

    assert _match_lines(torch, [], b"x") == []


def test_contains_literal_torch_lines_shorter_than_pattern_are_all_false() -> None:
    """Lines shorter than the pattern must be False, not a crash.

    This pins the `len(line_bytes) < pattern_len` guard in
    `_contains_literal_torch`: without it, `line_tensor.unfold(0, pattern_len, 1)`
    raises on a too-short line. Control arm -- delete that guard and this test
    fails; restore it and it passes.
    """
    torch = _cpu_torch_or_none()
    if torch is None:
        pytest.skip("torch not installed")

    results = _match_lines(torch, [b"ab", b"c", b""], b"toolong")

    assert results == [False, False, False]


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
