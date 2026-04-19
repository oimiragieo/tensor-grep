import importlib.util
import textwrap
from pathlib import Path


def test_should_validate_release_and_package_assets_consistency():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_all()
    assert errors == []


def test_should_require_readme_canonical_doc_links_and_release_markers():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = """
    # tensor-grep

    ## Canonical Docs

    - [docs/benchmarks.md](docs/benchmarks.md)
    - [docs/tool_comparison.md](docs/tool_comparison.md)
    - [docs/gpu_crossover.md](docs/gpu_crossover.md)
    """
    errors = module.validate_readme_contract(readme_content=readme)
    assert any("README missing canonical docs reference" in err for err in errors)
    assert any("README must link installation docs" in err for err in errors)
    assert any("README must link release checklist" in err for err in errors)


def test_should_accept_readme_when_public_contract_markers_exist():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = """
    # tensor-grep

    `tensor-grep` has first class support on Windows, macOS and Linux.

    Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md)
    and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).

    ## Canonical Docs

    - [docs/benchmarks.md](docs/benchmarks.md)
    - [docs/tool_comparison.md](docs/tool_comparison.md)
    - [docs/gpu_crossover.md](docs/gpu_crossover.md)
    - [docs/routing_policy.md](docs/routing_policy.md)
    - [docs/harness_api.md](docs/harness_api.md)
    - [docs/harness_cookbook.md](docs/harness_cookbook.md)
    - [docs/installation.md](docs/installation.md)
    - [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
    """
    errors = module.validate_readme_contract(readme_content=readme)
    assert errors == []


def test_should_require_benchmarks_doc_canonical_matrix_and_rules():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    benchmarks_doc = """
    # Benchmarks

    ## Benchmark Matrix

    | Surface | Script | Default artifact |
    | --- | --- | --- |
    | End-to-end CLI text search | `benchmarks/run_benchmarks.py` | `artifacts/bench_run_benchmarks.json` |
    """
    errors = module.validate_benchmarks_docs(benchmarks_content=benchmarks_doc)
    joined_errors = "\n".join(errors)
    assert "Benchmark docs missing required matrix contract" in joined_errors
    assert "Benchmark docs missing required artifact convention" in joined_errors
    assert "Benchmark docs missing required acceptance rule" in joined_errors


def test_should_accept_benchmarks_doc_when_public_benchmark_contract_exists():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    benchmarks_doc = """
    # Benchmarks

    ## Benchmark Matrix

    | Surface | Script | Default artifact |
    | --- | --- | --- |
    | End-to-end CLI text search | `benchmarks/run_benchmarks.py` | `artifacts/bench_run_benchmarks.json` |
    | Host-local CLI tool comparison | `benchmarks/run_tool_comparison_benchmarks.py` | `artifacts/bench_tool_comparison.json` |
    | AST rewrite plan/diff/apply | `benchmarks/run_ast_rewrite_benchmarks.py` | `artifacts/bench_ast_rewrite.json` |
    | Repeated-query / hot-cache search | `benchmarks/run_hot_query_benchmarks.py` | `artifacts/bench_hot_query_benchmarks.json` |

    ## Artifact Conventions

    - `suite`
    - `artifact`
    - `environment`
    - `generated_at_epoch_s`

    ## Acceptance Rules

    - Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.
    - Compare against the current accepted baseline, not memory.
    - Keep backend labels explicit in artifacts so routing claims are auditable.
    """
    errors = module.validate_benchmarks_docs(benchmarks_content=benchmarks_doc)
    assert errors == []


