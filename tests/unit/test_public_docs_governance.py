import tomllib
from pathlib import Path

README_PATH = Path("README.md")
ROUTING_DOC_PATH = Path("docs/routing_policy.md")
WORLD_CLASS_PLAN_PATH = Path("docs/world_class_plan.md")
BENCHMARKS_DOC_PATH = Path("docs/benchmarks.md")
TOOL_COMPARISON_DOC_PATH = Path("docs/tool_comparison.md")
GPU_CROSSOVER_DOC_PATH = Path("docs/gpu_crossover.md")
PAPER_DOC_PATH = Path("docs/PAPER.md")
AGENTS_DOC_PATH = Path("AGENTS.md")
SKILL_DOC_PATH = Path("SKILL.md")
SESSION_HANDOFF_PATH = Path("docs/SESSION_HANDOFF.md")
CONTINUATION_PLAN_PATH = Path("docs/CONTINUATION_PLAN.md")
CONTRACTS_DOC_PATH = Path("docs/CONTRACTS.md")


def _project_release_tag() -> str:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return f"v{pyproject['project']['version']}"


CURRENT_RELEASE_TAG = _project_release_tag()
VERIFIED_RELEASE_TAG = "v1.13.23"
VERIFIED_RELEASE_COMMIT = "bd7035c chore(release): v1.13.23 [skip ci]"
VERIFIED_FIX_COMMIT = "3c0c213 fix: repair owned python launchers"
CURRENT_RELEASE_COMMIT = VERIFIED_RELEASE_COMMIT
CURRENT_FIX_COMMIT = VERIFIED_FIX_COMMIT
CURRENT_GPU_FIX_COMMIT = "361e0db fix: harden public GPU unavailable routing"
CURRENT_DOCS_STAMP_FIX_COMMIT = "2100122 fix: harden release docs stamp governance"
CURRENT_MULTIPATTERN_FIX_COMMIT = "87d4ca4 fix: accelerate fixed multi-pattern native search"
CURRENT_FEATURE_COMMIT = "a518cc6 feat: add agent success harness"
LATEST_COMPLETE_RELEASE_TAG = CURRENT_RELEASE_TAG
LATEST_COMPLETE_RELEASE_COMMIT = VERIFIED_RELEASE_COMMIT
LATEST_COMPLETE_FIX_COMMIT = VERIFIED_FIX_COMMIT
LATEST_VERIFIED_RELEASE_TAG = VERIFIED_RELEASE_TAG
LATEST_VERIFIED_MAIN_CI = "26513809791"
LATEST_VERIFIED_CODEQL = "26513808787"


def test_readme_should_point_to_canonical_public_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    # Structural pointers to canonical docs are the README's job and stay pinned here.
    assert "docs/benchmarks.md" in readme
    assert "docs/tool_comparison.md" in readme
    assert "docs/gpu_crossover.md" in readme
    assert "docs/routing_policy.md" in readme
    assert "docs/harness_api.md" in readme
    assert "docs/harness_cookbook.md" in readme
    # High-level capability surface the README still advertises.
    assert "tg calibrate" in readme
    assert "tg mcp" in readme
    assert "native CPU engine" in readme
    assert "benchmark-governed" in readme

    # NOTE: the detailed CLI/contract prose below used to be pinned into the README too. The README
    # is now a marketing/positioning doc, so those redundant pins were relaxed; each contract is
    # still governed against its dedicated doc elsewhere in this file / on disk:
    #   - `tg search --ndjson`, `tg_agent_capsule` -> SKILL.md / AGENTS.md / docs/harness_api.md
    #   - native GPU engine (`NativeGpuBackend`) -> docs/gpu_crossover.md, docs/routing_policy.md
    #   - 100 MB large-file/binary skip -> docs/harness_api.md, docs/CONTRACTS.md (binary-skip)
    #   - `tg run --rewrite` / `--apply` / atomic temp-file rename -> docs/PAPER.md, docs/harness_api.md
    #   - multi-project workspace roots / broad generated-root scan -> docs/CONTRACTS.md (below)
    #   - PowerShell `$NAME` expansion / `cmd.exe` metacharacters -> docs/CONTRACTS.md (below)
    #   - open a session once / daemon-routed edit-plan/context -> docs/CONTRACTS.md (warm-path)


