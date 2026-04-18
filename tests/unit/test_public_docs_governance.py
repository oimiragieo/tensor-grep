from pathlib import Path

README_PATH = Path("README.md")
ROUTING_DOC_PATH = Path("docs/routing_policy.md")
WORLD_CLASS_PLAN_PATH = Path("docs/world_class_plan.md")
BENCHMARKS_DOC_PATH = Path("docs/benchmarks.md")
TOOL_COMPARISON_DOC_PATH = Path("docs/tool_comparison.md")
PAPER_DOC_PATH = Path("docs/PAPER.md")


def test_readme_should_point_to_canonical_public_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/benchmarks.md" in readme
    assert "docs/tool_comparison.md" in readme
    assert "docs/gpu_crossover.md" in readme
    assert "docs/routing_policy.md" in readme
    assert "docs/harness_api.md" in readme
    assert "docs/harness_cookbook.md" in readme
    assert "tg calibrate" in readme
    assert "tg search --ndjson" in readme
    assert "tg mcp" in readme
    assert "native CPU engine" in readme
    assert "native GPU engine" in readme
    assert "benchmark-governed" in readme
    assert "100 MB" in readme or "100MB" in readme
    assert "tg run --rewrite" in readme
    assert "--apply" in readme
    assert "atomic temp-file rename contract" in readme


def test_routing_policy_should_describe_current_native_and_fallback_routes() -> None:
    doc = ROUTING_DOC_PATH.read_text(encoding="utf-8")

    assert "# Routing Policy" in doc
    assert "NativeCpuBackend" in doc
    assert "NativeGpuBackend" in doc
    assert "TrigramIndex" in doc
    assert "AstBackend" in doc
    assert "GpuSidecar" in doc
    assert "RipgrepBackend" in doc
    assert "--index" in doc
    assert "--gpu-device-ids" in doc
    assert "--force-cpu" in doc
    assert "Warm non-stale compatible `.tg_index`" in doc
    assert "calibrated threshold" in doc


def test_post_100_roadmap_should_record_closed_statuses_for_remaining_programs() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap B: Claude Speed Architecture" in doc
    assert "### Roadmap C: Native Control Plane" in doc
    assert "### Roadmap D: Broad Provider Promotion" in doc
    assert "### Roadmap E: Comparative Benchmark v2" in doc
    assert "Status:" in doc
    assert "Closed on 2026-03-30" in doc
    assert "prompt/contract-space tuning is exhausted for this line" in doc
    assert "larger native rewrite is required" in doc
    assert "keep-opt-in decision" in doc
    assert "comparator set is frozen" in doc
    assert "scenario packs are frozen by purpose" in doc


def test_next_roadmap_should_record_launcher_mode_finding_for_roadmap_1() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 1: Native Control Plane" in doc
    assert "Status:" in doc
    assert "Closed on 2026-03-30" in doc
    assert "tg_launcher_mode" in doc
    assert "python_module_launcher" in doc
    assert "explicit_binary" in doc
    assert "0.252554" in doc
    assert "0.282347" in doc
    assert "still regress against the accepted Windows baseline" in doc
    assert "larger native rewrite is still required" in doc


def test_next_roadmap_should_record_closed_statuses_for_roadmaps_2_to_5() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 2: Agent Product Surface v2" in doc
    assert "Closed on 2026-03-30" in doc
    assert "retry taxonomy" in doc
    assert "attempt provenance" in doc
    assert "### Roadmap 3: Claude Speed Architecture v2" in doc
    assert "explicit architectural freeze" in doc
    assert "### Roadmap 4: Broad Provider Promotion v2" in doc
    assert "keep-opt-in decision" in doc
    assert "broad planning pack" in doc
    assert "### Roadmap 5: Comparative Benchmark v3" in doc
    assert "frozen comparator set" in doc
    assert "accepted artifacts for that line" in doc