def test_should_validate_winget_manifest_structure():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    winget = (
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert errors == []


def test_should_fail_winget_manifest_when_installer_url_not_nested():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    winget = (
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert any("InstallerUrl must be nested under first installer mapping" in err for err in errors)


def test_should_require_ci_pypi_parity_retry_arguments():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - uses: astral-sh/setup-uv@v8.0.0
        - uses: astral-sh/setup-uv@v8.0.0
        - run: |
            python scripts/validate_release_version_parity.py
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("--pypi-wait-seconds" in err for err in errors)
    assert any("--pypi-poll-interval-seconds" in err for err in errors)


def test_should_require_dependabot_config_targets_and_branch_separator():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    dependabot = """
    version: 2
    updates:
      - package-ecosystem: "uv"
        directory: "/"
        schedule:
          interval: "daily"
    """
    errors = module.validate_dependabot_config(dependabot_content=textwrap.dedent(dependabot))
    joined_errors = "\n".join(errors)
    assert "pull-request-branch-name.separator" in joined_errors
    assert "missing required update target `github-actions`" in joined_errors
    assert "schedule weekly checks" in joined_errors


def test_should_require_dependabot_automation_automerge_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = """
    name: Dependabot Automation
    on:
      pull_request_target:
        types: [opened]
    permissions:
      contents: write
      pull-requests: write
      issues: write
    jobs:
      dependabot-triage:
        if: github.actor == 'dependabot[bot]'
        runs-on: ubuntu-latest
        steps:
          - name: Fetch Dependabot metadata
            uses: dependabot/fetch-metadata@d7267f607e9d3fb96fc2fbe83e0af444713e90b7
          - name: Enable auto-merge for safe updates
            run: gh pr merge --squash "$PR_URL"
    """
    errors = module.validate_dependabot_automation_workflow_content(
        workflow_content=textwrap.dedent(workflow)
    )
    joined_errors = "\n".join(errors)
    assert "Ensure dependency labels exist" in joined_errors
    assert "Approve safe updates" in joined_errors
    assert "gh pr merge --auto --squash" in joined_errors


def test_should_require_audit_workflow_issue_remediation_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = """
    name: Security Audit
    on:
      schedule:
        - cron: '0 0 * * *'
      pull_request:
        branches: [main]
      workflow_dispatch:
    jobs:
      audit:
        name: Dependency & License Audit
        runs-on: ubuntu-latest
        steps:
          - run: cargo audit
          - run: cargo deny check
          - run: uv run pip-audit
      report-audit-status:
        if: github.event_name == 'schedule'
        needs: audit
        runs-on: ubuntu-latest
        permissions:
          contents: read
        steps:
          - name: Create or update scheduled audit issue on failure
            uses: actions/github-script@v7
          - name: Close scheduled audit issue on success
            uses: actions/github-script@v7
    """
    errors = module.validate_audit_workflow_content(workflow_content=textwrap.dedent(workflow))
    joined_errors = "\n".join(errors)
    assert "if: always()" in joined_errors
    assert "issues: write" in joined_errors
    assert "actions/github-script@v8" in joined_errors


def test_should_require_audit_workflow_managed_issue_title_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = """
    name: Security Audit
    jobs:
      audit:
        name: Dependency & License Audit
        runs-on: ubuntu-latest
        steps:
          - run: cargo audit
          - run: cargo deny check
          - run: uv run pip-audit
      report-audit-status:
        if: always() && github.event_name == 'schedule'
        needs: audit
        runs-on: ubuntu-latest
        permissions:
          contents: read
          issues: write
        steps:
          - name: Create or update scheduled audit issue on failure
            uses: actions/github-script@v8
            with:
              script: |
                const title = "audit failed";
          - name: Close scheduled audit issue on success
            uses: actions/github-script@v8
            with:
              script: |
                const title = "audit failed";
    """
    errors = module.validate_audit_workflow_content(workflow_content=textwrap.dedent(workflow))
    joined_errors = "\n".join(errors)
    assert "[Security Audit] Scheduled dependency audit failure" in joined_errors


def test_should_require_audit_workflow_uv_environment_before_pip_audit_install():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = """
    name: Security Audit
    jobs:
      audit:
        name: Dependency & License Audit
        runs-on: ubuntu-latest
        steps:
          - name: Install uv
            uses: astral-sh/setup-uv@v8.0.0
          - name: Setup Python
            run: uv python install 3.12
          - name: Install pip-audit
            run: uv pip install pip-audit
          - name: Run pip-audit
            run: uv run pip-audit
      report-audit-status:
        if: always() && github.event_name == 'schedule'
        needs: audit
        runs-on: ubuntu-latest
        permissions:
          contents: read
          issues: write
        steps:
          - name: Create or update scheduled audit issue on failure
            uses: actions/github-script@v8
            with:
              script: |
                const title = "[Security Audit] Scheduled dependency audit failure";
                github.rest.issues.create({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  title,
                });
                github.rest.issues.update({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  issue_number: 1,
                });
                github.rest.issues.createComment({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  issue_number: 1,
                  body: title,
                });
          - name: Close scheduled audit issue on success
            uses: actions/github-script@v8
            with:
              script: |
                const title = "[Security Audit] Scheduled dependency audit failure";
                github.rest.issues.createComment({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  issue_number: 1,
                  body: title,
                });
                github.rest.issues.update({
                  owner: context.repo.owner,
                  repo: context.repo.repo,
                  issue_number: 1,
                  state: "closed",
                });
    """
    errors = module.validate_audit_workflow_content(workflow_content=textwrap.dedent(workflow))
    joined_errors = "\n".join(errors)
    assert "Create Python audit environment" in joined_errors


def test_should_require_uv_security_floor_constraints_for_audited_transitive_dependencies():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pyproject = """
    [project]
    name = "tensor-grep"
    version = "1.3.2"

    [tool.uv]
    constraint-dependencies = ["requests>=2.33.0"]
    """
    errors = module.validate_uv_security_constraints(pyproject_content=textwrap.dedent(pyproject))
    joined_errors = "\n".join(errors)
    assert "cryptography>=46.0.7" in joined_errors
    assert "pygments>=2.20.0" in joined_errors
    assert "python-multipart>=0.0.26" in joined_errors


def test_should_accept_uv_security_floor_constraints_when_all_required_entries_present():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pyproject = """
    [project]
    name = "tensor-grep"
    version = "1.3.2"

    [tool.uv]
    constraint-dependencies = [
      "cryptography>=46.0.7",
      "pygments>=2.20.0",
      "python-multipart>=0.0.26",
      "requests>=2.33.0",
    ]
    """
    errors = module.validate_uv_security_constraints(pyproject_content=textwrap.dedent(pyproject))
    assert errors == []


def test_should_require_ci_package_manager_bundle_build_and_checksum_verification():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    package-manager-readiness:
      steps:
        - run: uv run python scripts/prepare_package_manager_release.py --check
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("Build package-manager publish bundle artifact" in err for err in errors)
    assert any("Verify package-manager publish bundle checksums" in err for err in errors)
    assert any("Smoke-test package-manager bundle contracts" in err for err in errors)
    assert any("Upload package-manager bundle artifact" in err for err in errors)


def test_should_require_release_checklist_to_document_semantic_pr_title_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content="## Homebrew Tap Flow\n## Winget Flow\n## Rollback Procedures\n## Verification Commands\n"
        "gh run list --limit 10\n"
        "uv run python scripts/prepare_package_manager_release.py --check\n"
        "ruby -c Formula/tensor-grep.rb\n"
        "winget validate --manifest\n"
        "winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\X.Y.Z\\\n"
        "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir\n"
        "uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir\n"
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z\n"
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi\n"
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm\n"
        "brew install oimiragieo/tap/tensor-grep\n"
        "winget install oimiragieo.tensor-grep\n"
        "tg --version\n"
        "git revert <tap-formula-commit>\n"
        "git push origin <rollback-branch>\n"
        "brew update\n"
        "winget uninstall oimiragieo.tensor-grep\n"
        "npm/GitHub mismatch\n",
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("feat: ...` -> minor" in err for err in errors)
    assert any("Squash and merge" in err for err in errors)


def test_should_require_ci_terminal_publish_success_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - uses: astral-sh/setup-uv@v8.0.0
        - uses: astral-sh/setup-uv@v8.0.0
        - run: |
            python scripts/validate_release_version_parity.py \
              --pypi-wait-seconds 180 \
              --pypi-poll-interval-seconds 10
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("publish-success-gate" in err for err in errors)
    assert any("empty release_version output" in err for err in errors)
    assert any("non-empty release_version" in err for err in errors)


def test_should_require_release_job_to_depend_on_benchmark_regression_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [release-readiness, package-manager-readiness, static-analysis, test-python, test-rust-core, test-gpu-linux]
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("release job must depend on benchmark-regression" in err for err in errors)


def test_should_require_release_intent_job_and_semantic_pr_title_validator():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release-intent:
        if: github.event_name == 'pull_request'
        steps:
          - name: Validate PR title for semantic release
            run: python scripts/something_else.py
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("scripts/validate_pr_title_semver.py" in err for err in errors)


def test_should_require_release_intent_job_to_be_pull_request_only():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release-intent:
        if: github.event_name == 'push'
        steps:
          - name: Validate PR title for semantic release
            run: python scripts/validate_pr_title_semver.py
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("release-intent job must run only for pull_request events" in err for err in errors)


def test_should_require_ci_benchmark_jobs_to_split_base_compare_and_drift_reporting():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - name: Install benchmark dependencies
            run: |
              uv venv --python 3.12
              uv pip install -e ".[bench,dev]"
          - name: Run core benchmark suite
            run: uv run python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.head.json
          - name: Run hot-query benchmark suite
            run: uv run python benchmarks/run_hot_query_benchmarks.py
          - run: |
              uv run python benchmarks/check_regression.py \
                --baseline base-revision/artifacts/bench_run_benchmarks.base.json \
                --current artifacts/bench_run_benchmarks.head.json
          - run: |
              uv run python benchmarks/summarize_benchmarks.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.head.json \
                --output artifacts/benchmark_summary.md
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "benchmark-regression job must include step `Determine benchmark base revision`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Checkout base revision for same-runner benchmark comparison`"
        in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Install base benchmark dependencies`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Run base benchmark suite`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Report accepted benchmark baseline drift`"
        in err
        for err in errors
    )


def test_should_require_explicit_base_artifact_for_blocking_benchmark_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - name: Install benchmark dependencies
            run: |
              uv venv --python 3.12
              uv pip install -e ".[bench,dev]"
          - name: Determine benchmark base revision
            run: echo "base_sha=deadbeef" >> "$GITHUB_OUTPUT"
          - name: Checkout base revision for same-runner benchmark comparison
            uses: actions/checkout@v6
            with:
              ref: deadbeef
              path: base-revision
          - name: Install base benchmark dependencies
            run: |
              cd base-revision
              uv venv --python 3.12
              uv pip install -e ".[bench,dev]"
          - name: Run core benchmark suite
            run: uv run python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.head.json
          - name: Run base benchmark suite
            run: |
              cd base-revision
              uv run python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.base.json
          - name: Run hot-query benchmark suite
            run: uv run python benchmarks/run_hot_query_benchmarks.py
          - name: Enforce benchmark regression gate
            run: |
              uv run python benchmarks/check_regression.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.head.json
          - name: Report accepted benchmark baseline drift
            run: |
              uv run python benchmarks/check_regression.py \
                --current artifacts/bench_run_benchmarks.json
          - name: Build benchmark markdown summary
            run: |
              uv run python benchmarks/summarize_benchmarks.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.head.json \
                --output artifacts/benchmark_summary.md
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "benchmark-regression `Enforce benchmark regression gate` step must compare against "
        "`base-revision/artifacts/bench_run_benchmarks.base.json`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression `Report accepted benchmark baseline drift` step must pass `--baseline auto`"
        in err
        for err in errors
    )


def test_should_require_structural_gpu_ci_steps_for_retry_and_gpu_pytest():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      test-gpu-linux:
        runs-on: ubuntu-latest
        steps:
          - name: Verify cuDF / RAPIDS Configuration
            run: uv pip install cudf-cu12
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "test-gpu-linux job must include step `Verify cuDF / RAPIDS Configuration (with retry)`"
        in err
        for err in errors
    )
    assert any(
        "test-gpu-linux job must include step `Run Pytest with GPU Hooks`" in err for err in errors
    )


def test_should_require_structural_benchmark_regression_steps_for_base_compare_and_drift_reporting():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        runs-on: ubuntu-latest
        steps:
          - name: Enforce benchmark regression gate
            run: |
              uv run python benchmarks/check_regression.py --current artifacts/bench_run_benchmarks.head.json
          - name: Build benchmark markdown summary
            run: |
              uv run python benchmarks/summarize_benchmarks.py --baseline auto --current artifacts/bench_run_benchmarks.head.json
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "benchmark-regression job must include step `Determine benchmark base revision`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Checkout base revision for same-runner benchmark comparison`"
        in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Install base benchmark dependencies`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Run base benchmark suite`" in err
        for err in errors
    )
    assert any(
        "benchmark-regression job must include step `Report accepted benchmark baseline drift`"
        in err
        for err in errors
    )


def test_should_require_benchmark_regression_to_run_hot_query_benchmark():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - name: Install benchmark dependencies
            run: |
              uv venv --python 3.12
              uv pip install -e ".[dev]"
          - name: Run core benchmark suite
            run: uv run python benchmarks/run_benchmarks.py
          - name: Enforce benchmark regression gate
            run: uv run python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
          - name: Build benchmark markdown summary
            run: uv run python benchmarks/summarize_benchmarks.py --baseline auto --current artifacts/bench_run_benchmarks.json
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "CI workflow benchmark-regression job must include step `Run hot-query benchmark suite`"
        in err
        for err in errors
    )


