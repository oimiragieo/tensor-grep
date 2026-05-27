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
VERIFIED_RELEASE_TAG = "v1.13.22"
VERIFIED_RELEASE_COMMIT = "5a2ad6b chore(release): v1.13.22 [skip ci]"
VERIFIED_FIX_COMMIT = "995b414 fix: harden v1.13.21 dogfood contracts"
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
LATEST_VERIFIED_MAIN_CI = "26473492381"
LATEST_VERIFIED_CODEQL = "26473490540"


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
    assert "tg_agent_capsule" in readme
    assert "native CPU engine" in readme
    assert "native GPU engine" in readme
    assert "benchmark-governed" in readme
    assert "100 MB" in readme or "100MB" in readme
    assert "tg run --rewrite" in readme
    assert "--apply" in readme
    assert "atomic temp-file rename contract" in readme
    assert "multi-project workspace roots" in readme
    assert "broad generated-root scan" in readme
    assert "PowerShell double quotes expand `$NAME`" in readme
    assert "cmd.exe metacharacters" in readme
    assert "open a session once" in readme
    assert "daemon-routed edit-plan/context" in readme


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

    for content in docs.values():
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

    for content in (
        docs["AGENTS.md"],
        docs["SKILL.md"],
        docs["docs/SESSION_HANDOFF.md"],
        docs["docs/CONTINUATION_PLAN.md"],
    ):
        assert CURRENT_RELEASE_COMMIT in content
        assert CURRENT_FIX_COMMIT in content
        assert CURRENT_GPU_FIX_COMMIT in content
        assert CURRENT_DOCS_STAMP_FIX_COMMIT in content
        assert CURRENT_MULTIPATTERN_FIX_COMMIT in content
        assert CURRENT_FEATURE_COMMIT in content

    handoff = docs["docs/SESSION_HANDOFF.md"]
    assert f"- Latest tagged version: `{CURRENT_RELEASE_TAG}`" in handoff
    assert f"- Latest complete PyPI version: `{LATEST_COMPLETE_RELEASE_TAG}`" in handoff
    assert (
        f"GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/{CURRENT_RELEASE_TAG}>"
        in handoff
    )
    assert "publish-success-gate` failed" in handoff
    assert "PyPI latest remains `1.10.10`" in handoff
    assert LATEST_VERIFIED_MAIN_CI in handoff
    assert LATEST_VERIFIED_CODEQL in handoff
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

    readme = docs["README.md"]
    assert "## Current Release State" in readme
    assert CURRENT_FIX_COMMIT in readme
    assert CURRENT_FEATURE_COMMIT in readme
    assert CURRENT_RELEASE_COMMIT in readme
    assert LATEST_VERIFIED_MAIN_CI in readme
    assert LATEST_VERIFIED_CODEQL in readme
    assert LATEST_COMPLETE_RELEASE_COMMIT in readme
    assert LATEST_COMPLETE_FIX_COMMIT in readme
    assert CURRENT_GPU_FIX_COMMIT in readme
    assert CURRENT_DOCS_STAMP_FIX_COMMIT in readme
    assert CURRENT_MULTIPATTERN_FIX_COMMIT in readme
    assert f"GitHub release assets for `{LATEST_VERIFIED_RELEASE_TAG}`" in readme
    assert f"tensor-grep=={LATEST_VERIFIED_RELEASE_TAG.removeprefix('v')}" in readme
    assert "PyPI latest remains `1.10.10`" in readme
    assert "rust_binary_version_status = matches" in readme
    assert "native front door" in readme
    assert "fresh quoted no-match phrase" in readme
    assert "tg classify --format json" in readme
    assert "classification_backend" in readme
    assert "not a full ast-grep replacement" in readme
    assert "GPU remains opt-in/experimental" in readme
    assert "Default `classify` is now deterministic and local" in readme
    assert "top-level `validation_commands`" in readme
    assert "local deterministic classifications" in readme
    assert "path_tg_first_launcher_kind" in readme
    assert "tg_launcher_command_kind" in readme
    assert "only initialize selected devices" in readme
    assert "Actionable Context Capsule" in readme
    assert "validation_alignment" in readme
    assert "public managed GPU is not promotion-ready" in readme
    assert "tg search --json` is tensor-grep aggregate JSON" in readme

    current_closed_heading = f"What `{LATEST_VERIFIED_RELEASE_TAG}` closed:"
    v1114_heading = "What `v1.11.4` closed:"
    v1113_heading = "What `v1.11.3` closed:"
    v1112_heading = "What `v1.11.2` closed:"
    v1111_heading = "What `v1.11.1` closed:"
    v1110_failed_heading = "What `v1.11.0` tagged but did not complete:"
    v11010_heading = "What `v1.10.10` closed:"
    v1109_heading = "What `v1.10.9` closed:"
    v1108_heading = "What `v1.10.8` closed:"
    v1107_heading = "What `v1.10.7` closed:"
    v1106_heading = "What `v1.10.6` closed:"
    v1105_heading = "What `v1.10.5` closed:"
    v1100_heading = "What `v1.10.0` closed:"
    v1911_heading = "What `v1.9.11` closed:"
    v1910_heading = "What `v1.9.10` closed:"
    v199_heading = "What `v1.9.9` closed:"
    v198_heading = "What `v1.9.8` closed:"
    v197_heading = "What `v1.9.7` closed:"
    v196_heading = "What `v1.9.6` closed:"
    v195_heading = "What `v1.9.5` closed:"
    v194_heading = "What `v1.9.4` closed:"
    v193_heading = "What `v1.9.3` closed:"
    v192_heading = "What `v1.9.2` closed:"
    follow_up_heading = f"Active post-`{CURRENT_RELEASE_TAG}` follow-up:"
    current_closed_block = readme.split(current_closed_heading, 1)[1].split(v1114_heading, 1)[0]
    v1114_closed_block = readme.split(v1114_heading, 1)[1].split(v1113_heading, 1)[0]
    v1113_closed_block = readme.split(v1113_heading, 1)[1].split(v1112_heading, 1)[0]
    v1112_closed_block = readme.split(v1112_heading, 1)[1].split(v1111_heading, 1)[0]
    v1111_closed_block = readme.split(v1111_heading, 1)[1].split(v1110_failed_heading, 1)[0]
    v1110_failed_block = readme.split(v1110_failed_heading, 1)[1].split(v11010_heading, 1)[0]
    v11010_closed_block = readme.split(v11010_heading, 1)[1].split(v1109_heading, 1)[0]
    v1109_closed_block = readme.split(v1109_heading, 1)[1].split(v1108_heading, 1)[0]
    v1108_closed_block = readme.split(v1108_heading, 1)[1].split(v1107_heading, 1)[0]
    v1107_closed_block = readme.split(v1107_heading, 1)[1].split(v1106_heading, 1)[0]
    v1106_closed_block = readme.split(v1106_heading, 1)[1].split(v1105_heading, 1)[0]
    v1105_closed_block = readme.split(v1105_heading, 1)[1].split(v1100_heading, 1)[0]
    v1100_closed_block = readme.split(v1100_heading, 1)[1].split(v1911_heading, 1)[0]
    v1911_closed_block = readme.split(v1911_heading, 1)[1].split(v1910_heading, 1)[0]
    v1910_closed_block = readme.split(v1910_heading, 1)[1].split(v199_heading, 1)[0]
    v199_closed_block = readme.split(v199_heading, 1)[1].split(v198_heading, 1)[0]
    v198_closed_block = readme.split(v198_heading, 1)[1].split(v197_heading, 1)[0]
    v197_closed_block = readme.split(v197_heading, 1)[1].split(v196_heading, 1)[0]
    v196_closed_block = readme.split(v196_heading, 1)[1].split(v195_heading, 1)[0]
    v195_closed_block = readme.split(v195_heading, 1)[1].split(v194_heading, 1)[0]
    v194_closed_block = readme.split(v194_heading, 1)[1].split(v193_heading, 1)[0]
    v192_closed_block = readme.split(v192_heading, 1)[1].split("What `v1.9.1` closed:", 1)[0]
    follow_up_block = readme.split(follow_up_heading, 1)[1]
    assert "post-release-safe docs governance" in current_closed_block
    assert "current tag labels" in current_closed_block
    assert "native GPU unavailable" in v1114_closed_block
    assert "NativeCpuBackend" in v1114_closed_block
    assert "fixed multi-pattern" in v1113_closed_block
    assert "Aho-Corasick" in v1113_closed_block
    assert "100 fixed no-match patterns over 1GB" in v1113_closed_block
    assert "classify provider provenance" in v1112_closed_block
    assert "classification_backend" in v1112_closed_block
    assert "tg classify --format json" in v1112_closed_block
    assert "agent capsule hardcases" in v1111_closed_block
    assert "implementation files outrank preview/mention files" in v1111_closed_block
    assert "release docs governance" in v1111_closed_block
    assert "publish-success-gate" in v1110_failed_block
    assert "PyPI latest remains `1.10.10`" in v1110_failed_block
    assert "GpuSidecar" in v11010_closed_block
    assert "subprocess.run" in v11010_closed_block
    assert "repair-launcher" in v11010_closed_block
    assert "agent-native code intelligence" in v1109_closed_block
    assert "release docs/governance" in v1109_closed_block
    assert "GpuSidecar" in v1108_closed_block
    assert "subprocess.run" in v1108_closed_block
    assert "native GPU search" in v1107_closed_block
    assert "smart-case" in v1107_closed_block
    assert "ambiguous invoice-task routing" in v1106_closed_block
    assert "CWD-is-generated-root" in v1106_closed_block
    assert "GpuSidecar" in v1106_closed_block
    assert "publish-success-gate" in v1106_closed_block
    assert "hot-query regex repeats" in v1105_closed_block
    assert "agentic GPU route evidence" in v1100_closed_block
    assert "sidecar-routed GPU evidence is reported as unsupported" in v1100_closed_block
    assert "release wheel retry" in v1911_closed_block
    assert "Cargo dependency prefetch" in v1911_closed_block
    assert "capsule alternative target confidence" in v1910_closed_block
    assert "provider tokens" in v1910_closed_block
    assert "transient crates.io DNS failure" in v1910_closed_block
    assert "agent workflow benchmark governance" in v199_closed_block
    assert "run_agent_workflow_benchmarks.py" in v199_closed_block
    assert "tensor-grep 1.9.9" in v199_closed_block
    assert "stale tensor-grep-owned `tg.com`" in v198_closed_block
    assert "Windows `PATHEXT`" in v198_closed_block
    assert "fresh `cmd` and unprofiled `pwsh` report `tg 1.9.8`" in v198_closed_block
    assert "Python GPU scale rows are unsupported" in v197_closed_block
    assert "Native CUDA correctness passed" in v197_closed_block
    assert "cold exact text" in v197_closed_block
    assert "sidecar" in v196_closed_block
    assert "foreign" in v196_closed_block
    assert "GPU native gate attribution" in v195_closed_block
    assert "$file" in v194_closed_block
    assert "docs-governance tests" in v194_closed_block
    assert "--diff --json" in v192_closed_block
    assert "rolls changed files back" in v192_closed_block
    assert "GPU benchmark auto-recommendation disabled" in follow_up_block
    assert "subprocess.run" in follow_up_block


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

    assert "validated useful slice" in public_surfaces["README.md"]
    assert "validated useful slice" in public_surfaces["SKILL.md"]
    assert "useful validated AST slice" in public_surfaces["AGENTS.md"]