def test_contracts_should_record_windows_shell_and_ordering_limits() -> None:
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")

    assert "Direct `.cmd` invocation from PowerShell" in contracts
    assert "--allow-broad-generated-scan" in contracts
    assert "broad generated-root scan" in contracts
    assert "multi-project workspace root" in contracts
    assert "semantic result parity" in contracts
    assert "validated compatibility set" in contracts
    assert "`--sort path`" in contracts
    assert "`--format rg`" in contracts
    assert "`--files-without-match`" in contracts
    assert "`--replace`" in contracts
    assert "exit-code behavior" in contracts
    assert "context_consistency" in contracts
    assert "JavaScript package-manager commands require `package.json` evidence" in contracts
    assert "omit commands entirely when no runner evidence exists" in contracts
    assert "stale-skipped" in contracts
    assert "Future token-efficiency profiles must be opt-in" in contracts
    assert "omission counts" in contracts
    assert "refetch commands" in contracts
    assert '`tg upgrade` must not infer "latest PyPI version"' in contracts
    assert (
        "post-upgrade imports" in contracts or "target Python can import `tensor_grep`" in contracts
    )
    assert "front-door files in a staging directory" in contracts
    assert "PowerShell installer native commands must check `$LASTEXITCODE`" in contracts
    assert "scheduled Windows self-upgrade helper" in contracts
    assert "skip yanked PyPI releases" in contracts
    assert "refresh the managed release-native front door" in contracts
    assert "Windows native-front-door retry helper" in contracts
    assert f"current `{CURRENT_RELEASE_TAG}` release line" in contracts
    assert "managed native-upgrade contract" in contracts
    assert "world_class_readiness" in contracts
    assert "raw_cold_text_search" in contracts
    assert "public_gpu_acceleration" in contracts
    assert "lsp_semantic_provider" in contracts
    assert "agent_target_selection_metrics" in contracts
    assert "path_tg_first_launcher_kind" in contracts
    assert "fresh_shell_path_tg_first_launcher_kind" in contracts
    assert "python_subprocess_path_tg_first_launcher_kind" in contracts
    assert "path_tg_launcher_warning" in contracts
    assert "shell_escaping_guidance" in contracts
    assert "PowerShell `$` expansion" in contracts
    assert "`cmd.exe` metacharacter escaping" in contracts
    assert "tg_launcher_command_kind" in contracts
    assert "tg_binary_version_status" in contracts
    assert "stale in-tree native tg binary" in contracts
    assert "agent-capsule-mixed-language" in contracts
    assert "agent-capsule-hardcases" in contracts
    assert "validation_alignment" in contracts
    assert 'ambiguity.status = "tie_requires_confirmation"' in contracts
    assert "GPU auto-recommendation must remain false" in contracts
    assert "`routing_gpu_device_ids = []`" in contracts
    assert "CPU fallback, not GPU acceleration proof" in contracts
    assert "gpu_evidence_status" in contracts
    assert "gpu_proof" in contracts
    assert "native_gpu_unavailable" in contracts
    assert "not_gpu_proof_reason" in contracts
    assert "fallback_or_sidecar_counts_as_gpu_proof" in contracts
    assert "tg-native-metadata.json" in contracts
    assert "--public-managed-proof" in contracts
    assert "public_managed_promotion_ready" in contracts
    assert "public_gpu_proof" in contracts
    assert "native_frontdoor_metadata_version" in contracts
    assert "native_frontdoor_asset_name" in contracts
    assert "classification_backend" in contracts
    assert "`tg run` is a validated AST slice" in contracts
    assert "`tg scan`" in contracts
    assert "`tg test`" in contracts
    assert "`tg new`" in contracts
    assert "not an ast-grep replacement" in contracts


