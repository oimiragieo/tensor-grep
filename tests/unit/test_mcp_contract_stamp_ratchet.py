"""M14: mcp_contract_version stamping uniformity + VALUE ratchet.

The central const ``_TG_MCP_SERVER_CONTRACT_VERSION`` (mcp_server.py) is the single
source of truth for the MCP wire contract version. M14 closes two escapes:

1. ``_inject_mcp_contract_fields`` used ``setdefault`` for ``mcp_contract_version``:
   a tool payload carrying its OWN top-level literal (a stale hardcode or a forked
   value) WON and skirted the central const. The injector now HARD-assigns the
   central const -- it ALWAYS wins (A49: the old tool-literal-wins behavior is
   retired; a forked literal defeats the point of a single source of truth).
   ``schema_version`` deliberately stays ``setdefault`` (F2-corrected): it is the
   JSON-output version only by default, and a tool with a DOCUMENTED domain meaning
   for that exact key keeps it (``tg_doctor`` documents top-level ``schema_version:
   2`` as the doctor JSON schema version; clobbering it would break harness_api.md
   consumers).
2. Raw ``json.dumps(...)`` return sites crossed the wire unstamped. The live census
   found 19 unstamped success/error sites across 15 tools (tg_search, tg_ast_search,
   tg_classify_logs, tg_devices, tg_query, tg_ruleset_scan, tg_scan,
   tg_symbol_blast_radius_plan, tg_audit_manifest_verify, tg_review_bundle_create,
   tg_review_bundle_verify, tg_session_refresh, tg_session_blast_radius,
   tg_session_blast_radius_plan, tg_session_blast_radius_render,
   tg_session_context_render, tg_session_edit_plan, tg_session_file_importers, plus
   the shared ``_broad_root_scan_refusal_result`` helper); every site now routes
   through the ``_inject_mcp_contract_fields`` chokepoint.

The ratchet (F3-strengthened): assert EVERY registered tool's JSON response carries
``mcp_contract_version == _TG_MCP_SERVER_CONTRACT_VERSION`` BY VALUE. The tool set
is derived LIVE from the server registry (``mcp.list_tools()``), never a
hand-written list. Per tool the census drives:

- a SUCCESS-family probe (existing closed-world reach map -- reused, not duplicated
  -- with deterministic hermetic fixtures for the file/manifest/bundle/session/
  checkpoint-gated tools),
- a SCHEMA-derived minimal-required probe (mechanical; a NEW tool is always
  exercised before anyone curates a reach entry),
- an ERROR-family probe for every non-exempt string path param (out-of-root
  confinement refusal, mirroring the confinement ratchet's enumeration).

Every observed JSON response must carry the const BY VALUE -- an unstamped SUCCESS
hidden behind a stamped error fails exactly like an unstamped error. A tool that
RAISES is a FAILURE unless its (tool, family) probe is on the strict allowlist AND
the raised exception is one of that entry's EXPECTED dependency-absent types (F1:
any other exception type -- e.g. a mutation raising AssertionError -- is a
violation); a non-string return is ALWAYS a violation, never excused by the raise
allowlist. The census force-disables the `tg find` dense leg for its duration (F2),
so tg_find's success reach is identical whether the ambient dense model is absent,
installed, or corrupt; and every allowlist/fixture (tool, family) key is validated
against the generated probe-family set (F3). A NEW tool added later without the
stamp fails this census.

ENVIRONMENT MATRIX (CI-round fix): the census must produce the SAME verdict on the
developer desktop and the CI pytest env. Two engine classes differ between them:

- tg_find's DENSE leg (model2vec present/absent/corrupt): force-disabled for the
  census duration -- the deterministic BM25-only fallback success arm fires
  everywhere (see _force_dense_unavailable).
- the AST tools (tg_ast_search, tg_ruleset_scan, tg_scan): their success arms
  require a real engine (tree-sitter grammar via the native-shaped AstBackend path,
  or the ast-grep wrapper binary) that the CI pytest env does NOT install; on CI the
  real probes can only raise an absent-dep exception or return the 'unavailable'
  envelope, so SUCCESS reach is impossible. The census therefore drives those
  (tool, family) probes through a CONTROLLED engine seam (_controlled_ast_engine:
  Pipeline.get_backend -> a fixed 'AstBackend' stub, _run_ast_scan_payload -> a
  deterministic empty-findings payload), so the tool's REAL success return site
  (the _inject_mcp_contract_fields envelope) is exercised and value-checked on both
  envs. Which arm proves the stamp per tool: SUCCESS arm (controlled engine) +
  ERROR arm (real out-of-root confinement refusals, always engine-free) on BOTH
  envs. The real-engine success paths themselves are covered by the repo's own AST
  unit tests (test_mcp_server.py::test_tg_ast_search_* et al.); the census's job is
  stamping, not engine correctness.
"""

import asyncio
import contextlib
import importlib.util
import json
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult

