"""`tg_find` MCP tool (Wave 2d, #189) -- the agent-callable form of `tg find`.

This is a NEW LLM-facing param surface (the highest-risk surface in the whole `tg find` plan),
so its test coverage is deliberately security-first: the fix-approach council's must-fixes are
each pinned by a dedicated test --

- S1: the scan-root `path` is confined via `_confine_mcp_path` as the VERY FIRST operation,
  before any walk root is derived from it (mirrors tg_file_importers's `path` confinement,
  round-8/audit #95 -- NOT tg_file_imports's `file` confinement, since `path` here IS the
  primary/only path param).
- S2: the error-sanitization split is preserved -- only the generic `except Exception` branch
  routes through `_sanitized_tool_error`; the confined-path/FileNotFoundError branches
  deliberately echo the within-root path.
- A4: `docs/harness_api.md` documents the new tool (enforced by
  test_harness_api_doc_lists_every_registered_mcp_tool_name in test_harness_api_docs.py).
- max_tokens: the MCP default is pinned equal to the CLI's `tg find --max-tokens` default.

Mirrors the CliRunner-based dense-leg isolation pattern in test_find_command.py -- every test
that reaches the ranking pipeline monkeypatches the dense-leg availability probe to a known
state rather than depending on whatever the real environment happens to have installed.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import mcp_server
from tensor_grep.cli import repo_map as repo_map_module


def _stub_dense_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: model2vec not installed -- test stub"),
    )


def _write_invoice_corpus(root: Path) -> None:
    (root / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------------------------
# S1 -- confinement is the FIRST operation, mirroring tg_file_importers's `path` param.
# ---------------------------------------------------------------------------------------------


def test_tg_find_path_traversal_rejected(tmp_path, monkeypatch):
    """../ escape, an absolute-outside path, and a symlink-outside path must all be refused by
    the confinement check, fail-closed, with NO read of the corpus outside the root ever
    happening (the pipeline must not even be reached)."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_invoice_corpus(outside)

    # --- absolute-outside
    out_abs = mcp_server.tg_find("invoice", path=str(outside))
    payload_abs = json.loads(out_abs)
    assert payload_abs["error"]["code"] == "invalid_input"
    assert "must stay within" in payload_abs["error"]["message"]
    assert payload_abs.get("matches") is None
    assert payload_abs.get("total_matches") is None

    # --- ../ escape
    out_dotdot = mcp_server.tg_find("invoice", path="../outside")
    payload_dotdot = json.loads(out_dotdot)
    assert payload_dotdot["error"]["code"] == "invalid_input"
    assert "must stay within" in payload_dotdot["error"]["message"]

    # --- symlink-outside: a symlink planted INSIDE the (confined) project root that resolves to
    # a target OUTSIDE it must still be refused -- `_confine_mcp_path`'s `.resolve()` follows
    # symlinks precisely so this case is caught.
    link = project / "escape_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out_symlink = mcp_server.tg_find("invoice", path=str(link))
    payload_symlink = json.loads(out_symlink)
    assert payload_symlink["error"]["code"] == "invalid_input"
    assert "must stay within" in payload_symlink["error"]["message"]


def test_tg_find_confines_root_before_walk(tmp_path, monkeypatch):
    """S1: the repo walk must receive the CONFINED (absolute, resolved) path, not the raw
    caller-supplied one -- proves confinement runs, and its result is forwarded, before
    `_execute_find` derives its whole-repo walk root."""
    monkeypatch.chdir(tmp_path)
    _write_invoice_corpus(tmp_path)
    _stub_dense_unavailable(monkeypatch)

    real_iter_repo_files = repo_map_module._iter_repo_files
    captured_roots = []

    def _spy_iter_repo_files(root, **kwargs):
        captured_roots.append(root)
        return real_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map_module, "_iter_repo_files", _spy_iter_repo_files)

    out = mcp_server.tg_find("invoice", path=".")

    assert captured_roots, "the repo walk was never invoked"
    assert captured_roots[0] == tmp_path.resolve(), (
        "the walk root must be the CONFINED absolute path, not the raw '.' the caller passed"
    )
    payload = json.loads(out)
    assert payload.get("error") is None
    assert payload["total_matches"] >= 1


def test_tg_find_accepts_in_root_path(tmp_path, monkeypatch):
    """Positive-path regression guard (Opus adversarial gate precedent, audit #81 fix #2):
    confining `path` must not break a legitimate in-root call."""
    monkeypatch.chdir(tmp_path)
    _write_invoice_corpus(tmp_path)
    _stub_dense_unavailable(monkeypatch)

    out = mcp_server.tg_find("invoice", path=str(tmp_path))

    payload = json.loads(out)
    assert payload.get("error") is None
    assert payload["total_matches"] >= 1
    assert payload["path"] == str(tmp_path.resolve())


