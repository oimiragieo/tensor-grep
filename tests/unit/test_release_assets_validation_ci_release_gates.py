"""ci.yml release/benchmark/publish/native-asset gate contracts."""

import importlib.util
from pathlib import Path

from tests.unit.test_release_assets_validation_shared import _detag


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
    assert any("semantic-release no-release output" in err for err in errors)
    assert any("released == 'true'" in err for err in errors)


def test_should_require_pypi_artifact_builds_to_prefetch_cargo_dependencies_with_retry():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        "      - name: Prefetch Rust dependencies for PyPI artifacts\n",
        "      - name: Prefetch Rust dependencies without retry\n",
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow build-wheels-pypi job must prefetch Rust dependencies with retry before maturin"
        in joined_errors
    )
    assert (
        "CI workflow build-sdist-pypi job must prefetch Rust dependencies with retry before maturin"
        in joined_errors
    )


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


def test_should_require_release_job_to_depend_on_full_required_gate_set():
    # Guard for the "spot-check only benchmark-regression" gap: dropping ANY blocking gate from
    # the release job's `needs` list (not just benchmark-regression) must fail validation, or a
    # future refactor could silently drop e.g. static-analysis/windows-agent-readiness and still
    # publish without that check having run.
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [smoke, release-readiness, package-manager-readiness, static-analysis, test-python, test-rust-core, search-golden-parity, native-build-smoke, test-gpu-linux, benchmark-regression]
    """
    # Missing: agent-readiness, windows-agent-readiness
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any("release job must depend on agent-readiness" in err for err in errors)
    assert any("release job must depend on windows-agent-readiness" in err for err in errors)


def test_should_accept_release_job_with_full_required_gate_set():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    jobs:
      release:
        needs: [smoke, release-readiness, agent-readiness, windows-agent-readiness, package-manager-readiness, static-analysis, test-python, test-rust-core, search-golden-parity, native-build-smoke, test-gpu-linux, benchmark-regression]
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert not any("release job must depend on" in err for err in errors)


def test_should_require_registration_completeness_step_in_static_analysis_job():
    # Audit #36: a future "just make CI green" pass could re-add `continue-on-error: true` to the
    # Registration completeness step, silently softening the --rank-class front-door gate back to
    # warn-only with no test failing. Guard both "step missing" and "step present but softened".
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow_missing_step = """
    jobs:
      static-analysis:
        steps:
          - name: Some other step
            run: echo hi
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow_missing_step)
    assert any(
        "static-analysis job must include step `Registration completeness`" in err for err in errors
    )

    ci_workflow_softened = """
    jobs:
      static-analysis:
        steps:
          - name: Registration completeness
            continue-on-error: true
            run: python -m tensor_grep.core.registration_check .tg-registration.toml
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow_softened)
    assert any(
        "Registration completeness` step must not set `continue-on-error: true`" in err
        for err in errors
    )


def test_should_accept_registration_completeness_step_without_continue_on_error():
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
          - name: Registration completeness
            run: python -m tensor_grep.core.registration_check .tg-registration.toml
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert not any("Registration completeness" in err for err in errors)


def test_should_require_concurrency_group_to_isolate_scheduled_runs():
    # Audit #39: a concurrency group keyed only on `github.ref` puts the weekly `schedule`
    # trigger in the SAME group as a merge-triggered push run, letting a queued weekly run widen
    # the known release push-race window. The group must key `schedule` into its own bucket.
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}
      cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
    jobs:
      release:
        needs: []
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "concurrency `group` must key the `schedule` trigger into its own group" in err
        for err in errors
    )


def test_should_accept_concurrency_group_that_isolates_scheduled_runs():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = """
    concurrency:
      group: ${{ github.workflow }}-${{ github.event_name == 'schedule' && 'schedule' || github.ref }}
      cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
    jobs:
      release:
        needs: []
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert not any("concurrency" in err for err in errors)


def test_should_require_ast_grep_version_parity_between_ci_and_benchmark_workflows():
    # Audit #40: ci.yml's agent-readiness ast-grep probe and benchmark.yml's ast-grep comparator
    # are pinned independently (a comment says "matches the pinned version" with no enforcement),
    # so one file's version can drift from the other silently.
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow_content = "run: cargo install ast-grep --version 0.41.1 --locked"
    benchmark_workflow_content = "run: cargo install ast-grep --version 0.40.0 --locked"
    errors = module.validate_ast_grep_version_parity(
        ci_workflow_content=ci_workflow_content,
        benchmark_workflow_content=benchmark_workflow_content,
    )
    assert any("ast-grep CLI version pin mismatch" in err for err in errors)
    assert any("0.41.1" in err and "0.40.0" in err for err in errors)