# The confinement ratchet's closed-world per-tool reach map, REUSED not duplicated
# (A22): pytest runs with --import-mode=importlib (pyproject.toml), so a plain cross-
# test-module import cannot resolve; load the sibling file by path instead. The one
# authoritative copy stays in test_mcp_server.py; this is only a reader of it.
_REACH_MAP_SPEC = importlib.util.spec_from_file_location(
    "_m14_reach_map_source", Path(__file__).with_name("test_mcp_server.py")
)
assert _REACH_MAP_SPEC.loader is not None
_REACH_MAP_MODULE = importlib.util.module_from_spec(_REACH_MAP_SPEC)
_REACH_MAP_SPEC.loader.exec_module(_REACH_MAP_MODULE)
_RATCHET_BASE_KWARGS: dict[str, dict[str, object]] = _REACH_MAP_MODULE._RATCHET_BASE_KWARGS
_CONFINEMENT_RATCHET_CASES: list[tuple[str, str]] = _REACH_MAP_MODULE._CONFINEMENT_RATCHET_CASES

# ---------------------------------------------------------------------------
# Strict-outcome allowlists (F3). Every entry is a (tool_name, probe-family) key
# with an explicit reason. A probe outcome NOT listed here is a census FAILURE.
# ---------------------------------------------------------------------------

# Tool probes that raise when the OPTIONAL ast-grep/tree-sitter deps are absent on a
# minimal runner (documented in the confinement ratchet's own notes: "Linux CI
# without ast-grep ... raises a wrapped ToolError BEFORE it would run"). These
# families normally never raise inside the census anymore -- the census drives them
# through the CONTROLLED ast engine seam (see _controlled_ast_engine), so they
# return stamped success JSON on every env. The allowlist is the backstop for any
# probe path that bypasses the shim (or a future refactor that removes it): it
# excuses ONLY the absent-dep raise. tg_scan's curated family delegates to
# tg_ruleset_scan.
#
# F1-tightened: each entry names the EXPECTED exception types (the types the code
# demonstrably raises for absent optional deps -- ConfigurationError from the
# Pipeline ast-backend guard, or a raw ImportError/ModuleNotFoundError from an
# eagerly imported optional package). ANY other exception type on those probe
# families is a census violation -- an allowlist entry cannot be used to mask a
# regression that happens to raise on a deprecated family.
_RAISE_ALLOWLIST: dict[tuple[str, str], tuple[tuple[type[Exception], ...], str]] = {
    ("tg_ast_search", "curated"): (
        (ConfigurationError, ImportError, ModuleNotFoundError),
        "ast-grep/tree-sitter optional deps absent on minimal runners -> Pipeline(ast=True) "
        "raises before any response (see confinement ratchet notes); its 'unavailable' JSON "
        "arm is value-pinned and stamped when deps present.",
    ),
    ("tg_ast_search", "schema"): (
        (ConfigurationError, ImportError, ModuleNotFoundError),
        "same optional-dep raise via the schema-derived probe.",
    ),
    ("tg_ruleset_scan", "curated"): (
        (ConfigurationError, ImportError, ModuleNotFoundError),
        "ruleset scan is ast-backed; same absent-dep raise.",
    ),
    ("tg_scan", "curated"): (
        (ConfigurationError, ImportError, ModuleNotFoundError),
        "delegates to tg_ruleset_scan; inherits the same absent-dep raise.",
    ),
}

# Tool probes that LEGITIMATELY return plain text (no JSON envelope exists to stamp).
_TEXT_ALLOWLIST: dict[tuple[str, str], str] = {
    ("tg_search", "schema"): "tg_search declares NO required params, so the schema probe "
    "sends {}; with no pattern/query it returns the plain-text 'Search failed: either "
    "pattern or query is required.' refusal before any JSON branch runs. Its JSON arms "
    "are covered by the curated probe and by M14's tg_search value pins.",
}

# Tools whose SUCCESS family requires a compiled native extension / native tg binary
# ahead of PATH -- deterministically unreachable on a worktree-less venv, reachable on
# CI. The census still value-checks their responses whenever a success happens to be
# observed; the gate only waives the "success family must be observed" requirement.
# Their success envelopes embed the const by construction (_rewrite_envelope /
# _index_search_envelope -> _envelope_base), pinned by test_mcp_plan_bound_apply's
# envelope-builder value tests.
_SUCCESS_UNREACHABLE: dict[str, str] = {
    "tg_rewrite_plan": "success requires the embedded-rewrite rust ext (absent in "
    "worktree-isolated venvs); envelope const pinned by test_mcp_plan_bound_apply.",
    "tg_rewrite_apply": "same native-ext gate as tg_rewrite_plan.",
    "tg_rewrite_diff": "same native-ext gate as tg_rewrite_plan.",
    "tg_rewrite": "meta-composes the rewrite tools; same native-ext gate.",
    "tg_index_search": "success requires a standalone native tg on PATH (resolution "
    "varies by venue); envelope const pinned via _index_search_envelope tests.",
}

