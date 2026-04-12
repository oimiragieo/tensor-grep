from pathlib import Path

README_PATH = Path("README.md")
CONTRIBUTING_PATH = Path("CONTRIBUTING.md")
SUPPORT_MATRIX_PATH = Path("docs/SUPPORT_MATRIX.md")
CONTRACTS_PATH = Path("docs/CONTRACTS.md")
HOTFIX_PROCEDURE_PATH = Path("docs/HOTFIX_PROCEDURE.md")
INSTALLATION_PATH = Path("docs/installation.md")
RELEASE_CHECKLIST_PATH = Path("docs/RELEASE_CHECKLIST.md")
CI_PIPELINE_PATH = Path("docs/CI_PIPELINE.md")
EXPERIMENTAL_PATH = Path("docs/EXPERIMENTAL.md")
DOCS_INDEX_PATH = Path("docs/index.md")
MKDOCS_PATH = Path("mkdocs.yml")
RESIDENT_WORKER_RUNBOOK_PATH = Path("docs/runbooks/resident-worker.md")
GPU_RUNBOOK_PATH = Path("docs/runbooks/gpu-troubleshooting.md")
CACHE_RUNBOOK_PATH = Path("docs/runbooks/cache-management.md")


def test_readme_should_point_to_enterprise_contract_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/CI_PIPELINE.md" in readme
    assert "docs/SUPPORT_MATRIX.md" in readme
    assert "docs/CONTRACTS.md" in readme
    assert "docs/HOTFIX_PROCEDURE.md" in readme
    assert "docs/EXPERIMENTAL.md" in readme
    assert "## Future Work" in readme


def test_support_matrix_should_distinguish_ci_tested_from_best_effort() -> None:
    doc = SUPPORT_MATRIX_PATH.read_text(encoding="utf-8")

    assert "CI-tested" in doc
    assert "Best-effort" in doc
    assert "3.11" in doc
    assert "3.12" in doc
    assert "3.9" in doc
    assert "3.14" in doc
    assert "Apple Silicon" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_contracts_should_exclude_experimental_surface_from_stable_guarantees() -> None:
    doc = CONTRACTS_PATH.read_text(encoding="utf-8")

    assert "docs/EXPERIMENTAL.md" in doc
    assert "tg worker" in doc
    assert "TG_RESIDENT_AST" in doc
    assert "not covered by the stability guarantees" in doc


def test_installation_doc_should_describe_release_validated_channels() -> None:
    doc = INSTALLATION_PATH.read_text(encoding="utf-8")

    assert "Recommended Channel by Use Case" in doc
    assert "tg update" in doc
    assert "tg-windows-amd64-cpu.exe" in doc
    assert "tg-linux-amd64-cpu" in doc
    assert "tg-macos-amd64-cpu" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_hotfix_procedure_should_route_through_semantic_release() -> None:
    doc = HOTFIX_PROCEDURE_PATH.read_text(encoding="utf-8")

    assert "Do not manually create release tags" in doc
    assert "semantic-release" in doc
    assert "fix: correct <hotfix subject>" in doc
    assert "vX.Y.Z" in doc
    assert "vX.Y.(Z+1)" in doc


def test_contributing_should_match_semantic_release_flow() -> None:
    doc = CONTRIBUTING_PATH.read_text(encoding="utf-8")

    assert "semantic-release" in doc
    assert "Do not manually create release tags" in doc
    assert "`feat: ...` => minor release" in doc
    assert "`fix: ...` or `perf: ...` => patch release" in doc


def test_release_checklist_should_define_enterprise_ready_evidence() -> None:
    doc = RELEASE_CHECKLIST_PATH.read_text(encoding="utf-8")

    assert "## 0. Enterprise-ready evidence" in doc
    assert "SBOMs" in doc
    assert "provenance" in doc
    assert "docs/SUPPORT_MATRIX.md" in doc
    assert "docs/EXPERIMENTAL.md" in doc


def test_ci_pipeline_doc_should_explain_release_and_supply_chain_automation() -> None:
    doc = CI_PIPELINE_PATH.read_text(encoding="utf-8")

    assert "Semantic Release" in doc
    assert "Security Audit" in doc
    assert "Dependabot" in doc
    assert "auto-merge only for low-risk updates" in doc
    assert "[Security Audit] Scheduled dependency audit failure" in doc
    assert "scripts/validate_release_assets.py" in doc


def test_docs_index_should_point_to_current_product_contracts() -> None:
    doc = DOCS_INDEX_PATH.read_text(encoding="utf-8")

    assert "native search and rewrite tool" in doc
    assert "Rust-native CPU text search" in doc
    assert "docs/CI_PIPELINE.md" in doc
    assert "docs/benchmarks.md" in doc
    assert "docs/SUPPORT_MATRIX.md" in doc
    assert "GPU acceleration is benchmark-governed" in doc


def test_mkdocs_should_publish_current_repo_and_enterprise_nav() -> None:
    doc = MKDOCS_PATH.read_text(encoding="utf-8")

    assert "Native search and rewrite tool" in doc
    assert "https://github.com/oimiragieo/tensor-grep" in doc
    assert "CI Pipeline: CI_PIPELINE.md" in doc
    assert "Support Matrix: SUPPORT_MATRIX.md" in doc
    assert "Contracts: CONTRACTS.md" in doc
    assert "Experimental Features: EXPERIMENTAL.md" in doc


def test_experimental_docs_and_runbooks_should_warn_about_worker_support_boundary() -> None:
    experimental = EXPERIMENTAL_PATH.read_text(encoding="utf-8")
    runbook = RESIDENT_WORKER_RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "Not covered by the stable enterprise contract" in experimental
    assert "workload-dependent" in experimental
    assert "not part of the stable default enterprise surface" in runbook
    assert "tg worker --stop" in runbook


def test_operational_runbooks_should_include_windows_safe_commands() -> None:
    gpu = GPU_RUNBOOK_PATH.read_text(encoding="utf-8")
    cache = CACHE_RUNBOOK_PATH.read_text(encoding="utf-8")

    assert '$env:TG_FORCE_CPU = "1"' in gpu
    assert "Remove-Item -LiteralPath .tg_cache -Recurse -Force" in cache