def test_should_require_benchmark_regression_to_install_bench_and_dev_extras():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      benchmark-regression:
        steps:
          - name: Install benchmark dependencies
            run: |
              uv venv --python 3.12
              uv pip install -e ".[dev]"
          - name: Run core benchmark suite
            run: uv run python benchmarks/run_benchmarks.py
          - name: Run hot-query benchmark suite
            run: uv run python benchmarks/run_hot_query_benchmarks.py
          - name: Enforce benchmark regression gate
            run: uv run python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
          - name: Build benchmark markdown summary
            run: uv run python benchmarks/summarize_benchmarks.py --baseline auto --current artifacts/bench_run_benchmarks.json
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "Install benchmark dependencies` step must install `.[bench,dev]`" in err for err in errors
    )


def test_should_require_benchmark_regression_job_to_exist_when_release_depends_on_it():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "CI workflow must define benchmark-regression job when release depends on it" in err
        for err in errors
    )


def test_should_require_ci_ruff_preview_formatter_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      static-analysis:
        steps:
          - run: uv run ruff format --check .
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("ruff format --check --preview" in err for err in errors)


def test_should_require_ci_pypi_publish_job_security_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      needs: [release, build-wheels-pypi, build-sdist-pypi, validate-pypi-artifacts]
      steps:
        - run: echo publish
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("publish-pypi job must target `environment: pypi`" in err for err in errors)
    assert any(
        "publish-pypi job must request `id-token: write` permission" in err for err in errors
    )
    assert any(
        "publish-pypi job must use pypa/gh-action-pypi-publish@release/v1" in err for err in errors
    )


def test_should_require_ci_pypi_publish_job_url_and_skip_existing_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      environment:
        name: pypi
      permissions:
        id-token: write
      steps:
        - uses: pypa/gh-action-pypi-publish@release/v1
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi job should set canonical PyPI project URL for deployment visibility" in err
        for err in errors
    )
    assert any(
        "publish-pypi job should pass `skip-existing: true` to avoid duplicate-upload failures"
        in err
        for err in errors
    )


def test_should_require_ci_publish_pypi_parity_step_to_include_check_and_retry_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-pypi:
        environment:
          name: pypi
          url: https://pypi.org/p/tensor-grep
        permissions:
          id-token: write
        steps:
          - uses: pypa/gh-action-pypi-publish@release/v1
          - name: Verify release version parity across tag/assets/PyPI
            run: |
              python scripts/validate_release_version_parity.py \
                --expected-version "${{ needs.release.outputs.release_version }}"
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--pypi-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--pypi-poll-interval-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "publish-pypi `Verify release version parity across tag/assets/PyPI` step must include `--dist-dir`"
        in err
        for err in errors
    )


def test_should_require_ci_publish_success_gate_pypi_parity_step_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi]
        steps:
          - name: Verify PyPI parity for semantic-release version (always)
            run: |
              python scripts/validate_release_version_parity.py \
                --expected-version "${{ needs.release.outputs.release_version }}"
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--pypi-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--pypi-poll-interval-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--dist-dir` in its publish_pypi conditional branch"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must conditionally gate `--dist-dir` on `publish_pypi`"
        in err
        for err in errors
    )


def test_should_require_ci_publish_pypi_and_publish_success_gate_parity_step_presence():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [benchmark-regression]
      publish-pypi:
        environment:
          name: pypi
          url: https://pypi.org/p/tensor-grep
        permissions:
          id-token: write
        steps:
          - uses: pypa/gh-action-pypi-publish@release/v1
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi]
        steps:
          - name: Confirm publish job result when publishing is required
            run: echo ok
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-pypi job must include step `Verify release version parity across tag/assets/PyPI`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate job must include step `Verify PyPI parity for semantic-release version (always)`"
        in err
        for err in errors
    )


def test_should_require_publish_success_gate_dist_branch_and_download_guard():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        outputs:
          release_version: 0.31.5
          publish_pypi: 'false'
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi]
        steps:
          - uses: actions/checkout@v6
            if: needs.release.outputs.release_version != ''
          - name: Download all distributions
            if: needs.release.outputs.release_version != ''
            uses: actions/download-artifact@v8
            with:
              pattern: pypi-*
              path: dist
              merge-multiple: true
          - name: Verify PyPI parity for semantic-release version (always)
            if: needs.release.outputs.release_version != ''
            run: |
              python scripts/validate_release_version_parity.py \
                --expected-version "${{ needs.release.outputs.release_version }}" \
                --expected-tag "v${{ needs.release.outputs.release_version }}" \
                --check-pypi \
                --pypi-wait-seconds 180 \
                --pypi-poll-interval-seconds 10
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must include `--dist-dir` in its publish_pypi conditional branch"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Verify PyPI parity for semantic-release version (always)` step must conditionally gate `--dist-dir` on `publish_pypi`"
        in err
        for err in errors
    )
    assert any(
        "publish-success-gate `Download all distributions` step must run only when `publish_pypi == 'true'`"
        in err
        for err in errors
    )


def test_should_fail_when_npm_repository_url_is_not_canonical():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._version_from_pyproject = lambda: "1.2.3"
    module._version_from_cargo = lambda: "1.2.3"

    real_read = module._read

    def fake_read(path):
        path_str = str(path).replace("\\", "/")
        if path_str.endswith("npm/package.json"):
            return (
                "{"
                '"version":"1.2.3",'
                '"repository":{"type":"git","url":"git+https://github.com/tensor-grep/tensor-grep.git"}'
                "}"
            )
        return real_read(path)

    module._read = fake_read
    errors = module.validate_all()
    assert any("npm/package.json repository.url must be" in err for err in errors)