# Tools whose SUCCESS family is gated on hermetic fixtures the census materializes
# (file / manifest / bundle / session / checkpoint). The fixture setup runs once per
# census; the success probe then uses the curated reach args with fixture overrides.
_SUCCESS_FIXTURE_KINDS: dict[str, str] = {
    "tg_file_imports": "file",
    "tg_file_importers": "file",
    "tg_navigate": "file",
    "tg_classify_logs": "file",
    "tg_audit": "manifest",
    "tg_audit_diff": "manifest",
    "tg_audit_manifest_verify": "manifest",
    "tg_review_bundle_create": "bundle",
    "tg_review_bundle_verify": "bundle",
    "tg_session": "session",
    "tg_session_context": "session",
    "tg_session_show": "session",
    "tg_session_blast_radius": "session",
    "tg_session_blast_radius_plan": "session",
    "tg_session_blast_radius_render": "session",
    "tg_session_context_render": "session",
    "tg_session_edit_plan": "session",
    "tg_session_file_importers": "session",
    "tg_session_refresh": "session",
    "tg_checkpoint_undo": "checkpoint",
}


def _schema_derived_minimal_kwargs(tool) -> dict[str, object]:
    """Type-based minimal values for every REQUIRED param on a tool's live schema."""
    props = tool.inputSchema.get("properties", {})
    required = set(tool.inputSchema.get("required", []))
    kwargs: dict[str, object] = {}
    for param_name, schema in props.items():
        if param_name not in required:
            continue
        types_seen = set()
        if "type" in schema:
            types_seen.add(schema["type"])
        for sub in schema.get("anyOf", ()):
            if "type" in sub:
                types_seen.add(sub["type"])
        if "string" in types_seen:
            kwargs[param_name] = "x"
        elif "integer" in types_seen or "number" in types_seen:
            kwargs[param_name] = 1
        elif "boolean" in types_seen:
            kwargs[param_name] = True
        elif "array" in types_seen:
            kwargs[param_name] = []
        elif "object" in types_seen:
            kwargs[param_name] = {}
        else:
            kwargs[param_name] = None
    return kwargs