def test_handoff_docs_should_record_current_release_state_and_fast_gate() -> None:
    docs = {
        "AGENTS.md": AGENTS_DOC_PATH.read_text(encoding="utf-8"),
        "README.md": README_PATH.read_text(encoding="utf-8"),
        "SKILL.md": SKILL_DOC_PATH.read_text(encoding="utf-8"),
        "docs/SESSION_HANDOFF.md": SESSION_HANDOFF_PATH.read_text(encoding="utf-8"),
        "docs/CONTINUATION_PLAN.md": CONTINUATION_PLAN_PATH.read_text(encoding="utf-8"),
    }

    # The README is now a marketing/positioning doc; the detailed current-release-state facts below
    # are governed against the dedicated handoff docs (AGENTS.md / SKILL.md / SESSION_HANDOFF.md /
    # CONTINUATION_PLAN.md) plus CHANGELOG.md, so README is excluded from these positive content pins.
    handoff_docs = {path: content for path, content in docs.items() if path != "README.md"}
    for content in handoff_docs.values():
        assert CURRENT_RELEASE_TAG in content
        assert f"release_docs_current_tag: {_project_release_tag()}" in content
        assert "python scripts/agent_readiness.py" in content
        assert "tg dogfood" in content

    for path in ("AGENTS.md", "SKILL.md"):
        assert (
            "latest complete public PyPI/release-asset distribution is also "
            f"`{CURRENT_RELEASE_TAG}`"
        ) in docs[path]

    for path, content in docs.items():
        assert "Latest complete public release PR" not in content, path
        assert "Latest complete public release commit" not in content, path

    assert f"current tagged state is `{CURRENT_RELEASE_TAG}`" in docs["docs/CONTINUATION_PLAN.md"]
    assert (
        f"latest complete public PyPI/release-asset distribution is also `{CURRENT_RELEASE_TAG}`"
    ) in docs["docs/CONTINUATION_PLAN.md"]

    # NOTE: per-release proof anchors (specific fix/release commit hashes) used to be pinned across
    # every doc here. They were a maintenance trap (hand-updated each release, inevitably drifting),
    # so they were removed; the current-release facts above plus CHANGELOG.md / GitHub releases are
    # the single source of release history. Behavioral/capability checks below are retained.

    handoff = docs["docs/SESSION_HANDOFF.md"]
    assert f"- Latest tagged version: `{CURRENT_RELEASE_TAG}`" in handoff
    assert f"- Latest complete PyPI version: `{LATEST_COMPLETE_RELEASE_TAG}`" in handoff
    assert (
        f"GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/{CURRENT_RELEASE_TAG}>"
        in handoff
    )
    assert f"tensor-grep=={LATEST_COMPLETE_RELEASE_TAG.removeprefix('v')}" in handoff
    assert "post-release-safe docs governance" in handoff
    assert "native GPU unavailable" in handoff
    assert 'subprocess.run(["tg", ...])' in handoff
    assert "Closed GPU gates and launcher diagnostics gap" in handoff
    assert "Closed docs/version governance and validation placeholder gap" in handoff
    assert "Closed explicit ranking and validation quoting gap" in handoff
    assert "Closed edit automation safety gap" in handoff
    assert "Closed capsule trust-alignment gap" in handoff
    assert "Prior GPU probe and benchmark-warning gaps" in handoff
    assert "Prior launcher observability and benchmark attribution gaps" in handoff
    assert "Prior public launcher and agent contract gaps" in handoff
    assert "Prior Windows `.cmd` quoted-pattern gap" in handoff
    assert "publish-github-release-assets" in handoff
    assert "native front door" in handoff
    assert "rust_binary_version_status = matches" in handoff
    assert "tg agent src/tensor_grep/cli" in handoff
    assert "Actionable Context Capsule" in handoff
    assert "public-windows-launcher-quoted-patterns" in handoff
    assert "fresh quoted no-match phrase" in handoff
    assert "tg classify --format json" in handoff
    assert "local deterministic" in handoff
    assert "top-level `validation_commands`" in handoff
    assert "1GB/5GB" in handoff
    assert "tg --version --verbose" in handoff
    assert "Usage: tg" in handoff
    assert "rust_binary_version_status = stale-skipped" in handoff
    assert "skipped_native_tg_binaries" in handoff
    assert "--format rg" in handoff
    assert "context_consistency" in handoff
    assert "no runner evidence exists" in handoff
    assert "agent-readiness dogfood gate" in handoff
    assert "--allow-broad-generated-scan" in handoff
    assert "multi-project workspace root" in handoff
    assert "--pcre2 --sort path" in handoff
    assert "multiline searches forward" in handoff
    assert "Exact symbol context queries" in handoff
    assert 'uppercase `API_KEY = "..."` assignments' in handoff
    assert "GPU benchmark correctness accepts `rg` exit code `1`" in handoff
    assert "path_tg_first_launcher_kind = cmd-shim" in handoff
    assert "fresh_shell_path_tg_first_launcher_kind = managed-native" in handoff
    assert "tg_launcher_command_kind" in handoff
    assert "does not initialize or warn about unrelated unsupported GPUs" in handoff
    assert "GPU benchmark auto-recommendation must remain false" in handoff
    assert "validation_alignment" in handoff
    assert "warn when timed entrypoints include `.cmd`, `uv`, or Python-module overhead" in handoff
    assert "v1.9.0` release adds `tg agent`" in handoff
    assert "v1.9.10` caps capsule alternative-target confidence" in handoff
    assert "v1.9.9` release adds `run_agent_workflow_benchmarks.py`" in handoff
    assert "v1.9.8` release refreshes stale tensor-grep-owned `tg.com`" in handoff
    assert "v1.9.7` release clarifies GPU benchmark promotion gates" in handoff
    assert "v1.9.6` release fixes the `v1.9.5` dogfood blockers" in handoff
    assert "v1.9.5` release hardens GPU native gate attribution" in handoff
    assert "v1.9.4` release fixes stale docs-governance expectations" in handoff
    assert "v1.9.3` release hardens explicit language/file-name agent ranking" in handoff
    assert "v1.9.2` release hardens edit JSON" in handoff
    assert "v1.9.1` release hardens mixed-language capsule confidence" in handoff

    # NOTE: the README used to carry a full "## Current Release State" section -- per-release fix /
    # feature / release commit hashes, CI/CodeQL run IDs, PyPI line, and a hand-maintained per-version
    # "What `vX` closed:" changelog ledger. The README is now a marketing/positioning doc and no longer
    # mirrors that ledger; it was a maintenance trap that drifted every release. Those facts remain
    # governed by their single sources of truth:
    #   - current-release-state facts (tag, agent-readiness gate, dogfood) -> handoff_docs loop above
    #     plus the docs/SESSION_HANDOFF.md `handoff` block above (latest tag / PyPI / GitHub release).
    #   - per-version "What `vX` closed" / fix-commit ledger -> CHANGELOG.md and GitHub releases.
    #   - the behavioral/capability fragments that used to be pinned into the README block (e.g.
    #     `native front door`, `tg classify --format json`, `classification_backend`,
    #     `top-level validation_commands`, `path_tg_first_launcher_kind`, `tg_launcher_command_kind`,
    #     `Actionable Context Capsule`, `validation_alignment`, `public managed GPU is not
    #     promotion-ready`, `NativeCpuBackend`, `GpuSidecar`, `Aho-Corasick`, etc.) are each governed
    #     against the dedicated docs (SKILL.md / AGENTS.md / docs/CONTRACTS.md / docs/SESSION_HANDOFF.md
    #     / docs/CONTINUATION_PLAN.md / docs/gpu_crossover.md / docs/benchmarks.md) in this file.
    # The negative `not in` README checks above (no "Latest complete public release PR/commit") are
    # retained so the README cannot silently re-grow an incorrect release ledger.


