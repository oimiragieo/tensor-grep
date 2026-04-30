import json
from pathlib import Path

DOC_PATH = Path("docs/harness_api.md")
EXAMPLES_DIR = Path("docs/examples")
EXPECTED_EXAMPLES = {
    "search.json": ("total_matches", "matches"),
    "ast_run.json": ("query", "total_matches", "matches"),
    "index_search.json": ("sidecar_used", "matches"),
    "rulesets.json": ("rulesets",),
    "ruleset_scan.json": ("ruleset", "findings", "total_matches"),
    "repo_map.json": ("files", "symbols"),
    "context_pack.json": ("query", "files"),
    "edit_plan.json": (
        "query",
        "candidate_edit_targets",
        "edit_plan_seed",
        "navigation_pack",
    ),
    "context_render.json": (
        "query",
        "rendered_context",
        "sources",
        "render_profile",
        "optimize_context",
        "sections",
        "candidate_edit_targets",
        "edit_plan_seed",
        "navigation_pack",
    ),
    "defs.json": ("symbol", "definitions"),
    "source.json": ("symbol", "sources"),
    "impact.json": ("symbol", "files"),
    "refs.json": ("symbol", "references"),
    "callers.json": ("symbol", "callers"),
    "blast_radius.json": ("symbol", "callers", "caller_tree", "rendered_caller_tree"),
    "blast_radius_plan.json": (
        "symbol",
        "candidate_edit_targets",
        "edit_plan_seed",
        "navigation_pack",
    ),
    "blast_radius_render.json": (
        "symbol",
        "rendered_context",
        "sources",
        "callers",
        "caller_tree",
        "edit_plan_seed",
        "navigation_pack",
    ),
    "session_open.json": ("session_id", "file_count"),
    "session_context.json": ("query", "files", "session_id"),
    "rewrite_plan.json": ("total_edits", "edits"),
    "rewrite_apply_verify.json": ("checkpoint", "plan", "verification", "validation"),
    "attempt_ledger.json": ("artifact", "attempts", "final_outcome", "replay"),
    "multi_session_attempt_ledger.json": ("artifact", "attempts", "final_outcome", "replay"),
    "multi_task_attempt_ledger.json": ("artifact", "attempts", "final_outcome", "replay"),
    "patch_bakeoff.json": ("summary", "rows"),
    "audit_manifest_verify.json": ("manifest_path", "checks", "valid"),
    "gpu_sidecar_search.json": ("sidecar_used", "matches"),
    "calibrate.json": ("corpus_size_breakpoint_bytes", "measurements"),
    "mcp_rewrite_diff.json": ("sidecar_used", "diff"),
}
FAILURE_MODE_EXAMPLES = {
    "session_invalid_request_stale.json": ("session_id", "error"),
    "patch_bakeoff_incomplete.json": ("summary", "rows"),
    "patch_bakeoff_no_patch.json": ("summary", "rows"),
    "defs_provider_disagreement.json": ("provider_agreement", "provider_status"),
    "provider_status_unavailable.json": ("provider_status",),
    "rewrite_apply_verify_validation_failed.json": ("plan", "validation", "verification"),
}