def test_benchmark_docs_should_freeze_comparator_set_and_pack_inventory() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "## Comparative Benchmark v2" in doc
    assert "### Frozen Comparator Set" in doc
    assert "`claude-baseline`" in doc
    assert "`claude-enhanced`" in doc
    assert "`copilot`" in doc
    assert "`gemini-cli`" in doc
    assert "`gemini-baseline`" in doc
    assert "`gemini-enhanced`" in doc
    assert "### Frozen Scenario Packs" in doc
    assert "planning broad pack" in doc.lower()
    assert "provider broad pack" in doc.lower()
    assert "provider hardcases" in doc.lower()
    assert "patch same-pack 12-scenario line" in doc.lower()
    assert "cold-path local benchmark" in doc.lower()
    assert "python_module_launcher" in doc
    assert "explicit_binary" in doc
    assert "0.252554" in doc
    assert "0.282347" in doc
    assert "## Comparative Benchmark v3" in doc
    assert "same accepted comparator set and pack inventory" in doc


def test_tool_comparison_doc_should_publish_workload_specific_comparator_story() -> None:
    doc = TOOL_COMPARISON_DOC_PATH.read_text(encoding="utf-8")

    assert "# Tool Comparison" in doc
    assert "one benchmark is never enough" in doc
    assert "Host-Local Command Snapshot" in doc
    assert "git grep --no-index" in doc
    assert "ast-grep" in doc
    assert "Semgrep" in doc
    assert "Zoekt" in doc
    assert "artifacts/bench_run_benchmarks.json" in doc
    assert "artifacts/bench_tool_comparison.json" in doc
    assert "artifacts/bench_run_native_cpu_benchmarks.json" in doc
    assert "rg --no-ignore ERROR artifacts/bench_data" in doc
    assert "tg search --cpu --no-ignore ERROR artifacts/bench_data" in doc
    assert "CLI contract parity" in doc


def test_future_roadmap_should_define_new_program_and_first_batch() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "## Future Roadmap (Draft)" in doc
    assert "### Roadmap 1: Rust-First Native Control Plane" in doc
    assert "### Roadmap 2: Agent Product Surface v3" in doc
    assert "### Roadmap 3: Structural Claude Speed Program" in doc
    assert "### Roadmap 4: Broad Provider Promotion" in doc
    assert "### Roadmap 5: Comparative Benchmark v4" in doc
    assert "Status:" in doc
    assert "Closed on 2026-03-31" in doc
    assert "tg_binary_source" in doc
    assert "explicit_arg" in doc
    assert "default_binary_path" in doc
    assert "python_module_rust_first" in doc
    assert "0.386778" in doc
    assert "0.384161" in doc
    assert "rejected experiment" in doc
    assert "explicit_binary_early_rg" in doc
    assert "0.297869" in doc
    assert "0.281141" in doc
    assert "explicit_binary_positional" in doc
    assert "0.286235" in doc
    assert "0.26987" in doc
    assert "explicit_binary_positional_early_rg" in doc
    assert "0.268412" in doc
    assert "0.255065" in doc
    assert "explicit_fast_binary" in doc
    assert "0.324425" in doc
    assert "0.312694" in doc
    assert "larger native rewrite is required" in doc
    assert "Closed on 2026-03-31 as a larger-native-rewrite boundary for the current line" in doc


def test_benchmark_docs_should_record_future_roadmap_batch_1_metadata() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "tg_binary_source" in doc
    assert "explicit_arg" in doc
    assert "default_binary_path" in doc
    assert "Rust-first native control-plane roadmap" in doc
    assert "python_module_rust_first" in doc
    assert "0.386778" in doc
    assert "0.384161" in doc
    assert "explicit_binary_early_rg" in doc
    assert "0.297869" in doc
    assert "0.281141" in doc
    assert "explicit_binary_positional" in doc
    assert "0.286235" in doc
    assert "0.26987" in doc
    assert "explicit_binary_positional_early_rg" in doc
    assert "0.268412" in doc
    assert "0.255065" in doc
    assert "explicit_fast_binary" in doc
    assert "0.324425" in doc
    assert "0.312694" in doc