def test_gpu_docs_should_record_current_gpu_crossover_story() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    benchmarks = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")
    gpu_doc = GPU_CROSSOVER_DOC_PATH.read_text(encoding="utf-8")
    paper = PAPER_DOC_PATH.read_text(encoding="utf-8")

    for doc in (readme, benchmarks, gpu_doc, paper):
        assert (
            f"post-`{CURRENT_RELEASE_TAG}`" in doc or f"post-`{CURRENT_RELEASE_TAG}` dogfood" in doc
        )
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

    assert "diagnostic probes" in benchmarks
    assert "tg_binary_version_status" in benchmarks
    assert "stale in-tree native tg binary" in benchmarks
    assert "UNSUPPORTED" in benchmarks
    assert "NativeGpuBackend" in gpu_doc
    assert "sidecar_used = false" in gpu_doc


def test_gpu_docs_should_distinguish_public_managed_binary_from_native_cuda_dogfood() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    benchmarks = BENCHMARKS_DOC_PATH.read_text(encoding="utf-8")
    gpu_doc = GPU_CROSSOVER_DOC_PATH.read_text(encoding="utf-8")

    for doc in (readme, benchmarks, gpu_doc):
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


def test_skill_ledger_should_record_root_forwarding_release_proof() -> None:
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    ledger_lines = [
        line
        for line in skill.splitlines()
        if "PR order: 6;" in line
        and "preserve root `tg` shortcut forwarding" in line
        and "`tg --count-matches PATTERN PATH`" in line
    ]
    assert len(ledger_lines) == 1

    ledger = ledger_lines[0]
    assert "pending" not in ledger
    assert "Gemini review: PASS" in ledger
    assert "PR #185 passed" in ledger
    assert "main CI run `26260569216` passed" in ledger
    assert "semantic-release published `v1.12.50`" in ledger
    assert "PyPI/uvx public install proof verified" in ledger


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

    assert (
        "Latest merged docs/product commit: `f311469 docs: define agent context capsule roadmap`"
        in skill
    )
    assert "PR #66 `docs: define agent context capsule roadmap` merged" in skill
    assert "Main CI run `25561521904` passed" in skill
    assert "CodeQL/dynamic main run `25561520180` passed" in skill
    assert "semantic-release correctly skipped publishing" in skill
    assert f"current tagged version is `{CURRENT_RELEASE_TAG}`" in skill
    assert "PR #101 `fix: harden gpu search accuracy contracts` merged" in skill
    assert "PR #100 `fix: harden v1.10.5 dogfood blockers` merged" in skill
    assert CURRENT_RELEASE_COMMIT in skill
    assert LATEST_VERIFIED_MAIN_CI in skill
    assert LATEST_VERIFIED_CODEQL in skill
    assert LATEST_COMPLETE_RELEASE_COMMIT in skill
    assert "PR #91 `fix: harden release wheel retries` merged" in skill
    assert "PR #90 `fix: harden v1.9.9 dogfood followups` merged" in skill
    assert "PR #89 `fix: add agent workflow benchmark governance` merged" in skill
    assert "PR #87 `fix: refresh stale tg.com bridge after upgrade` merged" in skill
    assert "PR #86 `fix: clarify GPU benchmark promotion gates` merged" in skill
    assert "PR #83 `fix: harden GPU gates and launcher diagnostics` merged" in skill
    assert "PR #82 `fix: harden docs governance and validation placeholders` merged" in skill
    assert "PR #81 `fix: harden agent ranking docs and validation quoting` merged" in skill
    assert "PR #80 `fix: harden edit JSON and capsule validation trust` merged" in skill
    assert "PR #78 `fix: harden agent capsule trust alignment` merged" in skill
    assert "PR #76 `feat: add actionable agent context capsule` merged" in skill
    assert "PR #74 `fix: scope GPU probing and benchmark launcher warnings` merged" in skill
    assert (
        "Previous agent-contract fix commit: `015fad9 fix: harden public launcher and agent contracts`"
        in skill
    )
    assert (
        "Previous launcher fix commit: `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`"
        in skill
    )
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

    assert "PR #182 `eea05c6 fix: restore dogfood docs claim wording (#182)`" in skill
    assert "Release commit: `9c538ba chore(release): v1.12.47 [skip ci]`" in skill
    assert "Main CI run `26236451411` passed" in skill
    assert "CodeQL/push run `26236447550` passed" in skill
    assert "PR #181 `524f6d4 fix: expose windows shell escaping diagnostics`" in skill
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
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")

    for doc in (readme, skill, contracts):
        assert "snapshot" in doc
        assert "size/mtime" in doc
        assert "`tg session refresh" in doc
        assert "`--refresh-on-stale`" in doc
        assert "discover nearby" in doc

    assert "must not walk the full repository" in contracts
    assert "should not walk the full repo" in skill