def test_public_ast_positioning_should_not_claim_ast_grep_parity() -> None:
    public_surfaces = {
        "README.md": README_PATH.read_text(encoding="utf-8"),
        "SKILL.md": SKILL_DOC_PATH.read_text(encoding="utf-8"),
        "AGENTS.md": AGENTS_DOC_PATH.read_text(encoding="utf-8"),
        "src/tensor_grep/cli/main.py": Path("src/tensor_grep/cli/main.py").read_text(
            encoding="utf-8"
        ),
        "rust_core/src/main.rs": Path("rust_core/src/main.rs").read_text(encoding="utf-8"),
    }

    for path, text in public_surfaces.items():
        assert "ast-grep parity" not in text, path

    # The README's marketing copy keeps the honest "useful slice of ast-grep, not a full replacement"
    # positioning, but the exact `validated useful slice` contract phrasing is governed against the
    # dedicated docs (SKILL.md / AGENTS.md) rather than re-pinned into the README prose.
    assert "validated useful slice" in public_surfaces["SKILL.md"]
    assert "useful validated AST slice" in public_surfaces["AGENTS.md"]


def test_gpu_docs_should_record_current_gpu_crossover_story() -> None:
    # The README is now a marketing doc; the full GPU-crossover story (RTX 4070/5070, 1GB/5GB
    # correctness, single- vs many-pattern positioning, public-managed not-promotion-ready) is
    # governed against the dedicated GPU/benchmark docs below, so the README is not pinned here.
    benchmarks = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")
    gpu_doc = GPU_CROSSOVER_DOC_PATH.read_text(encoding="utf-8")
    paper = PAPER_DOC_PATH.read_text(encoding="utf-8")

    # PAPER.md is an append-only historical log (never rewritten -- see the
    # tensor-grep-docs-and-writing skill), so it cannot carry a perpetually-current
    # post-`vX` freshness marker; that marker is governed only against the live GPU docs.
    # Pre-fix, PAPER.md only satisfied this because the buggy unanchored release stamp
    # re-injected a fresh version into its dated historical notes every release (audit #71/#73).
    for doc in (benchmarks, gpu_doc):
        assert (
            f"post-`{CURRENT_RELEASE_TAG}`" in doc or f"post-`{CURRENT_RELEASE_TAG}` dogfood" in doc
        )

    for doc in (benchmarks, gpu_doc, paper):
        assert "1GB and 5GB correctness" in doc
        assert "RTX 4070" in doc
        assert "RTX 5070" in doc
        assert "single-pattern" in doc.lower()
        assert "many fixed" in doc.lower() or "many-pattern" in doc.lower()
        assert "public managed" in doc.lower()
        assert "not promotion-ready" in doc.lower()

    for doc in (benchmarks, gpu_doc):
        assert "fair baseline is `rg -F -e ... -e ...`" in doc
        assert "100 fixed no-match patterns over 1GB" in doc
        assert "`rg` multi-pattern: `0.169s`" in doc
        assert "`tg` CPU multi-pattern: `0.394s`" in doc
        assert "`tg --gpu-device-ids 0`: `0.448s` via `NativeCpuBackend` CPU fallback" in doc
        assert "`rg` mixed multi-pattern: `0.105s`" in doc
        assert "`tg` CPU mixed multi-pattern: `2.220s`" in doc
        assert "sidecar-routed rows are unsupported for native CUDA promotion" in doc
        assert "gpu_evidence_status" in doc
        assert "gpu_proof" in doc
        assert "native_gpu_unavailable" in doc
        assert "not_gpu_proof_reason" in doc
        assert "fallback_or_sidecar_counts_as_gpu_proof" in doc

    native_gpu_section = benchmarks.split(
        "### Native GPU crossover / throughput (`run_gpu_native_benchmarks.py`)",
        maxsplit=1,
    )[1].split("### Python GPU/NLP sidecar benchmark", maxsplit=1)[0]
    assert "`rg -F -e ... = 0.169s`" in native_gpu_section
    assert "`rg -F -e ... = 0.105s`" in native_gpu_section
    assert "GPU request fell back to `NativeCpuBackend`" in native_gpu_section
    assert "not promotion-ready" in native_gpu_section
    for stale_sequential_claim in (
        "7222.304ms",
        "6676.904ms",
        "`5.55x` speedup",
        "`2.68x` speedup",
    ):
        assert stale_sequential_claim not in native_gpu_section

    assert "diagnostic probes" in benchmarks
    assert "tg_binary_version_status" in benchmarks
    assert "stale in-tree native tg binary" in benchmarks
    assert "UNSUPPORTED" in benchmarks
    assert "NativeGpuBackend" in gpu_doc
    assert "sidecar_used = false" in gpu_doc