def test_future_roadmap_should_record_closed_statuses_for_roadmaps_2_to_5() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 2: Agent Product Surface v3" in doc
    assert "Closed on 2026-03-31." in doc
    assert "canonical end-to-end CLI and MCP flows" in doc
    assert "final-score examples" in doc
    assert "### Roadmap 3: Structural Claude Speed Program" in doc
    assert "explicit architectural freeze" in doc
    assert "remaining speed gap is now recorded as structural/model-side" in doc
    assert "### Roadmap 4: Broad Provider Promotion" in doc
    assert "keep-opt-in decision for the broader pack as well" in doc
    assert "### Roadmap 5: Comparative Benchmark v4" in doc
    assert "comparator set and scenario-pack inventory" in doc
    assert "render only from accepted artifacts" in doc


def test_native_rewrite_roadmap_should_define_next_program() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "## Native Rewrite Roadmap (Draft)" in doc
    assert "### Roadmap 1: Native Control-Plane Rewrite" in doc
    assert (
        "Closed on 2026-03-31 as an explicit rejected architecture result for the current line"
        in doc
    )
    assert "accepted Windows baseline" in doc
    assert "### Roadmap 2: Agent Product Surface v4" in doc
    assert "multi-attempt chains" in doc
    assert "### Roadmap 3: Structural Claude Speed v3" in doc
    assert "context/instruction/caching lever" in doc
    assert "### Roadmap 4: Broad Provider Promotion v3" in doc
    assert "true broad planning pack" in doc
    assert "### Roadmap 5: Comparative Benchmark v5" in doc
    assert "accepted artifacts for that line" in doc


def test_native_rewrite_roadmap_should_record_agent_product_surface_v4_progress() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 2: Agent Product Surface v4" in doc
    assert "attempt_ledger.json" in doc
    assert "Multi-Attempt Replay Flow" in doc
    assert "partial retry ledgers" in doc
    assert "Closed on 2026-03-31." in doc


def test_native_rewrite_roadmap_should_record_closed_statuses_for_remaining_programs() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 1: Native Control-Plane Rewrite" in doc
    assert "explicit_fast_binary" in doc
    assert "0.324425" in doc
    assert "0.312694" in doc
    assert (
        "Closed on 2026-03-31 as an explicit rejected architecture result for the current line"
        in doc
    )
    assert "larger native rewrite is still required" in doc
    assert "### Roadmap 3: Structural Claude Speed v3" in doc
    assert "Closed on 2026-03-31 with another explicit architecture/model-side freeze" in doc
    assert "no accepted faster enhanced line exists for the current release line" in doc
    assert "### Roadmap 4: Broad Provider Promotion v3" in doc
    assert "Closed on 2026-03-31 with an explicit keep-opt-in decision for the broader pack" in doc
    assert "### Roadmap 5: Comparative Benchmark v5" in doc
    assert "Closed on 2026-03-31 as a frozen comparison surface" in doc
    assert "comparator set and pack inventory remain frozen" in doc


def test_benchmark_docs_should_record_comparative_benchmark_v5_closed_surface() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "## Comparative Benchmark v5" in doc
    assert "Closed on 2026-03-31 as a frozen comparison surface" in doc
    assert "comparator set and pack inventory remain frozen" in doc


def test_benchmark_docs_should_record_comparative_benchmark_v5_governance() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "## Comparative Benchmark v5" in doc
    assert "comparator additions" in doc
    assert "pack substitutions" in doc
    assert "new accepted artifact line" in doc


def test_benchmark_docs_should_record_2026_04_18_windows_baseline_refresh() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "## Windows Accepted Baseline Refresh (2026-04-18)" in doc
    assert "clean `origin/main` evidence" in doc
    assert "`benchmark_host_key`" in doc
    assert "`host_provenance`" in doc
    assert "`check_regression.py` policy is unchanged" in doc