def test_agent_docs_should_lock_agent_context_and_validation_contracts() -> None:
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, readme, skill, contracts, handoff):
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
        assert "route rationale" in doc
        assert "line maps" in doc
        assert "checkpoint" in doc
        assert "omission counts" in doc
        assert "confidence" in doc
        assert "ask" in doc.lower()

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
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    contracts = CONTRACTS_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (agents, readme, skill, contracts, handoff):
        assert "quoted multi-word" in doc
        assert "false-positive" in doc

    for doc in (agents, skill, handoff):
        assert "public-windows-launcher-quoted-patterns" in doc


def test_ast_info_public_docs_should_describe_json_languages_payload() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    skill = SKILL_DOC_PATH.read_text(encoding="utf-8")
    handoff = SESSION_HANDOFF_PATH.read_text(encoding="utf-8")

    for doc in (readme, skill, handoff):
        assert "`tg ast-info --json` exposes AST language identifiers" in doc
        assert "AST grammar inventory" not in doc


def test_continuation_plan_should_not_treat_pr_push_as_release_completion() -> None:
    doc = CONTINUATION_PLAN_PATH.read_text(encoding="utf-8")

    assert "Do not describe a pushed branch or open PR as complete release work" in doc
    assert "only ready for review/merge" in doc
    assert "release completion contract" in doc