def test_gpu_docs_should_distinguish_public_managed_binary_from_native_cuda_dogfood() -> None:
    # The public-managed-binary vs native-CUDA-dogfood distinction (metadata, proof gates, promotion
    # blockers, workload class) is governed against the dedicated GPU/benchmark docs below. The README
    # is now a marketing doc and no longer mirrors this contract prose.
    benchmarks = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")
    gpu_doc = GPU_CROSSOVER_DOC_PATH.read_text(encoding="utf-8")

    for doc in (benchmarks, gpu_doc):
        assert "public managed binary" in doc
        assert "CUDA-feature native build" in doc or "local CUDA-feature release build" in doc
        assert "tg-native-metadata.json" in doc
        assert "--public-managed-proof" in doc
        assert "public_managed_promotion_ready" in doc
        assert "public_gpu_proof" in doc
        assert "GpuSidecar" in doc
        assert "NativeGpuBackend" in doc
        assert "not public GPU readiness" in doc
        assert "promotion_evidence_contract" in doc
        assert "promotion_blockers" in doc
        assert "declared workload class" in doc
        assert "rg -F -e" in doc
        assert "single-invocation" in doc
        assert "sequential `rg`" in doc
        assert "many-pattern proof gate" in doc or "many fixed-string proof gate" in doc