def test_harness_api_doc_covers_all_required_json_shapes() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "# Harness API" in doc
    assert "## Search JSON" in doc
    assert "## AST Run JSON" in doc
    assert "## Index Search JSON" in doc
    assert "## Rulesets JSON" in doc
    assert "## Ruleset Scan JSON" in doc
    assert "## Repo Map JSON" in doc
    assert "## Context Pack JSON" in doc
    assert "## Edit Plan JSON" in doc
    assert "## Context Render JSON" in doc
    assert "## Rewrite Plan JSON" in doc
    assert "## Batch Rewrite Config" in doc
    assert "## Apply + Verify JSON" in doc
    assert "## Attempt Ledger JSON" in doc
    assert "## Patch Bakeoff JSON" in doc
    assert "## Audit Manifest Verify JSON" in doc
    assert "## GPU Sidecar JSON" in doc
    assert "## Calibrate JSON" in doc
    assert "## Search NDJSON" in doc
    assert "## Symbol Defs JSON" in doc
    assert "## Symbol Source JSON" in doc
    assert "## Symbol Impact JSON" in doc
    assert "## Symbol Refs JSON" in doc
    assert "## Symbol Callers JSON" in doc
    assert "## Symbol Blast Radius JSON" in doc
    assert "## Symbol Blast Radius Plan JSON" in doc
    assert "## Symbol Blast Radius Render JSON" in doc
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
    assert "tg_edit_plan" in doc
    assert "tg_context_render" in doc
    assert "tg_rulesets" in doc
    assert "tg_ruleset_scan" in doc
    assert "tg_mcp_capabilities" in doc
    assert "MCPRuntime" in doc
    assert "python-local" in doc
    assert "embedded-safe" in doc
    assert "native-required" in doc
    assert "native_required_options" in doc
    assert "native-tg-unavailable" in doc
    assert "TG_NATIVE_TG_BINARY" in doc
    assert "error.remediation" in doc
    assert "tg_symbol_defs" in doc
    assert "tg_symbol_source" in doc
    assert "tg_symbol_impact" in doc
    assert "tg_symbol_refs" in doc
    assert "tg_symbol_callers" in doc
    assert "semantic_provider" in doc
    assert "provider_agreement" in doc
    assert "provider_status" in doc
    assert "tg_symbol_blast_radius" in doc
    assert "tg_symbol_blast_radius_plan" in doc
    assert "tg_symbol_blast_radius_render" in doc
    assert "navigation_pack" in doc
    assert "tg_session_blast_radius" in doc
    assert "tg_session_blast_radius_render" in doc
    assert "tg_session_open" in doc
    assert "tg_session_list" in doc
    assert "tg_session_show" in doc
    assert "tg_session_refresh" in doc
    assert "tg_session_context" in doc
    assert "tg_session_edit_plan" in doc
    assert "tg_session_context_render" in doc
    assert "tg_session_blast_radius_plan" in doc
    assert "tg_audit_manifest_verify" in doc
    assert "tg_checkpoint_create" in doc
    assert "tg_checkpoint_list" in doc
    assert "tg_checkpoint_undo" in doc
    assert "attempt ledger" in doc.lower()
    assert "replay chain" in doc.lower()
    assert "partial retry" in doc.lower()
    assert "multi-session replay" in doc.lower()
    assert "multi-task replay" in doc.lower()
    assert "python benchmarks/build_attempt_ledger.py" in doc
    assert "python benchmarks/run_patch_bakeoff.py --scenarios" in doc
    assert "--attempt-ledger-dir" in doc
    assert "python benchmarks/run_claude_skill_ab.py --input" in doc
    assert "python benchmarks/run_gemini_skill_ab.py --input" in doc
    assert "python benchmarks/run_claude_patch_predictions.py --input" in doc
    assert "python benchmarks/run_copilot_patch_predictions.py --input" in doc
    assert "python benchmarks/run_gemini_patch_predictions.py --input" in doc
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