def test_should_accept_ast_grep_version_parity_when_pins_match():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow_content = "run: cargo install ast-grep --version 0.41.1 --locked"
    benchmark_workflow_content = "run: cargo install ast-grep --version 0.41.1 --locked"
    errors = module.validate_ast_grep_version_parity(
        ci_workflow_content=ci_workflow_content,
        benchmark_workflow_content=benchmark_workflow_content,
    )
    assert errors == []


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


def test_should_require_benchmark_regression_to_build_verified_native_binaries():
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
          - name: Build benchmark native binary
            working-directory: .
            run: cargo build --release
          - name: Determine benchmark base revision
            run: |
              echo "${{ github.event.pull_request.base.sha }}"
              echo "${{ github.event.before }}"
          - name: Checkout base revision for same-runner benchmark comparison
            uses: actions/checkout@v6
            with:
              ref: ${{ steps.benchmark-base.outputs.base_sha }}
              path: base-revision
          - name: Install base benchmark dependencies
            run: |
              cd base-revision
              uv venv --python 3.12
              uv pip install -e ".[bench,dev]"
          - name: Build base benchmark native binary
            working-directory: base-revision
            run: cargo build --release
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
                --baseline base-revision/artifacts/bench_run_benchmarks.base.json \
                --current artifacts/bench_run_benchmarks.head.json
          - name: Report accepted benchmark baseline drift
            continue-on-error: true
            run: |
              uv run python benchmarks/check_regression.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.head.json
          - name: Build benchmark markdown summary
            run: |
              uv run python benchmarks/summarize_benchmarks.py \
                --baseline auto \
                --current artifacts/bench_run_benchmarks.head.json
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "benchmark-regression `Build benchmark native binary` step must run "
        "`cargo build --release --no-default-features`" in joined_errors
    )
    assert (
        "benchmark-regression `Build benchmark native binary` step must set "
        "`working-directory: rust_core`" in joined_errors
    )
    assert (
        "benchmark-regression `Build base benchmark native binary` step must run "
        "`cargo build --release --no-default-features`" in joined_errors
    )
    assert (
        "benchmark-regression `Build base benchmark native binary` step must set "
        "`working-directory: base-revision/rust_core`" in joined_errors
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


def test_should_require_ci_semantic_release_github_asset_jobs():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace("  build-release-native-assets:", "  old-assets:", 1)
    ci_workflow = ci_workflow.replace("  publish-github-release-assets:", "  old-upload:", 1)

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow must define build-release-native-assets job for semantic-release GitHub assets"
        in joined_errors
    )
    assert (
        "CI workflow must define publish-github-release-assets job for semantic-release GitHub assets"
        in joined_errors
    )


def test_should_require_ci_release_native_assets_to_use_rust_frontdoor_not_nuitka():
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
      build-release-native-assets:
        needs: release
        if: needs.release.outputs.release_version != ''
        steps:
          - uses: actions/checkout@v6
            with:
              ref: v${{ needs.release.outputs.release_version }}
          - name: Build native release front door
            run: uv run python scripts/build_binaries.py
          - name: Package native release front door
            run: |
              echo tg-linux-amd64-cpu
              echo tg-macos-amd64-cpu
              echo tg-windows-amd64-cpu.exe
          - name: Upload native release front door
            uses: actions/upload-artifact@v7
      publish-github-release-assets:
        needs: [release, build-release-native-assets]
        permissions:
          contents: write
        steps:
          - uses: actions/checkout@v6
            with:
              ref: v${{ needs.release.outputs.release_version }}
          - name: Download native release front doors
            uses: actions/download-artifact@v8
            with:
              pattern: release-native-*
          - name: Validate native release asset matrix and generate checksums
            run: uv run python scripts/validate_release_binary_artifacts.py --expected-profile native-frontdoor --checksums-out artifacts/CHECKSUMS.txt
          - name: Upload GitHub release native assets
            uses: softprops/action-gh-release@v3
            with:
              tag_name: v${{ needs.release.outputs.release_version }}
              files: |
                artifacts/native/**/tg-*
                artifacts/CHECKSUMS.txt
                artifacts/package-manager-bundle/homebrew-tap/Formula/tensor-grep.rb
                artifacts/package-manager-bundle/PUBLISH_INSTRUCTIONS.md
                artifacts/package-manager-bundle/BUNDLE_CHECKSUMS.txt
          - name: Verify GitHub release native asset coverage
            run: uv run python scripts/verify_github_release_assets.py --expected-profile native-frontdoor --wait-seconds 120 --poll-interval-seconds 5
      publish-pypi:
        needs: [release, publish-github-release-assets]
      publish-success-gate:
        if: always()
        needs: [release, publish-pypi, publish-github-release-assets]
        steps:
          - name: Download all distributions
            if: needs.release.outputs.release_version != '' && needs.release.outputs.publish_pypi == 'true'
          - name: Confirm GitHub release asset job result when publishing is required
            run: echo ok
          - name: Verify GitHub release native assets for semantic-release version
            run: uv run python scripts/verify_github_release_assets.py --expected-profile native-frontdoor --wait-seconds 120 --poll-interval-seconds 5
          - name: Verify PyPI parity for semantic-release version (always)
            run: uv run python scripts/validate_release_version_parity.py --expected-version 1 --expected-tag v1 --check-pypi --pypi-wait-seconds 180 --pypi-poll-interval-seconds 10 --dist-dir dist
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow build-release-native-assets must use Rust native front doors, not the old Nuitka builder"
        in joined_errors
    )
    assert (
        "CI workflow build-release-native-assets `Build native release front door` step must invoke `cargo build --release ${{ matrix.cargo_args }}`"
        in joined_errors
    )