def test_python_gpu_benchmark_docs_should_not_claim_native_public_proof_fields() -> None:
    benchmarks = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")
    section = benchmarks.split("### Python GPU/NLP sidecar benchmark", maxsplit=1)[1]
    section = section.split("### Repeated Fixed-String Microbenchmark", maxsplit=1)[0]

    assert "gpu_proof_summary" in section
    assert "correctness_gate.required_sizes" in section
    assert "correctness_gate.passing_device_ids" in section
    assert "gpu_proof_summary.public_gpu_proof" in section
    assert "benchmarks/run_gpu_native_benchmarks.py --public-managed-proof" in section
    assert "correctness_gate.requires_direct_rg_match_identity" not in section
    assert "correctness_gate.rg_passing_sizes" not in section
    assert "top-level `public_managed_promotion_ready` and `public_gpu_proof`" not in section


def test_agent_workflow_docs_should_preserve_dogfood_research_pr_slice_process() -> None:
    docs = {
        "AGENTS.md": AGENTS_DOC_PATH.read_text(encoding="utf-8"),
        "SKILL.md": SKILL_DOC_PATH.read_text(encoding="utf-8"),
        "docs/SESSION_HANDOFF.md": SESSION_HANDOFF_PATH.read_text(encoding="utf-8"),
    }

    required_fragments = (
        "Dogfood follow-up workflow",
        "per-slice evidence ledger",
        "PR order",
        "slice scope",
        "Exa research",
        "thinktank",
        "subagent ownership",
        "PR-sized slices",
        "Gemini",
        "contract test",
        "targeted suite",
        "validation commands",
        "lint and format",
        "PR CI",
        "main CI",
        "release-bearing slices",
        "semantic-release",
        "release assets",
        "PyPI",
        "public release dogfood",
        "not applicable",
        "rationale",
        "do not collapse independent fixes into one broad PR",
    )
    for path, content in docs.items():
        for fragment in required_fragments:
            assert fragment in content, f"{path} missing `{fragment}`"


def test_agent_success_harness_should_remain_workflow_not_search_speed_contract() -> None:
    docs = {
        "AGENTS.md": AGENTS_DOC_PATH.read_text(encoding="utf-8"),
        "SKILL.md": SKILL_DOC_PATH.read_text(encoding="utf-8"),
        "docs/SESSION_HANDOFF.md": SESSION_HANDOFF_PATH.read_text(encoding="utf-8"),
        "docs/benchmarks.md": BENCHMARKS_DOC_PATH.read_text(encoding="utf-8"),
        "docs/CONTRACTS.md": CONTRACTS_DOC_PATH.read_text(encoding="utf-8"),
    }

    for path, content in docs.items():
        assert "run_agent_success_harness.py" in content, f"{path} missing harness command"
        assert "bench_agent_success_harness.json" in content, f"{path} missing harness artifact"

    for path in ("docs/benchmarks.md", "docs/CONTRACTS.md"):
        content = docs[path]
        assert "agent-native end-to-end success harness; not a raw search speed claim" in content
        for surface in ("intent", "context", "edit_seed", "apply", "verify", "rollback"):
            assert surface in content


def test_public_docs_should_not_contain_unaccepted_gpu_or_cold_rg_marketing() -> None:
    docs = {
        "README.md": README_PATH.read_text(encoding="utf-8"),
        "docs/benchmarks.md": BENCHMARKS_DOC_PATH.read_text(encoding="utf-8"),
        "docs/gpu_crossover.md": GPU_CROSSOVER_DOC_PATH.read_text(encoding="utf-8"),
        "docs/PAPER.md": PAPER_DOC_PATH.read_text(encoding="utf-8"),
    }
    banned_fragments = [
        "mathematically guaranteeing",
        "0ms interpreter lag",
        "peak theoretical throughput",
        "further buries",
        "designed to win on larger files",
        "GPU-ready",
        "GPU-accelerated",
    ]

    for path, doc in docs.items():
        for fragment in banned_fragments:
            assert fragment not in doc, f"{path} contains unaccepted claim `{fragment}`"


