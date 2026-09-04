"""W1-a: behavioural fail-closed proof for the MCP tool surface's broad exception handlers.

WHY THIS FILE EXISTS
--------------------
``docs/plans/2026-08-20-worldclass-closeout-plan.md`` W1.3 RED-3: a classification of
INTENTIONAL-BOUNDARY is a *claim about behaviour*, so on the network-reachable MCP surface it
gets a behavioural test rather than a reading. Every handler classified INTENTIONAL-BOUNDARY in
``docs/audits/2026-08-20-handler-dispositions.json`` for the four ``cli/mcp_*`` modules is
represented here: the tool's own success-path callee is forced to raise, and the tool must
answer with an EXPLICIT error -- never a clean, empty-but-successful result, which is the exact
shape ``AGENTS.md``'s Backend Fail-Closed Contract forbids, and never a raw exception escaping
across the MCP boundary.

RED PROVENANCE. Each parametrized case was observed RED against a version WITHOUT its guard:
the broad ``except Exception`` arm was deleted from the handler under test and the case failed
with the injected ``RuntimeError`` propagating out of the tool. The mechanical run and its
per-case output are recorded in the PR body. That is the arm that makes this file's green mean
something -- a test that has only ever been seen to pass proves nothing.

THE ORACLE, AND WHY "DID IT ERROR?" IS NOT ONE (A3 round 1, finding 2). Every case runs BOTH
arms and the discriminator is the injected MARKER, never the presence of an error:

  ARM A (no injection)  the marker must appear in NEITHER the wire answer NOR stderr.
  ARM B (injected)      the answer must disclose an error, AND the marker must appear in the
                        wire answer OR stderr.

The measurement that forced this: **15 of the 50 tools return a natural ``error`` on this
fixture** (a session id that does not exist, a manifest that is not there), so the earlier
"assert the payload has an error" oracle was satisfied IDENTICALLY with and without the
injection -- a check that passes in both arms, which is not verification. A further **18 of the
50 sanitize the wire message down to an exception class name** (``_sanitized_tool_error``), so
requiring the marker on the wire alone would have been unsatisfiable for them; ``_log_tool_
exception`` writes the full traceback to stderr, so both channels are searched and finding the
marker in neither is a failure.

``test_control_injection_actually_reaches_the_tool`` additionally pins the simplest case
end-to-end, so a future refactor that made the injection a no-op everywhere still fails loudly.
"""

from __future__ import annotations

import inspect
import json
import sys
import types as _types
from pathlib import Path
from typing import Any

import pytest

# `mcp_server` MUST be loaded BEFORE its three sibling tool modules: each resolves
# `_self = sys.modules["tensor_grep.cli.mcp_server"]` at import time and raises KeyError if it
# is not already in sys.modules. The combined `from ... import a, b, c` line below sorts
# alphabetically and puts `mcp_audit_tools` first, which is a collection ERROR, not a red arm --
# hence this plain `import`, which isort/ruff place in the earlier block by construction.
import tensor_grep.cli.mcp_server  # noqa: F401
from tensor_grep.cli import mcp_audit_tools, mcp_rewrite_tools, mcp_server, mcp_symbol_tools

_MARKER = "W1A_INJECTED_FAILURE"


def _boom(*_args: object, **_kwargs: object) -> Any:
    raise RuntimeError(_MARKER)