def test_should_fail_ci_workflow_when_parity_gate_skips_package_managers():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    publish-pypi:
      steps:
        - run: |
            python scripts/validate_release_version_parity.py --skip-package-managers
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("must not skip package-manager version checks" in err for err in errors)


def test_should_require_package_manager_runbook_and_checklist_sections():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content="## Homebrew Tap Flow\n",
        checklist_content="## 5. Rollback runbook\n",
    )
    assert any("## Winget Flow" in err for err in errors)
    assert any("## Rollback Procedures" in err for err in errors)
    assert any("Package-manager distribution finalization" in err for err in errors)


def test_should_require_package_manager_runbook_command_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content=(
            "## Homebrew Tap Flow\n"
            "## Winget Flow\n"
            "## Rollback Procedures\n"
            "## Verification Commands\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
            "gh run list --limit 10\n"
            "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z\n"
        ),
    )
    assert any("gh run list --limit 10" in err for err in errors)
    assert any("prepare_package_manager_release.py --check" in err for err in errors)
    assert any("winget validate --manifest" in err for err in errors)
    assert any("verify_package_manager_bundle_checksums.py --bundle-dir" in err for err in errors)
    assert any("ruby -c Formula/tensor-grep.rb" in err for err in errors)
    assert any(
        "winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\X.Y.Z\\" in err
        for err in errors
    )
    assert any(
        "verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z" in err
        for err in errors
    )
    assert any("git revert <tap-formula-commit>" in err for err in errors)
    assert any("git push origin <rollback-branch>" in err for err in errors)
    assert any("brew update" in err for err in errors)
    assert any("winget uninstall oimiragieo.tensor-grep" in err for err in errors)
    assert any(
        "--expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi" in err for err in errors
    )
    assert any("npm/GitHub rollback guidance" in err for err in errors)


def test_should_require_release_checklist_to_include_operator_verification_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content=(
            "## Homebrew Tap Flow\n"
            "## Winget Flow\n"
            "## Rollback Procedures\n"
            "## Verification Commands\n"
            "uv run python scripts/prepare_package_manager_release.py --check\n"
            "winget validate --manifest\n"
            "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle\n"
            "uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm\n"
            "brew install oimiragieo/tap/tensor-grep\n"
            "winget install oimiragieo.tensor-grep\n"
            "tg --version\n"
            "git revert <tap-formula-commit>\n"
            "winget uninstall oimiragieo.tensor-grep\n"
            "npm/GitHub mismatch\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("gh run list --limit 10" in err for err in errors)
    assert any(
        "verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z" in err
        for err in errors
    )


def test_should_require_package_manager_runbook_smoke_install_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_package_manager_docs(
        runbook_content=(
            "## Homebrew Tap Flow\n"
            "## Winget Flow\n"
            "## Rollback Procedures\n"
            "## Verification Commands\n"
            "uv run python scripts/prepare_package_manager_release.py --check\n"
            "winget validate --manifest\n"
            "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir\n"
            "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm\n"
            "npm/GitHub mismatch\n"
        ),
        checklist_content=(
            "## 4. Package-manager distribution finalization\n"
            "## 5. Rollback runbook\n"
            "Homebrew\n"
            "Winget\n"
        ),
    )
    assert any("brew install oimiragieo/tap/tensor-grep" in err for err in errors)
    assert any("winget install oimiragieo.tensor-grep" in err for err in errors)
    assert any("tg --version" in err for err in errors)


def test_should_require_explicit_homebrew_version_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_brew = 'class TensorGrep < Formula\n  version "1.2.3"\nend\n'
    errors = module.validate_homebrew_formula_contract(brew_content=bad_brew, py_version="1.2.3")
    assert any("TENSOR_GREP_VERSION assignment" in err for err in errors)
    assert any("version TENSOR_GREP_VERSION" in err for err in errors)


def test_should_require_package_manager_sections_in_installation_docs():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_installation_docs(
        installation_content="### Homebrew Tap Flow\n### Winget Flow\n"
    )
    assert any("### Repeatable Release Checklist" in err for err in errors)
    assert any("### Rollback Playbook" in err for err in errors)
    assert any("oimiragieo/tensor-grep" in err for err in errors)
    assert any("npm parity checks" in err for err in errors)


def test_should_require_installation_docs_to_include_package_manager_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_installation_docs(
        installation_content=(
            "### Homebrew Tap Flow\n"
            "### Winget Flow\n"
            "### Repeatable Release Checklist\n"
            "### Rollback Playbook\n"
            "https://github.com/oimiragieo/tensor-grep/releases\n"
            "--check-npm\n"
        )
    )
    assert any("brew tap oimiragieo/tap" in err for err in errors)
    assert any("brew install tensor-grep" in err for err in errors)
    assert any("brew install oimiragieo/tap/tensor-grep" in err for err in errors)
    assert any("winget validate --manifest" in err for err in errors)
    assert any("winget-pkgs" in err for err in errors)
    assert any("winget install oimiragieo.tensor-grep" in err for err in errors)
    assert any("tg --version" in err for err in errors)


def test_should_validate_readme_canonical_docs_and_installation_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_readme_contract(
        readme_content=(
            "# tensor-grep\n"
            "`tensor-grep` has first class support on Windows, macOS and Linux.\n"
            "Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md)\n"
            "and the workflow guide in [docs/harness_cookbook.md](docs/harness_cookbook.md).\n"
            "## Canonical Docs\n"
            "- [docs/benchmarks.md](docs/benchmarks.md)\n"
            "- [docs/tool_comparison.md](docs/tool_comparison.md)\n"
            "- [docs/gpu_crossover.md](docs/gpu_crossover.md)\n"
            "- [docs/routing_policy.md](docs/routing_policy.md)\n"
            "- [docs/harness_api.md](docs/harness_api.md)\n"
            "- [docs/harness_cookbook.md](docs/harness_cookbook.md)\n"
            "- [docs/installation.md](docs/installation.md)\n"
            "- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)\n"
            "## Installation\n"
            "pip install tensor-grep\n"
            'uv pip install "tensor-grep[ast,nlp]"\n'
            'npx tensor-grep search "ERROR" .\n'
            "GitHub Releases page\n"
        )
    )
    assert errors == []


def test_should_require_readme_canonical_docs_and_installation_surfaces():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_readme_contract(
        readme_content=(
            "# tensor-grep\n"
            "## Canonical Docs\n"
            "- [docs/benchmarks.md](docs/benchmarks.md)\n"
            "## Installation\n"
            "pip install tensor-grep\n"
        )
    )
    joined_errors = "\n".join(errors)
    assert "README missing canonical docs reference" in joined_errors
    assert "README must link installation docs" in joined_errors
    assert "README must link release checklist" in joined_errors
    assert "README must state current first-class platform support explicitly" in joined_errors
    assert "README must direct harness consumers to docs/harness_api.md" in joined_errors


def test_should_require_smoke_test_package_manager_bundle_command_in_runbook():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    runbook = """
    ## Homebrew Tap Flow
    ## Winget Flow
    ## Rollback Procedures
    ## Verification Commands
    uv run python scripts/prepare_package_manager_release.py --check
    winget validate --manifest
    uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
    python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi
    python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm
    brew install oimiragieo/tap/tensor-grep
    winget install oimiragieo.tensor-grep
    tg --version
    """
    checklist = """
    ## 4. Package-manager distribution finalization
    ## 5. Rollback runbook
    Homebrew
    Winget
    """
    errors = module.validate_package_manager_docs(
        runbook_content=runbook,
        checklist_content=checklist,
    )
    assert any("smoke_test_package_manager_bundle.py" in err for err in errors)


def test_should_require_publish_jobs_to_depend_on_tag_version_parity():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      validate-tag-version-parity:
        needs: verify-release-assets
        runs-on: ubuntu-latest
      publish-docs:
        needs: verify-release-assets
        runs-on: ubuntu-latest
      publish-npm:
        needs: verify-release-assets
        runs-on: ubuntu-latest
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("publish-docs must depend on validate-tag-version-parity" in err for err in errors)
    assert any("publish-npm must depend on validate-tag-version-parity" in err for err in errors)


def test_should_require_verify_release_assets_to_depend_on_create_release():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      create-release:
        runs-on: ubuntu-latest
      verify-release-assets:
        needs: build-binaries
        runs-on: ubuntu-latest
        steps:
          - name: Verify uploaded release assets and checksum coverage
            run: |
              python scripts/verify_github_release_assets.py \
                --repo "${{ github.repository }}" \
                --tag "${GITHUB_REF#refs/tags/}" \
                --token "${{ secrets.GITHUB_TOKEN }}"
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("verify-release-assets must depend on create-release" in err for err in errors)


def test_should_require_release_to_publish_package_manager_bundle_assets():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      create-release:
        steps:
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v2
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any("Build package-manager publish bundle" in err for err in errors)
    assert any("Verify package-manager bundle checksums" in err for err in errors)
    assert any("Smoke-test package-manager bundle contracts" in err for err in errors)
    assert any("Smoke-test Binary (Windows)" in err for err in errors)
    assert any("artifacts/package-manager-bundle/**" in err for err in errors)


def test_should_fail_release_workflow_when_removed_skip_pypi_flag_is_present():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-tag-version-parity:
        steps:
          - run: |
              python scripts/validate_release_version_parity.py --skip-pypi
    """
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    assert any("unsupported --skip-pypi" in err for err in errors)


def test_should_require_terminal_release_success_gate_dependencies():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bad_release_workflow = """
    jobs:
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-npm:
        needs: validate-tag-version-parity
      publish-docs:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: validate-tag-version-parity
        runs-on: ubuntu-latest
    """
    errors = module.validate_release_workflow_content(release_workflow=bad_release_workflow)
    assert any(
        "release-success-gate must depend on parity + publish-npm + publish-docs" in err
        for err in errors
    )


def test_should_require_validate_package_managers_job_to_include_preflight_bundle_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        runs-on: ubuntu-latest
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    assert any(
        "validate-package-managers job must include step `Preflight build package-manager publish bundle artifact`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers job must include step `Preflight verify package-manager bundle checksums`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers job must include step `Preflight smoke-test package-manager bundle contracts`"
        in err
        for err in errors
    )


def test_should_require_create_release_job_to_include_bundle_build_verify_and_smoke_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release job must include step `Verify package-manager bundle checksums`" in err
        for err in errors
    )
    assert any(
        "create-release job must include step `Smoke-test package-manager bundle contracts`" in err
        for err in errors
    )


def test_should_require_create_release_bundle_steps_to_invoke_expected_scripts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Build package-manager publish bundle` step must pass `--output-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Verify package-manager bundle checksums` step must pass `--bundle-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Smoke-test package-manager bundle contracts` step must pass `--bundle-dir artifacts/package-manager-bundle`"
        in err
        for err in errors
    )


def test_should_require_verify_release_assets_step_contracts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
        steps:
          - name: Verify uploaded release assets and checksum coverage
            run: python scripts/verify_github_release_assets.py --repo "${{ github.repository }}"
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "verify-release-assets `Verify uploaded release assets and checksum coverage` step must include `--tag`"
        in err
        for err in errors
    )
    assert any(
        "verify-release-assets `Verify uploaded release assets and checksum coverage` step must include `--token`"
        in err
        for err in errors
    )


def test_should_require_validate_tag_version_parity_step_contracts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Validate release tag/version parity across package metadata
            run: python scripts/validate_release_version_parity.py
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-version`"
        in err
        for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_binary_smoke_verify_expected_version_flag():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-verify Linux release binary version
            run: uv run python scripts/smoke_verify_release_binary.py
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Smoke-verify Linux release binary version` step must pass `--expected-version`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Smoke-verify Linux release binary version` step must pass `--artifacts-dir`"
        in err
        for err in errors
    )


def test_should_require_create_release_sbom_slsa_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      create-release:
        steps:
          - name: Build stuff
            run: echo "building"
    """
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "Release workflow create-release job must include step `Generate Rust SBOM`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Generate Python SBOM`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Sign artifacts with Sigstore`"
        in joined_errors
    )
    assert (
        "Release workflow create-release job must include step `Generate SLSA Provenance`"
        in joined_errors
    )