# ---------------------------------------------------------------------------------------------
# S2 -- error-sanitization split: only the generic Exception branch is sanitized.
# ---------------------------------------------------------------------------------------------


def test_tg_find_error_is_sanitized(tmp_path, monkeypatch):
    """A generic (non-FileNotFoundError, non-BackendExecutionError) fault deep in the ranking
    pipeline must come back as a sanitized `internal_error` -- no raw exception text, no
    traceback, no internal detail -- while the confined-path `invalid_input` branch continues to
    echo the within-root path (the split S2 requires preserving)."""
    monkeypatch.chdir(tmp_path)
    _write_invoice_corpus(tmp_path)
    _stub_dense_unavailable(monkeypatch)

    leak_marker = "LEAK_MARKER_internal_detail_should_never_reach_the_client"

    def _raise_generic(*_args, **_kwargs):
        raise RuntimeError(f"{leak_marker}: /some/internal/absolute/path")

    monkeypatch.setattr("tensor_grep.core.retrieval_bm25.Bm25Index", _raise_generic)

    out = mcp_server.tg_find("invoice", path=str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert leak_marker not in out, "raw exception text leaked to the MCP client"
    assert "/some/internal/absolute/path" not in out
    assert "RuntimeError" in payload["error"]["message"]
    assert payload["error"]["retryable"] is False

    # --- contrast: the invalid_input (confinement) branch DELIBERATELY keeps its raw echo.
    outside = tmp_path.parent / "outside_for_contrast"
    outside.mkdir(exist_ok=True)
    rejected_out = mcp_server.tg_find("invoice", path=str(outside))
    rejected_payload = json.loads(rejected_out)
    assert rejected_payload["error"]["code"] == "invalid_input"
    assert "must stay within" in rejected_payload["error"]["message"]


def test_tg_find_backend_execution_error_is_distinguishable_not_a_traceback(tmp_path, monkeypatch):
    """C1 mirror: a genuine backend fault (e.g. a corrupt dense model directory) must propagate
    out of `_execute_find` and come back as a clean, distinguishable `find_backend_error` --
    never a raw traceback, and never silently swallowed as an ordinary `internal_error`."""
    from tensor_grep.backends.base import BackendExecutionError

    monkeypatch.chdir(tmp_path)
    _write_invoice_corpus(tmp_path)
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raise_corrupt(_dir):
        raise BackendExecutionError(
            "dense model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", _raise_corrupt)

    out = mcp_server.tg_find("invoice", path=str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "find_backend_error"
    assert "boom" in payload["error"]["message"]
    assert payload["error"]["retryable"] is False


def test_tg_find_missing_path_is_invalid_input_with_within_root_echo(tmp_path, monkeypatch):
    """An in-root but nonexistent path passes confinement (confinement is pure path resolution,
    never an existence check) and is refused downstream by `_execute_find`'s own
    FileNotFoundError -- that branch also deliberately echoes the within-root path (S2)."""
    monkeypatch.chdir(tmp_path)

    out = mcp_server.tg_find("invoice", path="does-not-exist")

    payload = json.loads(out)
    assert payload["error"]["code"] == "invalid_input"
    assert "Path not found" in payload["error"]["message"]
    expected_missing_path = (tmp_path / "does-not-exist").resolve()
    assert str(expected_missing_path) in payload["error"]["message"]


# ---------------------------------------------------------------------------------------------
# Fail-closed ranking behavior (D3), reusing the shared `_execute_find` compute.
# ---------------------------------------------------------------------------------------------


def test_tg_find_bm25_only_degrade_sets_rank_fallback_reason(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_invoice_corpus(tmp_path)
    _stub_dense_unavailable(monkeypatch)

    out = mcp_server.tg_find("invoice", path=str(tmp_path))

    payload = json.loads(out)
    assert payload.get("error") is None
    assert payload.get("rank_fallback_reason") is not None
    assert "model2vec" in payload["rank_fallback_reason"]
    assert payload["total_matches"] >= 1


def test_tg_find_result_incomplete_surfaced(tmp_path, monkeypatch):
    """C2: a --max-repo-files cap trip means the ranked corpus was PARTIAL (no regex pre-filter
    narrowed it first) -- must surface as result_incomplete=true + incomplete_reason, mirroring
    the CLI's exit-2 case (MCP has no exit codes, so the JSON field is the whole signal)."""
    monkeypatch.chdir(tmp_path)
    _stub_dense_unavailable(monkeypatch)
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"def fn_{i}():\n    return {i}\n", encoding="utf-8")

    out = mcp_server.tg_find("fn", path=str(tmp_path), max_repo_files=2)

    payload = json.loads(out)
    assert payload.get("result_incomplete") is True
    assert "max-repo-files" in (payload.get("incomplete_reason") or "")


def test_tg_find_no_results_is_a_valid_empty_response(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "a.py").write_text("def completely_unrelated():\n    return 1\n", encoding="utf-8")

    out = mcp_server.tg_find("zzqzxvvvqqqnonexistentgibberish", path=str(tmp_path))

    payload = json.loads(out)
    assert payload.get("error") is None
    assert payload["total_matches"] == 0
    assert not payload.get("result_incomplete")


# ---------------------------------------------------------------------------------------------
# Bounded output (D4) + response shape.
# ---------------------------------------------------------------------------------------------


def test_tg_find_output_bounded(tmp_path, monkeypatch):
    """max_tokens bounds the result set, dropping the LOWEST-ranked matches first (never the top
    match), mirroring test_find_command.py's CLI-level test_find_budget_truncates_lowest_ranked_first."""
    monkeypatch.chdir(tmp_path)
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "strong.py").write_text(
        "def invoice_invoice_invoice():\n    invoice = 1\n    return invoice\n", encoding="utf-8"
    )
    (tmp_path / "medium.py").write_text(
        "def process(invoice):\n    return invoice\n", encoding="utf-8"
    )
    (tmp_path / "weak.py").write_text(
        "# an invoice is mentioned here only once\nx = 1\n", encoding="utf-8"
    )

    unbounded = json.loads(mcp_server.tg_find("invoice", path=str(tmp_path), max_tokens=0))
    assert unbounded.get("error") is None
    unbounded_files = [m["file"] for m in unbounded["matches"]]
    assert len(unbounded_files) >= 2, "expected the corpus to yield more than one ranked match"

    budgeted = json.loads(mcp_server.tg_find("invoice", path=str(tmp_path), max_tokens=1))
    assert budgeted.get("error") is None
    budgeted_files = [m["file"] for m in budgeted["matches"]]

    assert len(budgeted_files) == 1, "a 1-token budget must floor at exactly the top match"
    assert budgeted_files[0] == unbounded_files[0], (
        "the budget must drop the LOWEST-ranked matches first, keeping the top match"
    )


def test_tg_find_limit_caps_ranked_chunks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _stub_dense_unavailable(monkeypatch)
    for i in range(5):
        (tmp_path / f"invoice_{i}.py").write_text(
            f"def make_invoice_{i}(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
            encoding="utf-8",
        )

    out = json.loads(mcp_server.tg_find("invoice", path=str(tmp_path), limit=2, max_tokens=0))

    assert out.get("error") is None
    assert len(out["matches"]) <= 2


def test_tg_find_response_shape_and_ascii(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _stub_dense_unavailable(monkeypatch)
    _write_invoice_corpus(tmp_path)

    out = mcp_server.tg_find("invoice", path=str(tmp_path))

    assert out.isascii()
    payload = json.loads(out)
    assert payload["query"] == "invoice"
    assert payload["path"] == str(tmp_path.resolve())
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert isinstance(payload["matches"], list)
    match = payload["matches"][0]
    assert match["file"].endswith("invoice.py")
    assert isinstance(match["line_number"], int) and match["line_number"] >= 1
    assert isinstance(match["text"], str)


# ---------------------------------------------------------------------------------------------
# max_tokens unification guard.
# ---------------------------------------------------------------------------------------------


def test_tg_find_max_tokens_default_matches_cli():
    """Guard test (fix-approach council must-fix): the MCP tool's `max_tokens` default must
    equal the CLI's `tg find --max-tokens` default, mirroring test_inventory.py's
    test_cli_default_max_files_matches_module_constant introspection pattern."""
    cli_default = inspect.signature(cli_main.find).parameters["max_tokens"].default
    # typer.Option returns an OptionInfo whose .default holds the literal value.
    assert cli_default.default == mcp_server._DEFAULT_MCP_FIND_MAX_TOKENS == 4000

    mcp_default = inspect.signature(mcp_server.tg_find).parameters["max_tokens"].default
    assert mcp_default == mcp_server._DEFAULT_MCP_FIND_MAX_TOKENS
