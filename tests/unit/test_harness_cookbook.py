from pathlib import Path

COOKBOOK_PATH = Path("docs/harness_cookbook.md")
README_PATH = Path("README.md")


def test_harness_cookbook_covers_public_workflows() -> None:
    doc = COOKBOOK_PATH.read_text(encoding="utf-8")

    assert "# Harness Cookbook" in doc
    assert "## Search JSON Flow" in doc
    assert "## Indexed Search Flow" in doc
    assert "## Repo Map Flow" in doc
    assert "## Context Pack Flow" in doc
    assert "## Session Reuse Flow" in doc
    assert "## End-to-End CLI Flow" in doc
    assert "## End-to-End MCP Flow" in doc
    assert "## Rewrite Planning Flow" in doc
    assert "## Diff Review Flow" in doc
    assert "## Apply + Verify Flow" in doc
    assert "## Multi-Attempt Replay Flow" in doc
    assert "multi-session replay chain" in doc.lower()
    assert "multi-task replay chain" in doc.lower()
    assert "## Patch Score Flow" in doc
    assert "## Failure Mode Examples" in doc
    assert "## Checkpoint Flow" in doc
    assert "## NDJSON Streaming Flow" in doc
    assert "## MCP Workflow Flow" in doc
    assert "## Calibrate and Routing Flow" in doc
    assert "## Large Corpus Guidance" in doc
    assert "tg.exe search --json" in doc
    assert "tg.exe search --index --json" in doc
    assert "tg.exe map --json" in doc
    assert 'tg.exe context --query "invoice payment" --json' in doc
    assert "tg session open" in doc
    assert "tg session list" in doc
    assert "tg session show" in doc
    assert "tg session refresh" in doc
    assert "tg session context" in doc
    assert "tg session serve" in doc
    assert "--refresh-on-stale" in doc
    assert "tg.exe map --json" in doc
    assert "tg.exe context --query" in doc
    assert "tg.exe search --ndjson" in doc
    assert "tg.exe run --lang python --rewrite" in doc
    assert "--diff" in doc
    assert "--apply" in doc
    assert "--verify" in doc
    assert "--json" in doc
    assert "python benchmarks/run_patch_bakeoff.py" in doc
    assert "python benchmarks/render_patch_scorecard.py" in doc
    assert '"missing_predictions"' in doc
    assert "retry the producer, not the scorer" in doc
    assert "timeout after" in doc
    assert "session_invalid_request_stale.json" in doc
    assert "patch_bakeoff_incomplete.json" in doc
    assert "patch_bakeoff_no_patch.json" in doc
    assert "defs_provider_disagreement.json" in doc
    assert "provider_status_unavailable.json" in doc
    assert "rewrite_apply_verify_validation_failed.json" in doc
    assert "--lint-cmd" in doc
    assert "--test-cmd" in doc
    assert "tg checkpoint create" in doc
    assert "tg checkpoint list" in doc
    assert "tg checkpoint undo" in doc
    assert "tg defs --symbol" in doc
    assert "tg source --symbol" in doc
    assert "tg impact --symbol" in doc
    assert "tg refs --symbol" in doc
    assert "tg callers --symbol" in doc
    assert "--checkpoint" in doc
    assert "--apply-edit-ids" in doc
    assert "--reject-edit-ids" in doc
    assert "tg.exe calibrate" in doc
    assert '"routing_backend"' in doc
    assert '"routing_reason"' in doc
    assert '"sidecar_used"' in doc
    assert '"validation"' in doc
    assert "tg_rewrite_plan" in doc
    assert "tg_rewrite_apply" in doc
    assert "tg_rewrite_diff" in doc
    assert "tg_index_search" in doc
    assert "tg_repo_map" in doc
    assert "tg_context_pack" in doc
    assert "tg_edit_plan" in doc
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
    assert "tg_checkpoint_create" in doc
    assert "tg_checkpoint_list" in doc
    assert "tg_checkpoint_undo" in doc
    assert "tg_audit_manifest_verify" in doc
    assert "attempt ledger" in doc.lower()
    assert "replay chain" in doc.lower()
    assert "partial retry ledger" in doc.lower()
    assert "multi_session_attempt_ledger.json" in doc
    assert "multi_task_attempt_ledger.json" in doc
    assert "python benchmarks/build_attempt_ledger.py" in doc
    assert "python benchmarks/run_patch_bakeoff.py --scenarios" in doc
    assert "--attempt-ledger-dir" in doc
    assert "python benchmarks/run_claude_skill_ab.py --input" in doc
    assert "python benchmarks/run_gemini_skill_ab.py --input" in doc
    assert "python benchmarks/run_claude_patch_predictions.py --input" in doc
    assert "python benchmarks/run_copilot_patch_predictions.py --input" in doc
    assert "python benchmarks/run_gemini_patch_predictions.py --input" in doc


def test_readme_points_harness_consumers_to_contract_and_cookbook_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/harness_api.md" in readme
    assert "docs/harness_cookbook.md" in readme