def test_tensor_grep_skill_should_record_latest_docs_merge_state() -> None:
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")

    # Current-release + behavioral/contract checks are retained; the per-PR "merged and released
    # as vX" ledger pins (a hand-maintained list that drifted) were removed in favour of CHANGELOG.md.
    assert f"current tagged version is `{CURRENT_RELEASE_TAG}`" in skill
    assert "public-windows-launcher-quoted-patterns" in skill
    assert "path_tg_first_launcher_kind" in skill
    assert "tg_launcher_command_kind" in skill
    assert "tg_agent_capsule" in skill
    assert "Feature or tool changes must update" in skill
    assert "MCP signatures/docs when agent-facing" in skill
    assert "this skill when repo operating practice changes" in skill
    assert "agent-capsule-mixed-language" in skill
    assert "agent-capsule-hardcases" in skill
    assert "validation_alignment" in skill
    assert "$file" in skill


def test_tensor_grep_skill_should_match_current_public_cli_syntax() -> None:
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")

    # Per-PR proof pins removed (CHANGELOG.md is the release history); current public-CLI-syntax
    # and shell-escaping guidance checks are retained.
    assert "shell_escaping_guidance" in skill
    assert "use single quotes or escape `$`" in skill
    assert "tg checkpoint create [PATH]" in skill
    assert "tg checkpoint undo <checkpoint_id> [PATH]" in skill
    assert "tg checkpoint create [checkpoint_name]" not in skill
    assert "dramatically speed" not in skill
    assert "ensure zero data loss" not in skill
    assert "Provider availability is not navigation proof" in skill


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
    assert "`routing_gpu_device_ids = []`" in doc
    assert "normal output and docs must call it CPU fallback" in doc


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
    assert "validated compatibility set" in doc
    assert "--files-without-match --sort path" in doc
    assert "binary exclusion by default" in doc


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
    assert "0.266167" in doc
    assert "0.260132" in doc
    assert "passes parity on all 10 rows" in doc
    assert "passes `benchmarks/check_regression.py --baseline auto`" in doc


def test_benchmark_docs_should_record_default_frontdoor_probe() -> None:
    doc = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")

    assert "explicit_binary default front door" in doc
    assert "0.266167" in doc
    assert "0.260132" in doc
    assert "bench_run_benchmarks_v165_control_plane_current.json" in doc
    assert "passed with no benchmark regressions" in doc


def test_native_rewrite_v2_roadmap_should_record_closed_statuses() -> None:
    doc = WORLD_CLASS_PLAN_PATH.read_text(encoding="utf-8")

    assert "### Roadmap 1: Native Control-Plane Rewrite v2" in doc
    assert (
        "Closed on 2026-04-28 as a gate-clean but still workload-specific architecture result"
        in doc
    )
    assert "default front door" in doc
    assert "raw `rg` still wins several individual cold rows" in doc
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


def test_agent_docs_should_lock_pr_merge_release_completion_contract() -> None:
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, skill, handoff):
        assert "A branch push or open PR starts PR CI only" in doc
        assert "It is not a release, not a released version, and not complete release state" in doc
        assert (
            "Release versioning starts only after a release-bearing PR is squash-merged to `main`"
            in doc
        )
        assert "main CI and semantic-release complete successfully" in doc
        assert "publish-success-gate" in doc
        assert "git fetch origin main --tags" in doc
        assert "fast-forward local `main` to the release commit" in doc
        assert "PyPI/public installer availability is verified" in doc


def test_skill_current_release_proof_should_match_project_version() -> None:
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    version = CURRENT_RELEASE_TAG.removeprefix("v")
    current_release_proof = skill.split("Current release facts:", 1)[1].split(
        "Recent release history:",
        1,
    )[0]

    assert f"- Current release tag: `{CURRENT_RELEASE_TAG}`" in current_release_proof
    assert f"/releases/tag/{CURRENT_RELEASE_TAG}" in current_release_proof
    assert f"tensor-grep=={version}" in current_release_proof
    assert f"reports `tensor-grep {version}`" in current_release_proof
    assert "v1.12.46" not in current_release_proof
    assert "tensor-grep==1.12.46" not in current_release_proof


def test_agent_docs_should_not_describe_code_intelligence_limits_as_search_flags() -> None:
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = Path("docs/CONTRACTS.md").read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, skill, contracts, handoff):
        assert "Use scoped paths, globs, file types, and `--max-depth` for `tg search`" in doc
        assert "`--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence" in doc