@pytest.fixture()
def mcp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, existing directory that every confined tool parameter may point at.

    Set explicitly rather than relying on cwd: ``_confine_mcp_path`` refuses anything outside
    ``_mcp_root()``, and a refusal is an ``invalid_input`` error that would satisfy a naive
    "did it error?" assertion for the WRONG reason.
    """

    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _discloses_error(raw: object, *, case: str) -> None:
    """The tool answered, and its answer DISCLOSES a failure.

    Two disclosure shapes exist on this surface and both are accepted explicitly: a JSON
    envelope carrying a non-empty ``error`` object, or a plain-text sanitized error string. A
    JSON payload with no ``error`` key is a clean success and FAILS -- that is the whole point
    of the arm.
    """

    assert isinstance(raw, str), f"{case}: tool did not return a string ({type(raw)!r})"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        assert "error" in raw.lower(), f"{case}: non-JSON answer discloses nothing: {raw[:400]!r}"
        return
    if not isinstance(payload, dict):
        pytest.fail(f"{case}: JSON answer is not an object: {raw[:400]!r}")
    error = payload.get("error")
    assert error, (
        f"{case}: tool returned a CLEAN SUCCESS while its callee raised -- this is the "
        f"silent-empty-success shape the Backend Fail-Closed Contract forbids. "
        f"payload keys={sorted(payload)}"
    )
    if isinstance(error, dict):
        assert error.get("message") or error.get("code"), (
            f"{case}: error object carries neither message nor code: {error!r}"
        )


# ---------------------------------------------------------------------------
# The RED-3 table: (case id, tool callable, patch target, extra kwargs).
#
# `patch target` is the name the tool's own success path calls INSIDE the guarded ``try:`` --
# derived per handler from the AST (see the PR body's chokepoint table), never guessed.
# Function-local imports are patched at their DEFINING module, because patching the tool
# module would be a silent no-op for those -- the four-shape monkeypatch trap AGENTS.md names.
# ---------------------------------------------------------------------------

_SRV = "tensor_grep.cli.mcp_server"
_SESS = "tensor_grep.cli.session_store"
_RMAP = "tensor_grep.cli.repo_map"
_AUD = "tensor_grep.cli.audit_manifest"
_CKPT = "tensor_grep.cli.checkpoint_store"
_SYM = "tensor_grep.cli.mcp_symbol_tools"

# Cases whose guarded body is unreachable in a bare environment need setup BEFORE the injection,
# or the tool short-circuits and the case silently measures the short-circuit instead of the
# handler. Keyed by case id; each entry is applied with the same monkeypatch as the injection.
#
# `tg_ast_search` is the one that needed it, and CI is what found it: on a runner without
# ast-grep, `Pipeline(ast=True)` raises ConfigurationError and the tool returns a clean
# `code: "unavailable"` envelope from a branch ABOVE the broad handler. The old oracle called
# that a pass (an error came back!); the marker-based oracle correctly called it
# non-discriminating. Locally ast-grep IS present, so this case only ever failed on CI -- the
# environment difference was invisible on this desktop.
_CASE_SETUP: dict[str, str] = {
    "tg_ast_search": "ast-pipeline",
}


class AstBackend:
    """A stand-in whose CLASS NAME is load-bearing.

    `tg_ast_search` gates on `type(backend).__name__ in {"AstBackend", "AstGrepWrapperBackend"}`
    and returns `code: "unavailable"` otherwise -- a second short-circuit above the broad
    handler, which is why borrowing `test_cli_modes._FakeAstPipeline` was not enough.
    """

    def search(self, *_args: object, **_kwargs: object) -> Any:  # pragma: no cover - never reached
        raise AssertionError("the injected failure should fire before any search")


class _AstPipelineStub:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.selected_backend_name = "AstBackend"
        self.selected_backend_reason = "w1a-test-stub"

    def get_backend(self) -> AstBackend:
        return AstBackend()


def _apply_case_setup(case_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if _CASE_SETUP.get(case_id) != "ast-pipeline":
        return
    # `tg_ast_search` constructs the pipeline as `_self.Pipeline(...)`, i.e. the ATTRIBUTE on
    # `cli.mcp_server`, so patching `core.pipeline.Pipeline` is a silent no-op here -- the
    # four-shape monkeypatch trap again. Both names are patched: the module attribute is the one
    # that bites, the source module keeps any other importer consistent.
    monkeypatch.setattr(f"{_SRV}.Pipeline", _AstPipelineStub)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _AstPipelineStub)


def _cases() -> list[tuple[str, Any, str, dict[str, Any]]]:
    return [
        # -- cli/mcp_server.py -------------------------------------------------
        ("tg_repo_map", mcp_server.tg_repo_map, f"{_SRV}.build_repo_map", {}),
        ("tg_orient", mcp_server.tg_orient, f"{_SRV}.build_orient_capsule_json", {}),
        ("tg_doctor", mcp_server.tg_doctor, f"{_SRV}._build_doctor_payload", {}),
        (
            "tg_context_pack",
            mcp_server.tg_context_pack,
            f"{_SRV}.build_context_pack",
            {"query": "sample"},
        ),
        (
            "tg_edit_plan",
            mcp_server.tg_edit_plan,
            f"{_RMAP}.build_context_edit_plan",
            {"query": "sample"},
        ),
        (
            "tg_context_render",
            mcp_server.tg_context_render,
            f"{_SRV}.build_context_render",
            {"query": "sample"},
        ),
        (
            "tg_agent_capsule",
            mcp_server.tg_agent_capsule,
            "tensor_grep.cli.agent_capsule.build_agent_capsule",
            {"query": "sample"},
        ),
        (
            "tg_session_edit_plan",
            mcp_server.tg_session_edit_plan,
            f"{_SESS}.session_context_edit_plan",
            {"session_id": "s-1", "query": "sample"},
        ),
        (
            "tg_session_context_render",
            mcp_server.tg_session_context_render,
            f"{_SESS}.session_context_render",
            {"session_id": "s-1", "query": "sample"},
        ),
        (
            "tg_session_blast_radius",
            mcp_server.tg_session_blast_radius,
            f"{_SESS}.session_blast_radius",
            {"session_id": "s-1", "symbol": "sample"},
        ),
        (
            "tg_session_file_importers",
            mcp_server.tg_session_file_importers,
            f"{_SESS}.session_file_importers",
            {"session_id": "s-1", "file": "sample.py"},
        ),
        (
            "tg_session_blast_radius_render",
            mcp_server.tg_session_blast_radius_render,
            f"{_SESS}.session_blast_radius_render",
            {"session_id": "s-1", "symbol": "sample"},
        ),
        (
            "tg_session_blast_radius_plan",
            mcp_server.tg_session_blast_radius_plan,
            f"{_SESS}.session_blast_radius_plan",
            {"session_id": "s-1", "symbol": "sample"},
        ),
        ("tg_find", mcp_server.tg_find, f"{_SRV}._execute_find", {"query": "sample"}),
        # tg_search's guarded body branches on the selected backend, so the refusal gate is
        # only reached on ONE arm -- patching it left the other arm returning a clean success
        # (observed). `_finalize_aggregate_result` is on BOTH arms, unconditionally, and is
        # therefore the backend-independent chokepoint. tg_ast_search does reach the refusal
        # gate; tg_classify_logs opens its reader inside the try.
        (
            "tg_search",
            mcp_server.tg_search,
            f"{_SRV}._finalize_aggregate_result",
            {"pattern": "sample"},
        ),
        (
            "tg_ast_search",
            mcp_server.tg_ast_search,
            f"{_SRV}._mcp_broad_root_scan_refusal",
            {"pattern": "def $NAME()", "lang": "python"},
        ),
        (
            "tg_classify_logs",
            mcp_server.tg_classify_logs,
            "tensor_grep.io.reader_fallback.FallbackReader",
            {"file_path": "sample.py"},
        ),
        ("tg_session_open", mcp_server.tg_session_open, f"{_SESS}.open_session", {}),
        ("tg_session_list", mcp_server.tg_session_list, f"{_SESS}.list_sessions", {}),
        (
            "tg_session_show",
            mcp_server.tg_session_show,
            f"{_SESS}.get_session",
            {"session_id": "s-1"},
        ),
        (
            "tg_session_refresh",
            mcp_server.tg_session_refresh,
            f"{_SESS}.refresh_session",
            {"session_id": "s-1"},
        ),
        (
            "tg_session_context",
            mcp_server.tg_session_context,
            f"{_SESS}.session_context",
            {"session_id": "s-1", "query": "sample"},
        ),
        # meta-tools: the guarded try dispatches to a sibling tool, so the sibling is the lever
        (
            "tg_navigate",
            mcp_server.tg_navigate,
            f"{_SRV}.tg_symbol_defs",
            {"action": "defs", "symbol": "sample"},
        ),
        (
            "tg_impact",
            mcp_server.tg_impact,
            f"{_SRV}.tg_symbol_impact",
            {"action": "impact", "symbol": "sample"},
        ),
        (
            "tg_query",
            mcp_server.tg_query,
            f"{_SRV}._tg_query_dispatch",
            {"action": "search", "pattern": "sample"},
        ),
        (
            "tg_context",
            mcp_server.tg_context,
            f"{_SRV}.tg_context_pack",
            {"action": "pack", "query": "sample"},
        ),
        ("tg_explore", mcp_server.tg_explore, f"{_SRV}.tg_orient", {"action": "orient"}),
        ("tg_session", mcp_server.tg_session, f"{_SRV}.tg_session_list", {"action": "list"}),
        ("tg_audit", mcp_server.tg_audit, f"{_SRV}.tg_audit_history", {"action": "history"}),
        (
            "tg_checkpoint",
            mcp_server.tg_checkpoint,
            f"{_SRV}.tg_checkpoint_list",
            {"action": "list"},
        ),
        ("tg_scan", mcp_server.tg_scan, f"{_SRV}.tg_rulesets", {"action": "rulesets"}),
        (
            "tg_rewrite",
            mcp_server.tg_rewrite,
            f"{_SRV}.tg_rewrite_diff",
            {"action": "diff", "pattern": "a", "replacement": "b", "lang": "python"},
        ),
        # -- cli/mcp_symbol_tools.py ------------------------------------------
        (
            "tg_symbol_blast_radius_plan",
            mcp_symbol_tools.tg_symbol_blast_radius_plan,
            f"{_RMAP}.build_symbol_blast_radius_plan",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_defs",
            mcp_symbol_tools.tg_symbol_defs,
            f"{_SRV}.build_symbol_defs",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_source",
            mcp_symbol_tools.tg_symbol_source,
            f"{_SRV}.build_symbol_source",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_impact",
            mcp_symbol_tools.tg_symbol_impact,
            f"{_SRV}.build_symbol_impact",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_refs",
            mcp_symbol_tools.tg_symbol_refs,
            f"{_SRV}.build_symbol_refs",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_callers",
            mcp_symbol_tools.tg_symbol_callers,
            f"{_SRV}.build_symbol_callers",
            {"symbol": "sample"},
        ),
        (
            "tg_file_imports",
            mcp_symbol_tools.tg_file_imports,
            f"{_SYM}.build_file_imports",
            {"file": "sample.py"},
        ),
        (
            "tg_file_importers",
            mcp_symbol_tools.tg_file_importers,
            f"{_SRV}.build_file_importers",
            {"file": "sample.py"},
        ),
        (
            "tg_symbol_blast_radius",
            mcp_symbol_tools.tg_symbol_blast_radius,
            f"{_SRV}.build_symbol_blast_radius",
            {"symbol": "sample"},
        ),
        (
            "tg_symbol_blast_radius_render",
            mcp_symbol_tools.tg_symbol_blast_radius_render,
            f"{_SRV}.build_symbol_blast_radius_render",
            {"symbol": "sample"},
        ),
        # -- cli/mcp_audit_tools.py -------------------------------------------
        (
            "tg_audit_manifest_verify",
            mcp_audit_tools.tg_audit_manifest_verify,
            f"{_AUD}.verify_audit_manifest_json",
            {"manifest_path": "sample.py"},
        ),
        (
            "tg_audit_history",
            mcp_audit_tools.tg_audit_history,
            f"{_AUD}.list_audit_history_payload",
            {},
        ),
        (
            "tg_audit_diff",
            mcp_audit_tools.tg_audit_diff,
            f"{_AUD}.diff_audit_manifests_payload",
            {"previous_manifest": "sample.py", "current_manifest": "sample.py"},
        ),
        (
            "tg_review_bundle_create",
            mcp_audit_tools.tg_review_bundle_create,
            f"{_AUD}.create_review_bundle_json",
            {"manifest_path": "sample.py"},
        ),
        (
            "tg_review_bundle_verify",
            mcp_audit_tools.tg_review_bundle_verify,
            f"{_AUD}.verify_review_bundle_json",
            {"bundle_path": "sample.py"},
        ),
        (
            "tg_checkpoint_create",
            mcp_audit_tools.tg_checkpoint_create,
            f"{_CKPT}.create_checkpoint",
            {},
        ),
        (
            "tg_checkpoint_list",
            mcp_audit_tools.tg_checkpoint_list,
            f"{_CKPT}.list_checkpoints",
            {},
        ),
        (
            "tg_checkpoint_undo",
            mcp_audit_tools.tg_checkpoint_undo,
            f"{_CKPT}.undo_checkpoint",
            {"checkpoint_id": "ckpt-0-0"},
        ),
    ]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c[0])
def test_mcp_tool_boundary_is_fail_closed(
    case: tuple[str, Any, str, dict[str, Any]],
    mcp_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name, tool, target, kwargs = case
    call_kwargs = dict(kwargs)
    # Only pass `path` to tools that HAVE one. Passing it blindly raised TypeError on five
    # tools, which is a CRASH, not a red arm (AGENTS.md: "a test that ERRORS is not a red arm")
    # -- and a crash inside the parametrized body would have been indistinguishable from the
    # fail-closed failure this case exists to detect.
    if "path" in inspect.signature(tool).parameters:
        call_kwargs["path"] = str(mcp_root)

    # ---- ARM A: NO INJECTION (the per-case control A3 round 1 required) -------------------
    # Measured, not assumed: 15 of these 50 tools return a natural `error` on this fixture
    # (a session id that does not exist, a manifest that is not there). For those the old
    # "did it error?" oracle was satisfied identically with and without the injection -- a
    # check that passes in both arms. The discriminator is therefore the MARKER, never the
    # mere presence of an error.
    _apply_case_setup(name, monkeypatch)
    natural = tool(**call_kwargs)
    natural_err = capsys.readouterr().err
    assert _MARKER not in natural + natural_err, (
        f"{name}: the injection marker appears WITHOUT the injection -- the control arm is "
        "contaminated and this case proves nothing"
    )

    # ---- ARM B: INJECTED --------------------------------------------------------------
    monkeypatch.setattr(target, _boom, raising=True)
    raw = tool(**call_kwargs)
    injected_err = capsys.readouterr().err

    _discloses_error(raw, case=name)
    # ...and the disclosure must be CAUSED BY the injection. 18 of the 50 tools sanitize the
    # wire message down to an exception class (`_sanitized_tool_error`), so the marker is
    # legitimately absent there -- but `_log_tool_exception` writes the full traceback to
    # stderr, so the marker is observable on one channel or the other for every case. Both are
    # searched, and finding it in neither is a failure.
    assert _MARKER in raw + injected_err, (
        f"{name}: an error came back, but nothing ties it to the injected failure -- it may be "
        f"the SAME natural error the control arm produced. wire={raw[:300]!r} "
        f"stderr={injected_err[:300]!r}"
    )


def test_control_injection_actually_reaches_the_tool(
    mcp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSITIVE CONTROL for the whole table above.

    Without injection ``tg_session_list`` returns a clean payload with NO ``error`` key; with
    the injection it returns one. Same tool, same fixture, one variable moved -- so the
    ``error`` keys the table asserts are caused by the injected failure and not by the
    environment. If this test's first arm ever grows an ``error`` key, every case above is
    passing for the wrong reason and the table is measuring nothing.
    """

    clean = json.loads(mcp_server.tg_session_list(path=str(mcp_root)))
    assert "error" not in clean, f"control arm already errors, table proves nothing: {clean}"

    monkeypatch.setattr(f"{_SESS}.list_sessions", _boom)
    injected = json.loads(mcp_server.tg_session_list(path=str(mcp_root)))
    assert injected.get("error"), f"injection did not reach the tool: {injected}"