def test_should_require_release_binary_artifact_validation_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Validate release binary artifact matrix and generate checksums
            run: uv run python scripts/validate_release_binary_artifacts.py
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-verify Linux release binary version
            run: uv run python scripts/smoke_verify_release_binary.py --artifacts-dir artifacts --expected-version "${GITHUB_REF#refs/tags/v}"
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Validate release binary artifact matrix and generate checksums` step must pass `--artifacts-dir`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Validate release binary artifact matrix and generate checksums` step must pass `--checksums-out`"
        in err
        for err in errors
    )


def test_should_require_release_parity_steps_to_include_registry_check_flags_and_retries():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
        steps:
          - name: Verify final npm parity before release success gate
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
          - name: Verify final PyPI parity before release success gate
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Verify npm registry parity for release version` step must include `--check-npm`"
        in err
        for err in errors
    )
    assert any(
        "publish-npm `Verify npm registry parity for release version` step must include `--npm-wait-seconds`"
        in err
        for err in errors
    )
    assert any(
        "publish-npm `Verify npm registry parity for release version` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final npm parity before release success gate` step must include `--check-npm`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final npm parity before release success gate` step must include `--expected-tag`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final PyPI parity before release success gate` step must include `--check-pypi`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate `Verify final PyPI parity before release success gate` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_parity_step_presence_for_publish_npm_and_release_success_gate():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
          - name: Preflight verify package-manager bundle checksums
          - name: Preflight smoke-test package-manager bundle contracts
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Validate release tag/version parity across package metadata
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}"
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: echo "ok"
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
        steps:
          - name: Confirm release publication gates
            run: echo "ok"
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm job must include step `Verify npm registry parity for release version`" in err
        for err in errors
    )
    assert any(
        "release-success-gate job must include step `Verify final npm parity before release success gate`"
        in err
        for err in errors
    )
    assert any(
        "release-success-gate job must include step `Verify final PyPI parity before release success gate`"
        in err
        for err in errors
    )


def test_should_require_validate_pypi_artifacts_job_step_flags():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      validate-pypi-artifacts:
        steps:
          - name: Download all distributions
            uses: actions/download-artifact@v8
            with:
              pattern: pypi-*
              path: dist
              merge-multiple: true
          - name: Validate built PyPI artifact set
            run: |
              python scripts/validate_pypi_artifacts.py \
                --dist-dir dist
          - name: Smoke-test install from built PyPI artifacts
            run: |
              python scripts/smoke_test_pypi_artifacts.py \
                --dist-dir dist
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must include `--version`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must include `--require-platforms`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must include `--version`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must include `--work-dir`"
        in err
        for err in errors
    )


