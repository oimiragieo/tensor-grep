import json
from pathlib import Path

DOC_PATH = Path("docs/harness_api.md")
EXAMPLES_DIR = Path("docs/examples")
EXPECTED_EXAMPLES = {
    "search.json": ("total_matches", "matches"),
    "index_search.json": ("sidecar_used", "matches"),
    "repo_map.json": ("files", "symbols"),
    "context_pack.json": ("query", "files"),
    "context_render.json": (
        "query",
        "rendered_context",
        "sources",
        "render_profile",
        "optimize_context",
        "sections",
        "candidate_edit_targets",
        "edit_plan_seed",
    ),
    "defs.json": ("symbol", "definitions"),
    "source.json": ("symbol", "sources"),
    "impact.json": ("symbol", "files"),
    "refs.json": ("symbol", "references"),
    "callers.json": ("symbol", "callers"),
    "session_open.json": ("session_id", "file_count"),
    "session_context.json": ("query", "files", "session_id"),
    "rewrite_plan.json": ("total_edits", "edits"),
    "rewrite_apply_verify.json": ("checkpoint", "plan", "verification", "validation"),
    "gpu_sidecar_search.json": ("sidecar_used", "matches"),
    "calibrate.json": ("corpus_size_breakpoint_bytes", "measurements"),
    "mcp_rewrite_diff.json": ("sidecar_used", "diff"),
}


def test_harness_api_doc_covers_all_required_json_shapes() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "# Harness API" in doc
    assert "## Search JSON" in doc
    assert "## Index Search JSON" in doc
    assert "## Repo Map JSON" in doc
    assert "## Context Pack JSON" in doc
    assert "## Context Render JSON" in doc
    assert "## Rewrite Plan JSON" in doc
    assert "## Batch Rewrite Config" in doc
    assert "## Apply + Verify JSON" in doc
    assert "## GPU Sidecar JSON" in doc
    assert "## Calibrate JSON" in doc
    assert "## Search NDJSON" in doc
    assert "## Symbol Defs JSON" in doc
    assert "## Symbol Source JSON" in doc
    assert "## Symbol Impact JSON" in doc
    assert "## Symbol Refs JSON" in doc
    assert "## Symbol Callers JSON" in doc
    assert "## Session Open JSON" in doc
    assert "## Session Refresh JSON" in doc
    assert "## Session Context JSON" in doc
    assert "## Session Serve JSONL" in doc
    assert "## MCP Tool Responses" in doc
    assert "## Compatibility Policy" in doc
    assert "## Diff Output" in doc
    assert "routing_backend" in doc
    assert "routing_reason" in doc
    assert "version" in doc
    assert "coverage" in doc
    assert "python-js-ts-rust" in doc
    assert "python-ast+parser-js-ts-rust" in doc
    assert "filename+import+graph-heuristic" in doc
    assert "tg_repo_map" in doc
    assert "tg_context_pack" in doc
    assert "tg_context_render" in doc
    assert "tg_symbol_defs" in doc
    assert "tg_symbol_source" in doc
    assert "tg_symbol_impact" in doc
    assert "tg_symbol_refs" in doc
    assert "tg_symbol_callers" in doc
    assert "tg_session_open" in doc
    assert "tg_session_list" in doc
    assert "tg_session_show" in doc
    assert "tg_session_refresh" in doc
    assert "tg_session_context" in doc
    assert "tg_session_context_render" in doc
    assert "tg_checkpoint_create" in doc
    assert "tg_checkpoint_list" in doc
    assert "tg_checkpoint_undo" in doc
    assert "--apply-edit-ids" in doc
    assert "--reject-edit-ids" in doc
    assert "--checkpoint" in doc
    assert "--lint-cmd" in doc
    assert "--test-cmd" in doc
    assert "--batch-rewrite" in doc
    assert "rewrites" in doc
    assert "verify" in doc
    assert "validation" in doc
    assert "line_number" in doc
    assert "line" in doc
    assert "---" in doc
    assert "+++" in doc
    assert "@@" in doc
    assert "additive field" in doc.lower()
    assert "breaking change" in doc.lower()
    assert "version bump" in doc.lower()
    assert '"command":"context"' in doc
    assert "invalid_request" in doc
    assert "--refresh-on-stale" in doc


def test_harness_api_examples_exist_and_have_unified_envelope() -> None:
    assert EXAMPLES_DIR.is_dir()

    for file_name, required_keys in EXPECTED_EXAMPLES.items():
        payload = json.loads((EXAMPLES_DIR / file_name).read_text(encoding="utf-8"))

        if file_name == "session_open.json":
            assert isinstance(payload["session_id"], str)
            assert payload["session_id"]
            assert isinstance(payload["root"], str)
            assert payload["root"]
            assert isinstance(payload["created_at"], str)
            assert payload["created_at"]
            assert isinstance(payload["file_count"], int)
            assert isinstance(payload["symbol_count"], int)
        else:
            assert isinstance(payload["version"], int)
            assert isinstance(payload["routing_backend"], str)
            assert payload["routing_backend"]
            assert isinstance(payload["routing_reason"], str)
            assert payload["routing_reason"]
            if file_name in {
                "repo_map.json",
                "context_pack.json",
                "context_render.json",
                "defs.json",
                "source.json",
                "impact.json",
                "refs.json",
                "callers.json",
                "session_context.json",
            }:
                assert payload["coverage"]["language_scope"] == "python-js-ts-rust"
                assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
                assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"

        for key in required_keys:
            assert key in payload


def test_harness_api_examples_are_non_trivial_single_document_json() -> None:
    example_paths = sorted(EXAMPLES_DIR.glob("*.json"))

    assert len(example_paths) >= 7

    for path in example_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total_matches = payload.get("total_matches")
        total_edits = payload.get("total_edits")
        nested_total_edits = payload.get("plan", {}).get("total_edits")
        measurements = payload.get("measurements")
        diff = payload.get("diff")
        files = payload.get("files")
        symbols = payload.get("symbols")
        file_count = payload.get("file_count")

        assert (
            total_matches
            or total_edits
            or nested_total_edits
            or measurements
            or diff
            or files
            or symbols
            or file_count
        ), f"{path.name} should include matches, edits, repo inventory, or session metadata"


def test_harness_api_ndjson_example_contains_parseable_rows() -> None:
    ndjson_path = EXAMPLES_DIR / "search.ndjson"

    lines = [
        json.loads(line)
        for line in ndjson_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(lines) >= 2
    for row in lines:
        assert row["version"] == 1
        assert isinstance(row["routing_backend"], str)
        assert isinstance(row["routing_reason"], str)
        assert isinstance(row["sidecar_used"], bool)
        assert isinstance(row["query"], str)
        assert isinstance(row["path"], str)
        assert isinstance(row["file"], str)
        assert isinstance(row["line"], int)
        assert isinstance(row["text"], str)