def test_harness_api_doc_links_failure_mode_examples() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "## Failure Mode Examples" in doc
    assert "missing predictions" in doc.lower()
    assert "timeout" in doc.lower()
    assert "validation failed" in doc.lower()
    assert "stale session" in doc.lower()
    assert "provider unavailable" in doc.lower()
    assert "provider disagreement" in doc.lower()

    for file_name in FAILURE_MODE_EXAMPLES:
        assert file_name in doc
    assert "Failure-mode companion examples:" in doc
    assert "session_invalid_request_stale.json" in doc
    assert "patch_bakeoff_incomplete.json" in doc
    assert "patch_bakeoff_no_patch.json" in doc
    assert "defs_provider_disagreement.json" in doc
    assert "provider_status_unavailable.json" in doc
    assert "rewrite_apply_verify_validation_failed.json" in doc


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
        elif file_name == "patch_bakeoff.json":
            assert payload["artifact"] == "bench_patch_bakeoff"
            assert payload["suite"] == "run_patch_bakeoff"
            assert isinstance(payload["summary"]["missing_predictions"], list)
            assert isinstance(payload["rows"], list)
            assert payload["rows"]
            assert "reason" in payload["rows"][0]
        elif file_name == "attempt_ledger.json":
            assert payload["artifact"] == "agent_attempt_ledger"
            assert payload["suite"] == "agent_loop"
            assert isinstance(payload["attempts"], list)
            assert len(payload["attempts"]) >= 2
            assert payload["attempts"][0]["status"] == "validation_failed"
            assert (
                payload["attempts"][1]["parent_attempt_id"] == payload["attempts"][0]["attempt_id"]
            )
            assert (
                payload["final_outcome"]["accepted_attempt_id"]
                == payload["attempts"][1]["attempt_id"]
            )
            assert payload["replay"]["preserve_attempt_ids"] is True
            assert isinstance(payload["replay"]["partial_retry_ledger"], list)
            assert payload["replay"]["partial_retry_ledger"]
            assert isinstance(payload["replay"]["audit_chain"], list)
            assert payload["replay"]["audit_chain"]
        elif file_name == "multi_session_attempt_ledger.json":
            assert payload["artifact"] == "agent_attempt_ledger"
            assert payload["suite"] == "agent_loop"
            assert len(payload["attempts"]) >= 2
            assert payload["attempts"][0]["session_id"]
            assert payload["attempts"][1]["session_id"]
            assert payload["attempts"][0]["session_id"] != payload["attempts"][1]["session_id"]
            assert payload["replay"]["multi_session"] is True
            assert (
                payload["replay"]["handoff"]["from_session_id"]
                == payload["attempts"][0]["session_id"]
            )
            assert (
                payload["replay"]["handoff"]["to_session_id"]
                == payload["attempts"][1]["session_id"]
            )
        elif file_name == "multi_task_attempt_ledger.json":
            assert payload["artifact"] == "agent_attempt_ledger"
            assert payload["suite"] == "agent_loop"
            assert len(payload["tasks"]) >= 2
            assert payload["tasks"][0]["task_id"] != payload["tasks"][1]["task_id"]
            assert payload["replay"]["multi_task"] is True
            assert isinstance(payload["replay"]["task_chain"], list)
            assert payload["replay"]["task_chain"]
        else:
            assert isinstance(payload["version"], int)
            assert isinstance(payload["routing_backend"], str)
            assert payload["routing_backend"]
            assert isinstance(payload["routing_reason"], str)
            assert payload["routing_reason"]
            if file_name in {
                "repo_map.json",
                "context_pack.json",
                "edit_plan.json",
                "context_render.json",
                "defs.json",
                "source.json",
                "impact.json",
                "refs.json",
                "callers.json",
                "blast_radius.json",
                "blast_radius_plan.json",
                "blast_radius_render.json",
                "session_context.json",
            }:
                assert payload["coverage"]["language_scope"] == "python-js-ts-rust"
                assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
                assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
            if file_name == "rulesets.json":
                assert isinstance(payload["rulesets"], list)
                assert payload["rulesets"]
            if file_name == "ruleset_scan.json":
                assert payload["ruleset"]
                assert isinstance(payload["findings"], list)
                assert payload["findings"]
                assert payload["findings"][0]["fingerprint"]
                assert payload["findings"][0]["status"] in {
                    "new",
                    "existing",
                    "suppressed",
                    "clear",
                }
                assert "evidence" in payload["findings"][0]
                assert isinstance(payload["findings"][0]["evidence"][0].get("snippets", []), list)
                assert payload["baseline"]["existing_findings"] >= 0
                assert payload["baseline_written"]["count"] >= 0
                assert payload["suppressions"]["suppressed_findings"] >= 0
                assert payload["suppressions_written"]["count"] >= 0
            if file_name in {"edit_plan.json", "blast_radius_plan.json"}:
                assert isinstance(payload["navigation_pack"]["follow_up_reads"], list)
                assert payload["navigation_pack"]["follow_up_reads"]
                assert "#L" in payload["navigation_pack"]["primary_target"]["mention_ref"]
                assert payload["edit_plan_seed"]["primary_span"]["start_line"] >= 1
                assert isinstance(payload["edit_plan_seed"]["related_spans"], list)
                assert isinstance(payload["edit_plan_seed"]["dependent_files"], list)
                assert isinstance(payload["edit_plan_seed"]["edit_ordering"], list)
                assert isinstance(payload["edit_plan_seed"]["validation_plan"], list)
                assert payload["edit_plan_seed"]["validation_plan"]
                assert isinstance(payload["candidate_edit_targets"]["spans"], list)
                assert payload["candidate_edit_targets"]["spans"]
                assert 0.0 <= payload["edit_plan_seed"]["rollback_risk"] <= 1.0
            if file_name in {"context_render.json", "blast_radius_render.json"}:
                assert isinstance(payload["navigation_pack"]["follow_up_reads"], list)
                assert payload["navigation_pack"]["follow_up_reads"]
                assert "#L" in payload["navigation_pack"]["primary_target"]["mention_ref"]

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
        attempts = payload.get("attempts")
        findings = payload.get("findings")
        rulesets = payload.get("rulesets")
        symbols = payload.get("symbols")
        file_count = payload.get("file_count")
        checks = payload.get("checks")
        summary = payload.get("summary")
        rows = payload.get("rows")
        error = payload.get("error")
        replay = payload.get("replay")
        final_outcome = payload.get("final_outcome")
        provider_status = payload.get("provider_status")
        provider_agreement = payload.get("provider_agreement")

        assert (
            total_matches
            or total_edits
            or nested_total_edits
            or measurements
            or diff
            or files
            or attempts
            or findings
            or rulesets
            or symbols
            or file_count
            or checks
            or summary
            or rows
            or error
            or replay
            or final_outcome
            or provider_status
            or provider_agreement
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


def test_failure_mode_examples_exist_and_have_actionable_shapes() -> None:
    for file_name, required_keys in FAILURE_MODE_EXAMPLES.items():
        payload = json.loads((EXAMPLES_DIR / file_name).read_text(encoding="utf-8"))

        for key in required_keys:
            assert key in payload

        if file_name.startswith("patch_bakeoff_"):
            assert payload["artifact"] == "bench_patch_bakeoff"
            assert payload["suite"] == "run_patch_bakeoff"
            assert isinstance(payload["summary"]["missing_predictions"], list)
            assert isinstance(payload["rows"], list)
            assert payload["rows"]
            assert "reason" in payload["rows"][0]
        elif file_name == "rewrite_apply_verify_validation_failed.json":
            assert payload["verification"]["mismatches"] == []
            assert payload["validation"]["success"] is False
            assert payload["validation"]["commands"]
        elif file_name == "session_invalid_request_stale.json":
            assert payload["error"]["code"] == "invalid_request"
            assert "stale" in payload["error"]["message"].lower()
        elif file_name == "defs_provider_disagreement.json":
            assert payload["provider_agreement"]["agreement_status"] == "diverged"
            assert payload["provider_status"]["mode"] == "hybrid"
        elif file_name == "provider_status_unavailable.json":
            assert payload["provider_status"]["available"] is False
            assert payload["provider_status"]["last_error"]


def test_harness_api_failure_mode_examples_exist_and_match_documented_shapes() -> None:
    for file_name, required_keys in FAILURE_MODE_EXAMPLES.items():
        payload = json.loads((EXAMPLES_DIR / file_name).read_text(encoding="utf-8"))
        for key in required_keys:
            assert key in payload

    stale = json.loads(
        (EXAMPLES_DIR / "session_invalid_request_stale.json").read_text(encoding="utf-8")
    )
    assert stale["error"]["code"] == "invalid_request"
    assert "stale" in stale["error"]["message"].lower()

    incomplete = json.loads(
        (EXAMPLES_DIR / "patch_bakeoff_incomplete.json").read_text(encoding="utf-8")
    )
    assert incomplete["artifact"] == "bench_patch_bakeoff"
    assert incomplete["summary"]["missing_predictions"]

    no_patch = json.loads(
        (EXAMPLES_DIR / "patch_bakeoff_no_patch.json").read_text(encoding="utf-8")
    )
    assert no_patch["artifact"] == "bench_patch_bakeoff"
    assert any(not row["patch_applied"] for row in no_patch["rows"])
    assert any(not row["validation_passed"] for row in no_patch["rows"])
    assert any(row["reason"] == "timeout after 60s" for row in no_patch["rows"])

    provider = json.loads(
        (EXAMPLES_DIR / "defs_provider_disagreement.json").read_text(encoding="utf-8")
    )
    assert provider["provider_agreement"]["agreement_status"] in {
        "diverged",
        "fallback-native",
        "native-fallback",
    }
    assert provider["provider_status"]["mode"] in {"lsp", "hybrid"}

    provider_unavailable = json.loads(
        (EXAMPLES_DIR / "provider_status_unavailable.json").read_text(encoding="utf-8")
    )
    assert provider_unavailable["provider_status"]["available"] is False
    assert provider_unavailable["provider_status"]["last_error"]

    validation_failed = json.loads(
        (EXAMPLES_DIR / "rewrite_apply_verify_validation_failed.json").read_text(encoding="utf-8")
    )
    assert validation_failed["validation"] is not None
    assert validation_failed["validation"]["success"] is False
    assert validation_failed["verification"]["mismatches"] == []