def test_should_require_validate_pypi_artifacts_job_step_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      validate-pypi-artifacts:
        steps:
          - name: Validate built PyPI artifact set
            run: |
              python scripts/check_dist.py \
                --dist-dir dist \
                --version "${{ needs.release.outputs.release_version }}" \
                --require-platforms "linux,macos,windows"
          - name: Smoke-test install from built PyPI artifacts
            run: |
              python scripts/install_from_dist.py \
                --dist-dir dist \
                --version "${{ needs.release.outputs.release_version }}" \
                --work-dir .tmp
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "validate-pypi-artifacts `Validate built PyPI artifact set` step must invoke `scripts/validate_pypi_artifacts.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-pypi-artifacts `Smoke-test install from built PyPI artifacts` step must invoke `scripts/smoke_test_pypi_artifacts.py`"
        in err
        for err in errors
    )


def test_should_require_release_validate_package_managers_step_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/build_bundle.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/check_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/check_bundle_smoke.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-package-managers `Preflight build package-manager publish bundle artifact` step must invoke `scripts/prepare_package_manager_release.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Preflight verify package-manager bundle checksums` step must invoke `scripts/verify_package_manager_bundle_checksums.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Preflight smoke-test package-manager bundle contracts` step must invoke `scripts/smoke_test_package_manager_bundle.py`"
        in err
        for err in errors
    )


def test_should_require_release_validate_package_manager_source_state_command():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_bundle.py
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-package-managers `Validate package-manager publish bundle source state` step must invoke `scripts/prepare_package_manager_release.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-package-managers `Validate package-manager publish bundle source state` step must pass `--check`"
        in err
        for err in errors
    )


def test_should_require_release_publish_docs_step_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install docs
            run: pip install mkdocs
          - name: Deploy Docs
            run: mkdocs build
      publish-npm:
        needs: validate-tag-version-parity
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any("publish-docs job must include step `Install mkdocs`" in err for err in errors)
    assert any(
        "publish-docs `Deploy Docs` step must invoke `mkdocs gh-deploy --force`" in err
        for err in errors
    )


def test_should_require_release_publish_npm_prepublish_commands():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: echo "ok"
          - name: Publish NPM Package
            run: npm pack
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Verify Version Match` step must invoke `node -p \"require('./npm/package.json').version\"`"
        in err
        for err in errors
    )
    assert any(
        "publish-npm `Publish NPM Package` step must invoke `npm publish --access public`" in err
        for err in errors
    )


def test_should_require_release_create_github_release_step_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v1
            with:
              files: |
                artifacts/**/tg-*
              generate_release_notes: false
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "create-release `Create GitHub Release` step must use `softprops/action-gh-release@v2`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must include `artifacts/CHECKSUMS.txt`" in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must include `artifacts/package-manager-bundle/**`"
        in err
        for err in errors
    )
    assert any(
        "create-release `Create GitHub Release` step must set `generate_release_notes: true`" in err
        for err in errors
    )