# ---------------------------------------------------------------------------
# Handlers on the same surface that are not MCP tool entry points.
# ---------------------------------------------------------------------------


def test_stdin_reader_surfaces_a_malformed_frame_and_stays_usable() -> None:
    """``cli/mcp_server.py::stdin_reader`` handler 0 -- the TRANSPORT boundary.

    A3 round 1 (finding 3) rejected exempting this as "plumbing": it parses UNTRUSTED frames
    straight off the wire, so W1.3's behavioural requirement applies to it like any other
    network-facing boundary. Two properties, and the second is the one that matters:

      1. a malformed JSON frame is SURFACED as an object the session layer can turn into a
         JSON-RPC parse error -- not dropped, not logged-and-forgotten;
      2. the reader REMAINS USABLE afterwards -- the very next well-formed frame is still
         delivered. This is the reason the handler is INTENTIONAL-BOUNDARY rather than a
         defect: raising instead would take the whole stdio transport down with one bad frame
         from any client, which is a denial of service, not fail-closed.

    RED arm: with the broad handler neutralized the malformed frame propagates out of the task
    group and the second (valid) frame is never delivered -- both assertions below fail.
    """

    import io

    import anyio

    valid = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    stdin_bytes = b'{"jsonrpc": "2.0", "id": ' + b"\n" + valid.encode("utf-8") + b"\n"

    received: list[Any] = []

    async def _drive() -> None:
        stdin = anyio.wrap_file(io.BytesIO(stdin_bytes))
        stdout = anyio.wrap_file(io.StringIO())
        async with mcp_server._stdio_server_accepting_content_length(
            stdin=stdin, stdout=stdout
        ) as (read_stream, write_stream):
            for _ in range(2):
                received.append(await read_stream.receive())
            # Both halves must be closed or the context manager's task group waits forever on
            # `stdout_writer`, which never sees EOF. Learned the hard way: the first draft of
            # this test hung for ten minutes on a shared box (anti-hang-test-protocol).
            await write_stream.aclose()
            await read_stream.aclose()

    async def _bounded() -> None:
        # A hard wall-clock bound so a regression that BLOCKS the reader fails as a test
        # failure rather than hanging CI. The budget bounds a hang; it does not measure speed.
        with anyio.fail_after(20):
            await _drive()

    anyio.run(_bounded)

    assert received, "the reader delivered NOTHING -- the malformed frame killed the transport"
    assert isinstance(received[0], Exception), (
        "the malformed frame was swallowed rather than surfaced to the session layer; "
        f"got {received[0]!r}"
    )
    assert len(received) == 2, (
        "the reader did not survive one bad frame -- the next VALID frame never arrived, "
        f"which is the DoS this boundary exists to prevent. received={received!r}"
    )
    assert not isinstance(received[1], Exception), (
        f"the following valid frame was also reported as an error: {received[1]!r}"
    )