def test_should_require_ci_release_assets_to_gate_on_semantic_release_released_output():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        "      released: ${{ steps.publish_check.outputs.released }}\n",
        "",
        1,
    )
    ci_workflow = ci_workflow.replace(
        "          SEMANTIC_RELEASED: ${{ steps.release.outputs.released }}\n",
        "",
        1,
    )
    ci_workflow = ci_workflow.replace(
        "    if: needs.release.outputs.released == 'true'",
        "    if: needs.release.outputs.release_version != ''",
        2,
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert "CI workflow release job must expose semantic-release `released` output" in joined_errors
    assert (
        "CI workflow Determine PyPI Publish Need step must read `steps.release.outputs.released`"
        in joined_errors
    )
    assert (
        "CI workflow build-release-native-assets job must run only when semantic-release reports `released == 'true'`"
        in joined_errors
    )
    assert (
        "CI workflow publish-github-release-assets job must run only when semantic-release reports `released == 'true'`"
        in joined_errors
    )


def test_should_require_ci_macos_native_frontdoor_to_use_intel_runner_label():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        "- os: macos-15-intel\n"
        "            acceleration: cpu\n"
        "            cargo_args: --no-default-features\n"
        "            asset_name: tg-macos-amd64-cpu",
        "- os: macos-latest\n"
        "            acceleration: cpu\n"
        "            cargo_args: --no-default-features\n"
        "            asset_name: tg-macos-amd64-cpu",
        1,
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "CI workflow build-release-native-assets matrix must use an Intel macOS runner label" in err
        for err in errors
    )


def test_should_require_ci_native_build_smoke_to_use_intel_runner_label():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        "os: [ubuntu-latest, windows-latest, macos-latest, macos-15-intel]",
        "os: [ubuntu-latest, windows-latest, macos-latest]",
        1,
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "CI workflow native-build-smoke matrix must include Intel macOS runner label" in err
        for err in errors
    )


def test_should_configure_semantic_release_native_assets_for_optional_gpu_profile():
    root = Path(__file__).resolve().parents[2]
    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    assert "TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE" in ci_workflow
    assert "vars.TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE || 'native-frontdoor'" in ci_workflow
    assert "native-frontdoor-gpu" in ci_workflow
    assert "matrix.acceleration" in ci_workflow
    assert (
        "matrix.acceleration == 'cpu' || env.RELEASE_NATIVE_ASSET_PROFILE == 'native-frontdoor-gpu'"
        in ci_workflow
    )
    assert "release-native-${{ matrix.os }}-${{ matrix.acceleration }}" in ci_workflow
    assert "tg-linux-amd64-nvidia" in ci_workflow
    assert "tg-windows-amd64-nvidia.exe" in ci_workflow
    assert "tg-macos-amd64-nvidia" not in ci_workflow
    assert "--features cuda" in ci_workflow


def test_should_require_ci_release_asset_verifiers_to_use_selectable_native_profile():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        '--expected-profile "$RELEASE_NATIVE_ASSET_PROFILE"',
        "--expected-profile native-frontdoor",
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow publish-github-release-assets native asset validation and verification "
        "steps must use selectable `$RELEASE_NATIVE_ASSET_PROFILE`" in joined_errors
    )


def test_should_reject_ci_release_native_macos_nvidia_asset():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    ci_workflow = ci_workflow.replace(
        "- os: macos-15-intel\n"
        "            acceleration: cpu\n"
        "            cargo_args: --no-default-features\n"
        "            asset_name: tg-macos-amd64-cpu",
        "- os: macos-15-intel\n"
        "            acceleration: nvidia\n"
        "            cargo_args: --features cuda\n"
        "            asset_name: tg-macos-amd64-nvidia",
        1,
    )

    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    assert any(
        "CI workflow build-release-native-assets must keep macOS CPU-only" in err for err in errors
    )


def test_should_require_pypi_publish_to_wait_for_github_release_assets():
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
        needs: [release, build-wheels-pypi]
    """
    errors = module.validate_ci_workflow_content(ci_workflow=ci_workflow)
    joined_errors = "\n".join(errors)
    assert (
        "CI workflow publish-pypi job must depend on publish-github-release-assets so PyPI cannot publish before GitHub release assets are verified"
        in joined_errors
    )