def test_should_require_release_validate_tag_version_parity_setup_and_command():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v2
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
                artifacts/package-manager-bundle/**
              generate_release_notes: true
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Validate release tag/version parity across package metadata
            run: python scripts/check_release_parity.py --expected-version "${GITHUB_REF#refs/tags/v}"
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "validate-tag-version-parity job must include step `Install uv`" in err for err in errors
    )
    assert any(
        "validate-tag-version-parity job must include step `Setup Python`" in err for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must invoke `scripts/validate_release_version_parity.py`"
        in err
        for err in errors
    )
    assert any(
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` step must include `--expected-tag`"
        in err
        for err in errors
    )


def test_should_require_release_publish_npm_setup_node_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = """
    jobs:
      validate-package-managers:
        steps:
          - name: Validate package-manager publish bundle source state
            run: uv run python scripts/prepare_package_manager_release.py --check
          - name: Preflight build package-manager publish bundle artifact
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Preflight verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Preflight smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
      build-binaries:
        needs: [validate-release-assets, validate-package-managers]
      create-release:
        steps:
          - name: Build package-manager publish bundle
            run: uv run python scripts/prepare_package_manager_release.py --output-dir artifacts/package-manager-bundle
          - name: Verify package-manager bundle checksums
            run: uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir artifacts/package-manager-bundle
          - name: Smoke-test package-manager bundle contracts
            run: uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir artifacts/package-manager-bundle
          - name: Create GitHub Release
            uses: softprops/action-gh-release@v2
            with:
              files: |
                artifacts/**/tg-*
                artifacts/CHECKSUMS.txt
                artifacts/package-manager-bundle/**
              generate_release_notes: true
      verify-release-assets:
        needs: create-release
      validate-tag-version-parity:
        needs: verify-release-assets
        steps:
          - name: Install uv
            uses: astral-sh/setup-uv@v8.0.0
          - name: Setup Python
            run: uv python install 3.12
          - name: Validate release tag/version parity across package metadata
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}"
      publish-docs:
        needs: validate-tag-version-parity
        steps:
          - name: Install mkdocs
            run: pip install mkdocs-material
          - name: Deploy Docs
            run: mkdocs gh-deploy --force
      publish-npm:
        needs: validate-tag-version-parity
        steps:
          - name: Setup Node.js
            uses: actions/setup-node@v3
            with:
              node-version: '22'
          - name: Verify Version Match
            run: |
              TAG_VERSION=${GITHUB_REF#refs/tags/v}
              NPM_VERSION=$(node -p "require('./npm/package.json').version")
              if [ "$TAG_VERSION" != "$NPM_VERSION" ]; then
                exit 1
              fi
          - name: Publish NPM Package
            run: npm publish --access public
          - name: Verify npm registry parity for release version
            run: python scripts/validate_release_version_parity.py --expected-version "${GITHUB_REF#refs/tags/v}" --expected-tag "${GITHUB_REF#refs/tags/}" --check-npm --npm-wait-seconds 180 --npm-poll-interval-seconds 10
      release-success-gate:
        needs: [validate-tag-version-parity, publish-npm, publish-docs]
    """
    errors = module.validate_release_workflow_content(release_workflow=release_workflow)
    assert any(
        "publish-npm `Setup Node.js` step must use `actions/setup-node@v6`" in err for err in errors
    )
    assert any(
        "publish-npm `Setup Node.js` step must include `registry-url: https://registry.npmjs.org`"
        in err
        for err in errors
    )


def test_should_require_release_build_binaries_step_contracts():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    build_binaries_prefix, build_binaries_rest = release_workflow.split("  build-binaries:", 1)
    build_binaries_section, remainder = build_binaries_rest.split("  create-release:", 1)
    build_binaries_section = build_binaries_section.replace(
        "astral-sh/setup-uv@v8.0.0",
        "astral-sh/setup-uv@v4.0.0",
        1,
    )
    build_binaries_section = build_binaries_section.replace(
        "uv python install 3.12",
        "python -V",
        1,
    )
    release_workflow = (
        build_binaries_prefix
        + "  build-binaries:"
        + build_binaries_section
        + "  create-release:"
        + remainder
    )
    release_workflow = release_workflow.replace(
        'uv pip install -e ".[dev]"',
        'uv pip install -e "."',
        1,
    )
    release_workflow = release_workflow.replace(
        "uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124",
        "uv pip install torch torchvision torchaudio",
        1,
    )
    release_workflow = release_workflow.replace(
        'uv pip install -e ".[gpu-win,nlp,ast,dev]"',
        'uv pip install -e ".[dev]"',
        1,
    )
    release_workflow = release_workflow.replace(
        "uv run python scripts/build_binaries.py",
        "python scripts/build.py",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg.exe tg-windows-amd64-${{ matrix.gpu }}.exe",
        "mv tg.exe tg.exe",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg tg-linux-amd64-${{ matrix.gpu }}",
        "mv tg tg-linux",
        1,
    )
    release_workflow = release_workflow.replace(
        "mv tg tg-macos-amd64-${{ matrix.gpu }}",
        "mv tg tg-macos",
        1,
    )
    release_workflow = release_workflow.replace(
        r".\tg-windows-amd64-${{ matrix.gpu }}.exe --version",
        r".\tg.exe --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "./tg-linux-amd64-${{ matrix.gpu }} --version",
        "./tg --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "./tg-macos-amd64-${{ matrix.gpu }} --version",
        "./tg --version",
        1,
    )
    release_workflow = release_workflow.replace(
        "actions/upload-artifact@v7", "actions/upload-artifact@v3", 1
    )
    release_workflow = release_workflow.replace("path: tg-*", "path: dist/*", 1)
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "build-binaries `Install uv` step must use `astral-sh/setup-uv@v8.0.0`" in joined_errors
    assert (
        "build-binaries `Set up Python` step must invoke `uv python install 3.12`" in joined_errors
    )
    assert (
        "build-binaries `Build Binary` step must invoke `scripts/build_binaries.py`"
        in joined_errors
    )
    assert (
        'build-binaries `Install dependencies (CPU)` step must invoke `uv pip install -e ".[dev]"`'
        in joined_errors
    )
    assert (
        "build-binaries `Install dependencies (NVIDIA)` step must invoke "
        "`uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124`"
        in joined_errors
    )
    assert (
        'build-binaries `Install dependencies (NVIDIA)` step must invoke `uv pip install -e ".[gpu-win,nlp,ast,dev]"`'
        in joined_errors
    )
    assert (
        "build-binaries `Upload Artifact` step must use `actions/upload-artifact@v7`"
        in joined_errors
    )
    assert "build-binaries `Upload Artifact` step must include `path: tg-*`" in joined_errors
    assert (
        "build-binaries `Rename Artifact (Windows)` step must invoke "
        "`mv tg.exe tg-windows-amd64-${{ matrix.gpu }}.exe`" in joined_errors
    )
    assert (
        "build-binaries `Rename Artifact (Linux)` step must invoke "
        "`mv tg tg-linux-amd64-${{ matrix.gpu }}`" in joined_errors
    )
    assert (
        "build-binaries `Rename Artifact (macOS)` step must invoke "
        "`mv tg tg-macos-amd64-${{ matrix.gpu }}`" in joined_errors
    )
    assert "build-binaries `Smoke-test Binary (Windows)` step must invoke" in joined_errors
    assert "tg-windows-amd64-${{ matrix.gpu }}.exe --version" in joined_errors
    assert (
        "build-binaries `Smoke-test Binary (Linux)` step must invoke "
        "`./tg-linux-amd64-${{ matrix.gpu }} --version`" in joined_errors
    )
    assert (
        "build-binaries `Smoke-test Binary (macOS)` step must invoke "
        "`./tg-macos-amd64-${{ matrix.gpu }} --version`" in joined_errors
    )


def test_should_require_create_release_download_artifacts_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    release_workflow = release_workflow.replace(
        "actions/download-artifact@v8",
        "actions/download-artifact@v3",
        1,
    )
    release_workflow = release_workflow.replace("path: artifacts", "path: dist", 1)
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "create-release `Download Artifacts` step must use `actions/download-artifact@v8`"
        in joined_errors
    )
    assert (
        "create-release `Download Artifacts` step must include `path: artifacts`" in joined_errors
    )


def test_should_require_create_release_setup_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    create_release_prefix, create_release_rest = release_workflow.split("  create-release:", 1)
    create_release_section, remainder = create_release_rest.split("  verify-release-assets:", 1)
    create_release_section = create_release_section.replace(
        "astral-sh/setup-uv@v8.0.0",
        "astral-sh/setup-uv@v4.0.0",
        1,
    )
    create_release_section = create_release_section.replace(
        "uv python install 3.12",
        "python -V",
        1,
    )
    release_workflow = (
        create_release_prefix
        + "  create-release:"
        + create_release_section
        + "  verify-release-assets:"
        + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "create-release `Install uv` step must use `astral-sh/setup-uv@v8.0.0`" in joined_errors
    assert (
        "create-release `Setup Python` step must invoke `uv python install 3.12`" in joined_errors
    )


def test_should_require_create_release_artifact_validation_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    create_release_prefix, create_release_rest = release_workflow.split("  create-release:", 1)
    create_release_section, remainder = create_release_rest.split("  verify-release-assets:", 1)
    create_release_section = create_release_section.replace(
        "Validate release binary artifact matrix and generate checksums",
        "Validate release binaries",
        1,
    )
    create_release_section = create_release_section.replace(
        "Smoke-verify Linux release binary version",
        "Smoke-verify release binary",
        1,
    )
    release_workflow = (
        create_release_prefix
        + "  create-release:"
        + create_release_section
        + "  verify-release-assets:"
        + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "create-release job must include step `Validate release binary artifact matrix and generate checksums`"
        in joined_errors
    )
    assert (
        "create-release job must include step `Smoke-verify Linux release binary version`"
        in joined_errors
    )


def test_should_require_verify_release_assets_checkout_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    verify_prefix, verify_rest = release_workflow.split("  verify-release-assets:", 1)
    verify_section, remainder = verify_rest.split("  validate-tag-version-parity:", 1)
    verify_section = verify_section.replace("actions/checkout@v6", "actions/checkout@v3", 1)
    release_workflow = (
        verify_prefix
        + "  verify-release-assets:"
        + verify_section
        + "  validate-tag-version-parity:"
        + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "verify-release-assets job must include `actions/checkout@v6`" in joined_errors


def test_should_require_verify_release_assets_python_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    verify_prefix, verify_rest = release_workflow.split("  verify-release-assets:", 1)
    verify_section, remainder = verify_rest.split("  validate-tag-version-parity:", 1)
    verify_section = verify_section.replace(
        "python scripts/verify_github_release_assets.py",
        "uv run python scripts/verify_github_release_assets.py",
        1,
    )
    release_workflow = (
        verify_prefix
        + "  verify-release-assets:"
        + verify_section
        + "  validate-tag-version-parity:"
        + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "verify-release-assets `Verify uploaded release assets and checksum coverage` step must invoke "
        "`python scripts/verify_github_release_assets.py`" in joined_errors
    )


def test_should_require_validate_tag_version_parity_setup_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    tag_prefix, tag_rest = release_workflow.split("  validate-tag-version-parity:", 1)
    tag_section, remainder = tag_rest.split("  publish-npm:", 1)
    tag_section = tag_section.replace("actions/checkout@v6", "actions/checkout@v3", 1)
    tag_section = tag_section.replace("astral-sh/setup-uv@v8.0.0", "astral-sh/setup-uv@v4.0.0", 1)
    tag_section = tag_section.replace("uv python install 3.12", "python -V", 1)
    release_workflow = (
        tag_prefix + "  validate-tag-version-parity:" + tag_section + "  publish-npm:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "validate-tag-version-parity job must include `actions/checkout@v6`" in joined_errors
    assert (
        "validate-tag-version-parity `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
        in joined_errors
    )
    assert (
        "validate-tag-version-parity `Setup Python` step must invoke `uv python install 3.12`"
        in joined_errors
    )


def test_should_require_validate_tag_version_parity_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    tag_prefix, tag_rest = release_workflow.split("  validate-tag-version-parity:", 1)
    tag_section, remainder = tag_rest.split("  publish-npm:", 1)
    tag_section = tag_section.replace(
        "python scripts/validate_release_version_parity.py",
        "uv run python scripts/validate_release_version_parity.py",
        1,
    )
    release_workflow = (
        tag_prefix + "  validate-tag-version-parity:" + tag_section + "  publish-npm:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "validate-tag-version-parity `Validate release tag/version parity across package metadata` "
        "step must invoke `python scripts/validate_release_version_parity.py`" in joined_errors
    )


def test_should_require_publish_npm_checkout_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace("actions/checkout@v6", "actions/checkout@v3", 1)
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-npm job must include `actions/checkout@v6`" in joined_errors


def test_should_require_publish_npm_uv_python_setup_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace("astral-sh/setup-uv@v8.0.0", "astral-sh/setup-uv@v4.0.0", 1)
    npm_section = npm_section.replace("uv python install 3.12", "python -V", 1)
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-npm `Install uv` step must use `astral-sh/setup-uv@v8.0.0`" in joined_errors
    assert "publish-npm `Setup Python` step must invoke `uv python install 3.12`" in joined_errors


def test_should_require_publish_npm_node_version_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace("node-version: '22'", "node-version: '18'", 1)
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-npm `Setup Node.js` step must include `node-version: 22`" in joined_errors


def test_should_require_publish_npm_version_check_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace(
        "TAG_VERSION=${GITHUB_REF#refs/tags/v}",
        "VERSION=${GITHUB_REF#refs/tags/v}",
        1,
    )
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "publish-npm `Verify Version Match` step must begin with `TAG_VERSION=${GITHUB_REF#refs/tags/v}`"
        in joined_errors
    )


def test_should_require_publish_npm_registry_parity_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace(
        "python scripts/validate_release_version_parity.py",
        "uv run python scripts/validate_release_version_parity.py",
        1,
    )
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "publish-npm `Verify npm registry parity for release version` step must invoke "
        "`python scripts/validate_release_version_parity.py`" in joined_errors
    )


def test_should_require_publish_npm_working_directory_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace("working-directory: npm", "working-directory: .", 1)
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "publish-npm `Publish NPM Package` step must include `working-directory: npm`"
        in joined_errors
    )


def test_should_require_publish_npm_auth_env_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    npm_prefix, npm_rest = release_workflow.split("  publish-npm:", 1)
    npm_section, remainder = npm_rest.split("  publish-docs:", 1)
    npm_section = npm_section.replace(
        "NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}",
        "NODE_AUTH_TOKEN: ${{ secrets.OTHER_TOKEN }}",
        1,
    )
    release_workflow = npm_prefix + "  publish-npm:" + npm_section + "  publish-docs:" + remainder
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "publish-npm `Publish NPM Package` step must include `NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}`"
        in joined_errors
    )


def test_should_require_publish_docs_checkout_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace("actions/checkout@v6", "actions/checkout@v3", 1)
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs job must include `actions/checkout@v6`" in joined_errors


def test_should_require_publish_docs_python_setup_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace("actions/setup-python@v6", "actions/setup-python@v4", 1)
    docs_section = docs_section.replace("python-version: '3.11'", "python-version: '3.10'", 1)
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs `Set up Python` step must use `actions/setup-python@v6`" in joined_errors
    assert "publish-docs `Set up Python` step must include `python-version: 3.11`" in joined_errors


def test_should_require_publish_docs_force_flag():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace("mkdocs gh-deploy --force", "mkdocs gh-deploy", 1)
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs `Deploy Docs` step must invoke `mkdocs gh-deploy --force`" in joined_errors


def test_should_require_publish_docs_build_step_with_strict_mode():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace(
        "      - name: Build Docs\n", "      - name: Build Site\n", 1
    )
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs job must include step `Build Docs`" in joined_errors

    docs_section = docs_rest.split("  release-success-gate:", 1)[0]
    docs_section = docs_section.replace("mkdocs build --strict", "mkdocs build", 1)
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs `Build Docs` step must invoke `mkdocs build --strict`" in joined_errors


def test_should_require_publish_docs_install_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace(
        "pip install mkdocs-material", "uv run pip install mkdocs-material", 1
    )
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "publish-docs `Install mkdocs` step must invoke `pip install mkdocs-material`"
        in joined_errors
    )


def test_should_require_ci_release_readiness_docs_build_step():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ci_workflow = ci_workflow.replace(
        "      - name: Build docs site (strict)\n",
        "      - name: Build docs site\n",
        1,
    )
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow missing expected package-manager validation block: Build docs site (strict)"
        in joined_errors
    )


def test_should_require_publish_docs_deploy_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs_prefix, docs_rest = release_workflow.split("  publish-docs:", 1)
    docs_section, remainder = docs_rest.split("  release-success-gate:", 1)
    docs_section = docs_section.replace(
        "mkdocs gh-deploy --force", "uv run mkdocs gh-deploy --force", 1
    )
    release_workflow = (
        docs_prefix + "  publish-docs:" + docs_section + "  release-success-gate:" + remainder
    )
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "publish-docs `Deploy Docs` step must invoke `mkdocs gh-deploy --force`" in joined_errors


def test_should_require_release_success_gate_setup_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gate_prefix, gate_rest = release_workflow.split("  release-success-gate:", 1)
    gate_section = gate_rest
    gate_section = gate_section.replace("actions/checkout@v6", "actions/checkout@v3", 1)
    gate_section = gate_section.replace("astral-sh/setup-uv@v8.0.0", "astral-sh/setup-uv@v4.0.0", 1)
    gate_section = gate_section.replace("uv python install 3.12", "python -V", 1)
    release_workflow = gate_prefix + "  release-success-gate:" + gate_section
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert "release-success-gate job must include `actions/checkout@v6`" in joined_errors
    assert (
        "release-success-gate `Install uv` step must use `astral-sh/setup-uv@v8.0.0`"
        in joined_errors
    )
    assert (
        "release-success-gate `Setup Python` step must invoke `uv python install 3.12`"
        in joined_errors
    )


def test_should_require_release_success_gate_confirm_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gate_prefix, gate_rest = release_workflow.split("  release-success-gate:", 1)
    gate_section = gate_rest.replace(
        'echo "Release publication gates passed: parity, npm, docs."',
        'echo "Release checks passed."',
        1,
    )
    release_workflow = gate_prefix + "  release-success-gate:" + gate_section
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "release-success-gate `Confirm release publication gates` step must invoke "
        '`echo "Release publication gates passed: parity, npm, docs."`' in joined_errors
    )


def test_should_require_release_success_gate_parity_script_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gate_prefix, gate_rest = release_workflow.split("  release-success-gate:", 1)
    gate_section = gate_rest.replace(
        "python scripts/validate_release_version_parity.py",
        "python scripts/check_versions.py",
        1,
    )
    release_workflow = gate_prefix + "  release-success-gate:" + gate_section
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "release-success-gate `Verify final npm parity before release success gate` step must invoke "
        "`scripts/validate_release_version_parity.py`" in joined_errors
    )


def test_should_require_release_success_gate_parity_entrypoint_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gate_prefix, gate_rest = release_workflow.split("  release-success-gate:", 1)
    gate_section = gate_rest.replace(
        "python scripts/validate_release_version_parity.py",
        "uv run python scripts/validate_release_version_parity.py",
        1,
    )
    release_workflow = gate_prefix + "  release-success-gate:" + gate_section
    errors = module.validate_release_workflow_content(
        release_workflow=textwrap.dedent(release_workflow)
    )
    joined_errors = "\n".join(errors)
    assert (
        "release-success-gate `Verify final npm parity before release success gate` step must invoke "
        "`python scripts/validate_release_version_parity.py`" in joined_errors
    )