def test_paper_should_record_2026_04_18_windows_baseline_refresh() -> None:
    doc = PAPER_DOC_PATH.read_text(encoding="utf-8")

    assert "2026-04-18 Windows baseline refresh" in doc
    assert "clean `origin/main` evidence" in doc
    assert "`benchmark_host_key`" in doc
    assert "`host_provenance`" in doc
    assert "policy remained unchanged" in doc


def test_native_rewrite_v2_roadmap_should_define_next_program() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "## Native Rewrite Roadmap v2 (Draft)" in doc
    assert "### Roadmap 1: Native Control-Plane Rewrite v2" in doc
    assert "real native front door" in doc
    assert "### Roadmap 2: Agent Product Surface v5" in doc
    assert "multi-task and multi-session replay chains" in doc
    assert "### Roadmap 3: Claude Speed Architecture v4" in doc
    assert "static context caching" in doc
    assert "### Roadmap 4: Broad Provider Promotion v4" in doc
    assert "true broad planning pack" in doc
    assert "### Roadmap 5: Comparative Benchmark v6" in doc
    assert "frozen accepted inputs" in doc


def test_native_rewrite_v2_roadmap_should_define_parallel_execution_board() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "## Parallel Execution Board" in doc
    assert "Main integrator" in doc
    assert "Lane A: Native control plane" in doc
    assert "Lane B: Structural rewrite core" in doc
    assert "Lane C: Agent product surface" in doc
    assert "Lane D: Provider broad-pack decision" in doc
    assert "Lane E: Benchmark and competitor governance" in doc
    assert "3x throughput" in doc
    assert "disjoint write sets" in doc
    assert "full repo gates run at merge points" in doc
    assert "close completed subagents at lane handoff or merge time" in doc
    assert "do not leave completed subagents running after their result is integrated" in doc


def test_native_rewrite_v2_roadmap_should_record_default_frontdoor_probe() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "explicit_binary default front door" in doc
    assert "0.261513" in doc
    assert "0.247376" in doc
    assert "5 of 10 scenarios" in doc


def test_benchmark_docs_should_record_default_frontdoor_probe() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "explicit_binary default front door" in doc
    assert "0.261513" in doc
    assert "0.247376" in doc


def test_native_rewrite_v2_roadmap_should_record_closed_statuses() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 1: Native Control-Plane Rewrite v2" in doc
    assert (
        "Closed on 2026-03-31 as an explicit rejected architecture result for the current line"
        in doc
    )
    assert "default front door" in doc
    assert "5 of 10 scenarios" in doc
    assert "### Roadmap 2: Agent Product Surface v5" in doc
    assert "Closed on 2026-03-31." in doc
    assert "multi-task and multi-session replay chains" in doc
    assert "multi_task_attempt_ledger.json" in doc
    assert "multi_session_attempt_ledger.json" in doc
    assert "### Roadmap 3: Claude Speed Architecture v4" in doc
    assert "explicit architecture/model-side freeze" in doc
    assert "### Roadmap 4: Broad Provider Promotion v4" in doc
    assert "keep-opt-in decision for the broader pack" in doc
    assert "### Roadmap 5: Comparative Benchmark v6" in doc
    assert "Closed on 2026-03-31 as a frozen comparison surface" in doc


def test_benchmark_docs_should_record_comparative_benchmark_v6_governance() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "## Comparative Benchmark v6" in doc
    assert "frozen accepted inputs" in doc
    assert "comparator additions" in doc
    assert "pack substitutions" in doc
    assert "### External workload baselines" in doc
    assert "`ripgrep`" in doc
    assert "`ast-grep`" in doc
    assert "`Semgrep`" in doc
    assert "`Zoekt`" in doc
    assert "cold plain-text search baseline" in doc
    assert "structural search/rewrite baseline" in doc
    assert "policy/security scan baseline" in doc
    assert "indexed repeated-query baseline" in doc