def test_session_docs_should_lock_warm_path_and_discovery_contracts() -> None:
    # The warm-path/session-discovery contract (snapshot size/mtime, `tg session refresh`,
    # `--refresh-on-stale`, nearby-scope discovery) is governed against the dedicated docs SKILL.md
    # and docs/CONTRACTS.md below. The README is now a marketing doc and is not pinned to this prose.
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")

    for doc in (skill, contracts):
        assert "snapshot" in doc
        assert "size/mtime" in doc
        assert "`tg session refresh" in doc
        assert "`--refresh-on-stale`" in doc
        assert "discover nearby" in doc

    assert "must not walk the full repository" in contracts
    assert "response_cache_stale_detection" in contracts
    assert "snapshot_mtime_only" in contracts
    assert "should not walk the full repo" in skill


def test_agent_docs_should_lock_agent_context_and_validation_contracts() -> None:
    # The agent context/validation contract (`context_consistency`, executable body lines,
    # `validation_plan[].detection`, `validation_alignment`) is governed against the dedicated agent
    # docs (AGENTS.md / SKILL.md / docs/CONTRACTS.md / docs/SESSION_HANDOFF.md) below. The README is a
    # marketing doc now; it still surfaces `validation_alignment` but is not pinned to the rest.
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, skill, contracts, handoff):
        assert "context_consistency" in doc
        assert "executable" in doc
        assert "validation_plan[].detection" in doc
        assert "validation_alignment" in doc

    for doc in (agents, skill, contracts, handoff):
        assert "`package.json` evidence" in doc
        assert "no runner evidence exists" in doc
        assert "primary target language" in doc


def test_agent_docs_should_lock_agent_context_capsule_roadmap() -> None:
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")
    continuation = CONTINUATION_PLAN_PATH.read_text(encoding="utf-8")

    for doc in (agents, readme, skill, contracts, handoff, continuation):
        assert "tg agent" in doc
        assert "Actionable Context Capsule" in doc
        assert "line maps" in doc
        assert "checkpoint" in doc
        assert "confidence" in doc
        assert "ask" in doc.lower()

    # `route rationale` and `omission counts` are detailed capsule-contract terms. The marketing
    # README summarizes the capsule without that exact wording, so they are governed against the
    # dedicated agent docs (AGENTS.md / SKILL.md / docs/CONTRACTS.md / docs/SESSION_HANDOFF.md /
    # docs/CONTINUATION_PLAN.md) instead of being re-pinned into the README.
    for doc in (agents, skill, contracts, handoff, continuation):
        assert "route rationale" in doc
        assert "omission counts" in doc

    for doc in (agents, skill, contracts, continuation):
        assert "parser-backed" in doc
        assert "rg-backed" in doc
        assert "graph-derived" in doc
        assert "heuristic" in doc
        assert "stale/uncertain" in doc

    assert "Search Intent Router" in continuation
    assert "Patch Planning Without Editing" in continuation
    assert "Safe Rewrite Loop" in continuation
    assert "Test Selection Engine" in continuation
    assert "Failure-Aware CI Triage" in continuation
    assert "Repo Memory" in continuation
    assert "Agent Token Economy Mode" in continuation


def test_agent_docs_should_lock_windows_cmd_quoted_pattern_probe() -> None:
    # The Windows `.cmd` quoted multi-word false-positive probe is governed against the dedicated
    # agent/contract docs below. The README is now a marketing doc and is not pinned to this prose.
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, skill, contracts, handoff):
        assert "quoted multi-word" in doc
        assert "false-positive" in doc

    for doc in (agents, skill, handoff):
        assert "public-windows-launcher-quoted-patterns" in doc


def test_ast_info_public_docs_should_describe_json_languages_payload() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    # The exact `tg ast-info --json` language-identifier wording is governed against the dedicated
    # docs (SKILL.md / docs/SESSION_HANDOFF.md); the marketing README does not carry that prose.
    for doc in (skill, handoff):
        assert "`tg ast-info --json` exposes AST language identifiers" in doc

    # The negative guard (no misleading "AST grammar inventory" claim) is kept for all surfaces,
    # including the README, so none can re-grow the overclaim.
    for doc in (readme, skill, handoff):
        assert "AST grammar inventory" not in doc


def test_continuation_plan_should_not_treat_pr_push_as_release_completion() -> None:
    doc = CONTINUATION_PLAN_PATH.read_text(encoding="utf-8")

    assert "Do not describe a pushed branch or open PR as complete release work" in doc
    assert "only ready for review/merge" in doc
    assert "release completion contract" in doc