def _stub_rust_core(monkeypatch: pytest.MonkeyPatch, *, with_symbols: bool) -> None:
    """Install a fake ``tensor_grep.rust_core``.

    ``with_symbols=False`` omits the two names, so the function-local
    ``from tensor_grep.rust_core import ...`` raises ImportError -- the exact failure the two
    availability handlers exist for, without needing the real extension present or absent.
    """

    fake = _types.ModuleType("tensor_grep.rust_core")
    if with_symbols:
        fake.ast_rewrite_plan_json = _boom  # type: ignore[attr-defined]
        fake.ast_rewrite_apply_json = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tensor_grep.rust_core", fake)


def test_embedded_rewrite_availability_probe_degrades_and_its_consumer_discloses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_embedded_rewrite_available``'s contract IS "False on any failure" (a capability
    probe, INTENTIONAL-BOUNDARY) -- but the CONSUMER of the same import failure must produce a
    disclosed error, not a silent empty success. Both halves are asserted, because a probe
    that degrades correctly while its caller swallows is still a silent failure."""

    _stub_rust_core(monkeypatch, with_symbols=False)
    assert mcp_rewrite_tools._embedded_rewrite_available() is False

    payload = json.loads(
        mcp_rewrite_tools._execute_embedded_rewrite_json(
            pattern="a", replacement="b", lang="python", path=".", mode="plan"
        )
    )
    assert payload["error"]["code"] == "unavailable"
    assert payload["error"]["message"], "unavailable error carries no reason"


def test_embedded_rewrite_engine_failure_is_classified_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second broad handler in ``_execute_embedded_rewrite_json`` (engine raised): the tool
    must return a classified error carrying the engine's exception class, never a clean result,
    and log the full exception to stderr."""

    _stub_rust_core(monkeypatch, with_symbols=True)
    payload = json.loads(
        mcp_rewrite_tools._execute_embedded_rewrite_json(
            pattern="a", replacement="b", lang="python", path=".", mode="plan"
        )
    )
    assert "RuntimeError" in payload["error"]["message"]
    assert payload["error"]["code"]
    captured = capsys.readouterr()
    assert _MARKER in captured.err


def test_embedded_rewrite_unsupported_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Third arm of the same try: an unsupported mode must be refused explicitly rather than
    falling through to the broad handler with a confusing message."""

    _stub_rust_core(monkeypatch, with_symbols=True)
    payload = json.loads(
        mcp_rewrite_tools._execute_embedded_rewrite_json(
            pattern="a", replacement="b", lang="python", path=".", mode="not-a-mode"
        )
    )
    assert payload["error"]["code"] == "unavailable"
    assert "not-a-mode" in payload["error"]["message"]