def _materialize_census_fixtures(root: Path) -> dict[str, object]:
    """Hermetic fixtures for the success probes. Runs inside the census root (cwd)."""
    from tensor_grep.cli import mcp_server

    (root / "dummy.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (root / "dummy.log").write_text("INFO startup ok\nERROR database failed\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({
            "version": 1,
            "kind": "rewrite-audit-manifest",
            "created_at": "2026-08-09T00:00:00Z",
            "path": str(root),
            "files": [],
        }),
        encoding="utf-8",
    )
    # bundle.json is derived from the manifest via the real create tool, so the verify
    # probe exercises a genuine round trip rather than a hand-built fixture.
    bundle = json.loads(mcp_server.tg_review_bundle_create(manifest_path="manifest.json"))
    (root / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    # a real session (usable by every session-family success probe)
    session_payload = json.loads(mcp_server.tg_session_open(path="."))
    # a real checkpoint (usable by tg_checkpoint_undo)
    checkpoint_payload = json.loads(mcp_server.tg_checkpoint_create(path="."))
    return {
        "session_id": session_payload.get("session_id"),
        "checkpoint_id": checkpoint_payload.get("checkpoint_id"),
    }


def _success_kwargs(
    tool_name: str, base_kwargs: dict[str, object], fixtures: dict[str, object]
) -> dict[str, object]:
    """Curated reach args for a tool's SUCCESS probe, with fixture overrides."""
    kwargs = dict(base_kwargs)
    kind = _SUCCESS_FIXTURE_KINDS.get(tool_name)
    if kind == "session":
        kwargs["session_id"] = fixtures["session_id"]
    elif kind == "checkpoint":
        kwargs["checkpoint_id"] = fixtures["checkpoint_id"]
    elif kind == "manifest" and tool_name == "tg_audit_diff":
        kwargs["previous_manifest"] = "manifest.json"
        kwargs["current_manifest"] = "manifest.json"
    return kwargs


def _force_dense_unavailable() -> tuple[object, object]:
    """Hermetically force the `tg find` dense leg off for the census duration (F2).

    ``tg_find``'s success family must be reachable regardless of the AMBIENT dense
    model state: with model2vec absent or corrupt the dense availability probe / model
    load can vary (a corrupt model directory even raises BackendExecutionError ->
    find_backend_error, flipping the census from success-reach to error-only on that
    one runner). The census pins `dense_available()` to a deterministic "unavailable"
    answer so every venue exercises the same BM25-only fallback success path; the
    same return value is used wherever the degrade writes
    ``rank_fallback_reason``. Returns (original_fn, module) for the caller to restore.
    """
    import tensor_grep.core.retrieval_dense as retrieval_dense

    original = retrieval_dense.dense_available
    retrieval_dense.dense_available = lambda: (
        False,
        "census hermetic force: dense model state must not change census outcome (M14 F2)",
    )
    return original, retrieval_dense


def _probe_family_keys(tool) -> list[str]:
    """The family KEY SET the census generates for one tool (F3: parity validation
    compares allowlist keys against this set, never just the tool name)."""
    keys = ["curated", "schema"]
    for case_tool, param in _CONFINEMENT_RATCHET_CASES:
        if case_tool == tool.name:
            keys.append("confinement:" + param)
    return keys


# (tool, family) probes that need the CONTROLLED ast engine seam (CI-round fix,
# mirroring the tg_find dense force): the SUCCESS arm of these tools requires a real
# AST engine (tree-sitter grammar via the native-shaped AstBackend path, or the
# ast-grep wrapper binary / ruleset machinery), which the CI pytest env does not
# install -- there the real probes can only raise an absent-dep exception or return
# the stamped 'unavailable' envelope, so success reach would flip the census verdict
# between desktop (engine present) and CI (engine absent). The census drives exactly
# these families through a fixed engine seam instead; every other family (incl. the
# engine-free out-of-root confinement error probes) runs the real code.
_AST_ENGINE_SHIM_FAMILIES: dict[str, frozenset[str]] = {
    "tg_ast_search": frozenset({"curated", "schema"}),
    "tg_ruleset_scan": frozenset({"curated"}),
    "tg_scan": frozenset({"curated"}),
}


class _ControlledAstBackendShim:
    """Stand-in for the Pipeline AST backend: fixed 'AstBackend' identity, empty
    results, no engine dependency. `type(backend).__name__` is load-bearing --
    tg_ast_search refuses any backend not named AstBackend/AstGrepWrapperBackend,
    so the class's OWN __name__ is overwritten below."""

    def search(
        self, current_file: str, pattern: str, *, config: object | None = None
    ) -> SearchResult:
        return SearchResult(matches=[], total_files=0, total_matches=0)


_ControlledAstBackendShim.__name__ = "AstBackend"  # type: ignore[attr-defined]


class _ControlledAstPipelineShim:
    """Deterministic Pipeline stand-in providing the same `get_backend()` seam.

    The REAL Pipeline(ast=True) raises ConfigurationError when neither a tree-sitter
    grammar nor the ast-grep binary is present (CI pytest env), so real probes there
    can never reach the tool's success return site. This stub returns the fixed
    backend above, letting the probe flow through the tool's REAL success path
    (_inject_mcp_contract_fields-wrapped JSON) on every env.
    """

    def __init__(self, config: object) -> None:
        self.config = config
        self.selected_backend_name = "AstBackend"
        self.selected_backend_reason = "ast-native-census-controlled"
        self.selected_gpu_device_ids: list[int] = []
        self.selected_gpu_chunk_plan_mb: list[int] = []
        self.fallback_reason = None

    def get_backend(self) -> _ControlledAstBackendShim:
        return _ControlledAstBackendShim()


def _controlled_ast_scan_payload(
    project_cfg: dict[str, object],
    rules: list[dict[str, str]],
    *,
    routing_reason: str,
    **kwargs: object,
) -> dict[str, object]:
    """Deterministic stand-in for _run_ast_scan_payload (the tg_ruleset_scan/tg_scan
    engine seam): returns a minimal empty-findings payload in the real function's
    shape, exercising the tools' real success return site (`_inject_mcp_contract_fields`
    over the payload) without the ast-grep engine."""
    return {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "config_path": str(project_cfg.get("config_path", "census-controlled")),
        "path": str(project_cfg.get("root_dir", ".")),
        "ruleset": kwargs.get("ruleset_name"),
        "rule_count": len(rules),
        "matched_rules": [],
        "total_matches": 0,
        "files_scanned": 0,
        "findings": [],
    }


@contextlib.contextmanager
def _controlled_ast_engine() -> Iterator[None]:
    """Temporarily swap the two AST engine seams (Pipeline + _run_ast_scan_payload)
    for their controlled stand-ins; restored in a finally."""

    from tensor_grep.cli import mcp_server

    original_pipeline = mcp_server.Pipeline
    original_run_scan = mcp_server._run_ast_scan_payload
    mcp_server.Pipeline = _ControlledAstPipelineShim  # type: ignore[assignment]
    mcp_server._run_ast_scan_payload = _controlled_ast_scan_payload  # type: ignore[assignment]
    try:
        yield
    finally:
        mcp_server.Pipeline = original_pipeline
        mcp_server._run_ast_scan_payload = original_run_scan


def _probe_families(
    tool,
    outside_dir: Path,
    fixtures: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    """(family_key, kwargs) probes for one tool: success/curated, schema-derived,
    and one out-of-root confinement error probe per non-exempt string path param."""
    base = _RATCHET_BASE_KWARGS.get(tool.name, {})
    families: list[tuple[str, dict[str, object]]] = [
        ("curated", _success_kwargs(tool.name, base, fixtures)),
        ("schema", _schema_derived_minimal_kwargs(tool)),
    ]
    for case_tool, param in _CONFINEMENT_RATCHET_CASES:
        if case_tool != tool.name:
            continue
        assert tool.name in _RATCHET_BASE_KWARGS, (
            f"{tool.name} has confinement case {param!r} but no _RATCHET_BASE_KWARGS entry"
        )
        families.append((
            "confinement:" + param,
            {**_RATCHET_BASE_KWARGS[tool.name], param: str(outside_dir)},
        ))
    deduped: list[tuple[str, dict[str, object]]] = []
    seen: set[str] = set()
    for family_key, kwargs in families:
        key = family_key + "|" + repr(sorted(kwargs.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((family_key, kwargs))
    return deduped


@dataclass
class CensusResult:
    violations: list[str] = field(default_factory=list)
    coverage: dict[str, dict[str, object]] = field(default_factory=dict)


def _mcp_census(
    expected: str,
    *,
    root: Path,
    outside_dir: Path,
) -> CensusResult:
    """Run every registered tool through its probe families.

    FAILURES (each appended to ``violations``):
    - any JSON-dict response whose mcp_contract_version != expected (success OR error);
    - any raise NOT covered by a strict _RAISE_ALLOWLIST entry whose EXPECTED exception
      type matches (a same-family mutation raising a different type is a violation);
    - any non-string return (always a violation -- never excused by the raise
      allowlist, F1);
    - any non-JSON text not on the narrow _TEXT_ALLOWLIST;
    - a missing ERROR family where confinement probes make it deterministically reachable;
    - a missing SUCCESS family for any tool that is neither native-ext-gated nor
      fixture-probed (and a missing success for a fixture-probed tool = fixture rot).

    Environment-independence (F2): the `tg find` dense leg is force-disabled for the
    census duration so tg_find's success reach is identical whether the ambient dense
    model is installed, absent, or corrupt.
    """
    original_dense_available, retrieval_dense = _force_dense_unavailable()
    try:
        return _mcp_census_run(expected, root=root, outside_dir=outside_dir)
    finally:
        retrieval_dense.dense_available = original_dense_available


def _mcp_census_run(
    expected: str,
    *,
    root: Path,
    outside_dir: Path,
) -> CensusResult:
    from tensor_grep.cli import mcp_server

    fixtures = _materialize_census_fixtures(root)
    result = CensusResult()
    tools = sorted(asyncio.run(mcp_server.mcp.list_tools()), key=lambda t: t.name)
    for tool in tools:
        fam_counts: dict[str, int] = {"success": 0, "error": 0}
        raised_fams: list[str] = []
        text_fams: list[str] = []
        for family_key, kwargs in _probe_families(tool, outside_dir, fixtures):
            try:
                if family_key in _AST_ENGINE_SHIM_FAMILIES.get(tool.name, frozenset()):
                    # Controlled engine seam: the ast trio's success arms are
                    # unreachable on CI (no tree-sitter/ast-grep), so drive the real
                    # tool code with a fixed engine to exercise the real success
                    # return site; verdict identical on desktop and CI.
                    with _controlled_ast_engine():
                        out = getattr(mcp_server, tool.name)(**kwargs)
                else:
                    out = getattr(mcp_server, tool.name)(**kwargs)
            except Exception as exc:
                allow = _RAISE_ALLOWLIST.get((tool.name, family_key))
                if allow is None or not isinstance(exc, allow[0]):
                    expected_types = (
                        "n/a" if allow is None else ", ".join(t.__name__ for t in allow[0])
                    )
                    result.violations.append(
                        f"{tool.name}[{family_key}] RAISED {type(exc).__name__}: "
                        f"{str(exc)[:80]} -- allowlist expects {expected_types} on this "
                        "family; any other exception type is a violation (a NEW tool that "
                        "raises fails by default)"
                    )
                raised_fams.append(family_key)
                continue
            if not isinstance(out, str):
                # F1: a non-string return is a violation INDEPENDENT of the raise
                # allowlist -- the allowlist excuses typed dependency-absent RAISES only,
                # never a tool that returns a non-str object instead of a response.
                result.violations.append(
                    f"{tool.name}[{family_key}] returned {type(out).__name__}, not str"
                )
                raised_fams.append(family_key)
                continue
            try:
                payload = json.loads(out)
            except json.JSONDecodeError:
                reason = _TEXT_ALLOWLIST.get((tool.name, family_key))
                if reason is None:
                    result.violations.append(
                        f"{tool.name}[{family_key}] returned non-JSON text -- allowlist "
                        "with a reason if intentional, otherwise stamp its JSON arms"
                    )
                text_fams.append(family_key)
                continue
            if not isinstance(payload, dict):
                result.violations.append(
                    f"{tool.name}[{family_key}] JSON is not an object: {type(payload).__name__}"
                )
                continue
            family = "error" if isinstance(payload.get("error"), dict) else "success"
            if payload.get("mcp_contract_version") != expected:
                result.violations.append(
                    f"{tool.name}[{family_key}] {family}-family stamp="
                    f"{payload.get('mcp_contract_version')!r} (expected {expected!r})"
                )
            fam_counts[family] += 1
        result.coverage[tool.name] = {
            "success": fam_counts["success"],
            "error": fam_counts["error"],
            "text": text_fams,
            "raised": raised_fams,
        }
        conf_params = [p for (t, p) in _CONFINEMENT_RATCHET_CASES if t == tool.name]
        if conf_params and fam_counts["error"] == 0:
            result.violations.append(
                f"{tool.name}: ERROR family not observed despite {len(conf_params)} "
                "deterministic out-of-root confinement probes"
            )
        if tool.name in _SUCCESS_FIXTURE_KINDS:
            if fam_counts["success"] == 0:
                result.violations.append(
                    f"{tool.name}: fixture-driven SUCCESS probe did not reach a "
                    "success-family response (fixture rot or an ungated success hole)"
                )
        elif tool.name not in _SUCCESS_UNREACHABLE and fam_counts["success"] == 0:
            result.violations.append(
                f"{tool.name}: SUCCESS family not observed -- add fixture-driven reach, "
                "or an entry in _SUCCESS_UNREACHABLE/_SUCCESS_FIXTURE_KINDS with a reason"
            )
    return result


def _success_probe_text(tool_name: str, fixtures: dict[str, object]) -> str:
    """The success-family probe for one tool (shared by the census and the per-tool tests)."""
    from tensor_grep.cli import mcp_server

    base = _RATCHET_BASE_KWARGS.get(tool_name, {})
    kwargs = _success_kwargs(tool_name, base, fixtures)
    return getattr(mcp_server, tool_name)(**kwargs)


# ---------------------------------------------------------------------------
# Defect 1: the setdefault value-escape in _inject_mcp_contract_fields
# ---------------------------------------------------------------------------


def test_inject_replaces_stale_mcp_contract_version_literal() -> None:
    """A tool payload carrying its OWN stale/forked mcp_contract_version literal must
    be OVERWRITTEN by the central const -- tool-literal-wins (the retired setdefault
    behavior) defeats the single source of truth."""
    from tensor_grep.cli import mcp_server

    payload = json.dumps({
        "tool": "x",
        "mcp_contract_version": "0.0.0-stale-forked-literal",
        "schema_version": 99,
    })
    result = json.loads(mcp_server._inject_mcp_contract_fields(payload))
    assert result["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


def test_inject_preserves_domain_schema_version_and_defaults_absent_one() -> None:
    """F2-corrected: schema_version stays setdefault. A payload with a DOCUMENTED domain
    meaning for that exact key (like tg_doctor's schema_version: 2) keeps it; only an
    ABSENT schema_version is defaulted to the current JSON-output version."""
    from tensor_grep.cli import mcp_server

    domain = json.loads(
        mcp_server._inject_mcp_contract_fields(
            json.dumps({"mcp_contract_version": "5.0.0-ignored", "schema_version": 2})
        )
    )
    assert domain["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert domain["schema_version"] == 2

    absent = json.loads(mcp_server._inject_mcp_contract_fields(json.dumps({"tool": "x"})))
    assert absent["schema_version"] == mcp_server._json_output_version()


def test_inject_idempotent_when_value_already_matches_const() -> None:
    """A payload already carrying the exact central const passes through unchanged
    (the common case -- every builder that embeds the const stays byte-identical)."""
    from tensor_grep.cli import mcp_server

    payload = json.dumps({
        "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": mcp_server._json_output_version(),
    })
    result = json.loads(mcp_server._inject_mcp_contract_fields(payload))
    assert result["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert result["schema_version"] == mcp_server._json_output_version()


def test_inject_noop_on_non_dict_json() -> None:
    """Array/primitive JSON and non-JSON must pass through byte-identical."""
    from tensor_grep.cli import mcp_server

    array_json = json.dumps([1, 2, 3])
    assert mcp_server._inject_mcp_contract_fields(array_json) == array_json
    assert mcp_server._inject_mcp_contract_fields("not json at all") == "not json at all"


# ---------------------------------------------------------------------------
# Defect 2: raw json.dumps success/error sites crossed the wire unstamped
# ---------------------------------------------------------------------------


class _ExplodingCybertBackend:
    def __init__(self) -> None:
        raise AssertionError("MCP classify must not probe CyBERT by default")


def test_tg_classify_logs_success_path_stamps_contract_version_by_value(
    tmp_path, monkeypatch
) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingCybertBackend),
    )
    log_path = tmp_path / "app.log"
    log_path.write_text("INFO startup ok\nERROR database failed\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs(str(log_path), structured_json=True)
    payload = json.loads(out)
    assert payload["provider"] == "heuristic"
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


def test_tg_classify_logs_error_path_stamps_contract_version_by_value(
    tmp_path, monkeypatch
) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    out = mcp_server.tg_classify_logs("does-not-exist.log", structured_json=True)
    payload = json.loads(out)
    assert "error" in payload
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Shared helper reachable from tg_search / tg_ast_search -- census-unreachable
# ---------------------------------------------------------------------------


def test_broad_root_scan_refusal_result_is_stamped() -> None:
    """_broad_root_scan_refusal_result is shared by tg_search and tg_ast_search but is
    NOT reachable by the black-box census (no hermetic probe root triggers a
    broad-refusal), so it gets its own direct value pin."""
    from tensor_grep.cli import mcp_server

    out = mcp_server._broad_root_scan_refusal_result(
        "refused", pattern="x", path=".", structured_json=True
    )
    payload = json.loads(out)
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# F1 + F3 per-tool SUCCESS-path value pins (fixture-driven)
# ---------------------------------------------------------------------------

_F1_SUCCESS_TOOLS = [
    "tg_audit_manifest_verify",
    "tg_review_bundle_create",
    "tg_review_bundle_verify",
    "tg_session_refresh",
    "tg_session_blast_radius",
    "tg_session_blast_radius_plan",
    "tg_session_blast_radius_render",
    "tg_session_context_render",
    "tg_session_edit_plan",
    "tg_session_file_importers",
    "tg_audit",
]


@pytest.mark.parametrize("tool_name", _F1_SUCCESS_TOOLS)
def test_fixture_driven_success_path_carries_stamp_by_value(
    tool_name: str, tmp_path, monkeypatch
) -> None:
    """Each previously-unstamped tool's SUCCESS family must carry
    mcp_contract_version == the central const BY VALUE (the census probes their error
    families; this pins the success families the fixtures make deterministically
    reachable)."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    from tensor_grep.cli import mcp_server

    fixtures = _materialize_census_fixtures(tmp_path)
    out = _success_probe_text(tool_name, fixtures)
    payload = json.loads(out)
    assert not isinstance(payload.get("error"), dict), (
        f"{tool_name}: success probe returned an ERROR family: {payload.get('error')}"
    )
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# The VALUE ratchet over the live registry (F3)
# ---------------------------------------------------------------------------


def test_all_registered_tools_stamp_contract_version_by_value(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """Every registered tool's observed JSON response -- success AND error families --
    must carry mcp_contract_version == the central const BY VALUE. Tool set derived
    live from the registry; a NEW tool with a missing/wrong stamp fails here, as does
    a raise or non-JSON response not on the narrow allowlists."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    assert not result.violations, "census violations:\n- " + "\n- ".join(sorted(result.violations))


def test_census_reaches_success_and_error_families_per_tool(
    monkeypatch, tmp_path, tmp_path_factory
) -> None:
    """The census must not be error-family-skewed: every tool that is not natively
    gated must reach a SUCCESS family, and every tool with path parameters must reach
    an ERROR family. Reading the coverage map proves per-tool reach breadth."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    assert not result.violations
    for tool_name, cov in sorted(result.coverage.items()):
        assert cov["success"] >= 1 or tool_name in _SUCCESS_UNREACHABLE, (
            f"{tool_name}: no success-family response observed"
        )
        assert cov["error"] >= 1 or not any(
            t == tool_name for (t, _p) in _CONFINEMENT_RATCHET_CASES
        ), f"{tool_name}: no error-family response observed despite confinement probes"


def test_census_allowlist_entries_refer_to_registered_tools(monkeypatch) -> None:
    """Allowlist/fixture/gate keys must resolve against the LIVE registry AND the
    generated probe families (F3): a stale tool NAME fails, AND a stale
    (tool, family) FAMILY KEY -- a key matching no probe the census actually generates
    for that tool (e.g. after a confinement-param rename) -- fails too."""
    from tensor_grep.cli import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    registered = {t.name for t in tools}
    by_name = {t.name: t for t in tools}
    for name in list(_SUCCESS_UNREACHABLE) + list(_SUCCESS_FIXTURE_KINDS):
        assert name in registered, f"stale census tool entry: {name}"
    for entry in list(_RAISE_ALLOWLIST) + list(_TEXT_ALLOWLIST):
        tool_name, family_key = entry
        assert tool_name in registered, f"stale census tool entry: {entry}"
        assert family_key in _probe_family_keys(by_name[tool_name]), (
            f"stale census FAMILY key: {entry} -- the census generates no probe named "
            f"{family_key!r} for {tool_name} (generated: {_probe_family_keys(by_name[tool_name])})"
        )


def test_contract_stamp_ratchet_fires_when_injector_is_neutered(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """Negative control (error route): with the injector neutered, every
    inject-routed response loses its stamp and the census must report violations --
    otherwise this ratchet is decoration that can never RED."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "_inject_mcp_contract_fields",
        lambda result_json: result_json,
    )
    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    assert result.violations, "census reported zero violations with the injector neutered"


def test_contract_stamp_ratchet_fires_on_unstamped_success_path(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """Negative control (success route, F3): patch ONE tool's SUCCESS output to drop
    the stamp and the census must report THAT tool -- proving the ratchet discriminates
    an unstamped SUCCESS, not just an injector-wide failure."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    original_tg_devices = mcp_server.tg_devices

    def _unstamped_devices(json_output: bool = True) -> str:
        out = original_tg_devices(json_output=json_output)
        payload = json.loads(out)
        payload.pop("mcp_contract_version", None)
        return json.dumps(payload)

    monkeypatch.setattr(mcp_server, "tg_devices", _unstamped_devices)
    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    hit = [v for v in result.violations if "tg_devices" in v]
    assert hit, f"census did not report the unstamped tg_devices success: {result.violations}"


def test_contract_stamp_ratchet_fires_when_a_tool_raises(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """Negative control (raise route, F3): a tool that RAISES during its probe is a
    census FAILURE by default -- the ratchet cannot be gamed by an exception that
    skips the stamp check."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    def _exploding_rulesets() -> str:
        raise RuntimeError("simulated census probe failure")

    monkeypatch.setattr(mcp_server, "tg_rulesets", _exploding_rulesets)
    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    hit = [v for v in result.violations if "tg_rulesets" in v and "RAISED" in v]
    assert hit, f"census did not report the raising tool: {result.violations}"


def test_contract_stamp_ratchet_fires_on_disallowed_exception_type_in_allowlisted_family(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """F1 mutation control: an allowlisted (tool, family) entry excuses ONLY its
    declared dependency-absent exception types. A mutation that makes the same family
    raise AssertionError (or any other type) must RED -- an allowlist can no longer
    mask a regression that merely raises."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    def _mutated_ast_search(**kwargs) -> str:
        raise AssertionError("new regression in an allowlisted family")

    monkeypatch.setattr(mcp_server, "tg_ast_search", _mutated_ast_search)
    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    hits = [
        v
        for v in result.violations
        if "tg_ast_search" in v and "AssertionError" in v and "allowlist expects" in v
    ]
    assert hits, (
        "census did not RED on a disallowed exception type inside an allowlisted family: "
        f"{result.violations}"
    )


def test_contract_stamp_ratchet_census_is_independent_of_ambient_dense_model_state(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """F2 environment-independence: simulate the corrupt-dense environment (dense
    availability probe answers True and the model load raises BackendExecutionError,
    the exact find_backend_error failure codex's runner saw). The census's internal
    hermetic force must override this ambient state, so tg_find's SUCCESS family is
    still reached via the deterministic BM25-only fallback and the census stays green
    -- the census outcome must not depend on which dense-model state a runner has."""
    import tensor_grep.core.retrieval_dense as retrieval_dense
    from tensor_grep.backends.base import BackendExecutionError

    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(
        retrieval_dense,
        "dense_available",
        lambda: (True, "corrupt-dense simulation: model reports available"),
    )
    monkeypatch.setattr(
        retrieval_dense,
        "load_dense_model",
        lambda model_dir: (_ for _ in ()).throw(BackendExecutionError("corrupt model dir")),
    )

    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    assert not result.violations, "corrupt-dense census violations:\n- " + "\n- ".join(
        sorted(result.violations)
    )
    assert result.coverage["tg_find"]["success"] >= 1, (
        "tg_find success family not reached under simulated corrupt-dense ambient state -- "
        "the hermetic BM25 force is not engaging"
    )
    # The census's own force must not leak: after _mcp_census returns, the AMBIENT
    # (test-patched) availability probe is what the module sees again.
    ok, _reason = retrieval_dense.dense_available()
    assert ok is True, "census hermetic force leaked past its finally-restore"


def test_contract_stamp_ratchet_census_is_independent_of_ambient_ast_engine_state(
    tmp_path, tmp_path_factory, monkeypatch
) -> None:
    """CI-round env proof, mirroring the dense-model test: simulate the CI pytest env
    where NO ast engine exists -- Pipeline(ast=True) construction and
    _run_ast_scan_payload both raise ConfigurationError ("no AST backend is
    available"). A shim-less census on such an env can only reach the stamped
    'unavailable'/error arms; the controlled engine seam must kick in so the three
    AST tools still reach their SUCCESS return sites, keeping the census verdict
    identical to the desktop (where the real engine exists)."""
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    outside_dir = tmp_path_factory.mktemp("m14_ratchet_outside")

    from tensor_grep.cli import mcp_server

    real_pipeline = mcp_server.Pipeline

    class _NoAstEnginePipeline:
        """Faithful CI simulation: the REAL Pipeline's ast branch raises
        ConfigurationError when no tree-sitter grammar/ast-grep binary exists, but its
        NON-ast branch (tg_search's regex path) still works. Mirror exactly that:
        ast configs raise, everything else delegates to the real Pipeline."""

        def __new__(cls, config: object = None, **kwargs: object) -> object:
            if getattr(config, "ast", False):
                raise ConfigurationError("no AST backend is available (simulated CI env)")
            return real_pipeline(config, **kwargs)  # type: ignore[call-arg]

    def _no_ast_engine_scan(*args: object, **kwargs: object) -> dict[str, object]:
        raise ConfigurationError("no AST backend is available (simulated CI env)")

    monkeypatch.setattr(mcp_server, "Pipeline", _NoAstEnginePipeline)
    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _no_ast_engine_scan)

    result = _mcp_census(
        mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        root=tmp_path,
        outside_dir=outside_dir,
    )
    assert not result.violations, "no-ast-engine census violations:\n- " + "\n- ".join(
        sorted(result.violations)
    )
    for tool_name in ("tg_ast_search", "tg_ruleset_scan", "tg_scan"):
        assert result.coverage[tool_name]["success"] >= 1, (
            f"{tool_name} success family not reached under the simulated no-ast-engine "
            "env -- the controlled engine seam is not engaging"
        )
        assert result.coverage[tool_name]["error"] >= 1, (
            f"{tool_name} error family not reached under the simulated no-ast-engine env"
        )
        assert result.coverage[tool_name]["raised"] == [], (
            f"{tool_name} still produced raises under the simulated no-ast-engine env: "
            f"{result.coverage[tool_name]['raised']}"
        )
    # No leak: the ambient (engine-less-for-ast) Pipeline is what the module sees again
    # after the census's internal shim restores it.
    from tensor_grep.core.config import SearchConfig

    with pytest.raises(ConfigurationError):
        mcp_server.Pipeline(config=SearchConfig(ast=True, lang="python", no_messages=True))
