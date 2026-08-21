"""CI uv pins, Dependabot, audit.yml, and security-floor contracts."""

import importlib.util
import textwrap
from pathlib import Path

from tests.unit.test_release_assets_validation_shared import _detag


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


def test_should_reject_unpinned_uv_bootstrap_in_ci():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    unpinned = """
    jobs:
      x:
        steps:
          - run: python -m pip install uv
          - run: python -m pip install uv
    """
    assert any(
        "must pin uv" in err for err in module.validate_ci_workflow_content(ci_workflow=unpinned)
    )

    pinned = """
    jobs:
      x:
        steps:
          - run: python -m pip install uv==0.11.25
          - run: python -m pip install uv==0.11.25
    """
    assert not any(
        "must pin uv" in err for err in module.validate_ci_workflow_content(ci_workflow=pinned)
    )


def test_should_reject_unpinned_uv_in_public_gpu_proof_workflow():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    unpinned = "steps:\n  - run: python -m pip install uv\n"
    assert any(
        "pin uv" in err
        for err in module.validate_public_gpu_proof_workflow_content(workflow_content=unpinned)
    )
    pinned = "steps:\n  - run: python -m pip install uv==0.11.25\n"
    assert not any(
        "pin uv" in err
        for err in module.validate_public_gpu_proof_workflow_content(workflow_content=pinned)
    )


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
    assert "gh pr merge" in joined_errors


def test_should_require_dependabot_automation_explicit_repo_targeting():
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
          - name: Ensure dependency labels exist
            run: gh label create dependencies --color 0366d6 --description "Dependency maintenance" --force
          - name: Apply dependency labels
            run: gh pr edit "$PR_URL" --add-label "dependencies"
          - name: Determine automerge policy
            run: echo "safe=false" >> "$GITHUB_OUTPUT"
          - name: Mark safe updates
            run: gh pr edit "$PR_URL" --add-label "automerge:eligible"
          - name: Mark manual review updates
            run: gh pr edit "$PR_URL" --add-label "manual-review"
          - name: Approve safe updates
            run: gh pr review "$PR_URL" --approve
          - name: Enable auto-merge for safe updates
            run: gh pr merge --auto --squash "$PR_URL"
    """
    errors = module.validate_dependabot_automation_workflow_content(
        workflow_content=textwrap.dedent(workflow)
    )
    joined_errors = "\n".join(errors)
    assert "must define `GH_REPO: ${{ github.repository }}`" in joined_errors
    assert 'must pass `--repo "$GH_REPO"` to every `gh` command' in joined_errors


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


def test_should_require_audit_workflow_isolated_pip_audit_tool_run():
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
          - name: Export Python audit requirements
            run: uv export --format requirements.txt --all-extras --no-emit-project --output-file "$RUNNER_TEMP/python-audit-requirements.txt" --locked
          - name: Run pip-audit
            run: uv run pip-audit -r "$RUNNER_TEMP/python-audit-requirements.txt"
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
    assert "uv run --no-project" in joined_errors
    assert "--with pip-audit" in joined_errors
    assert "--progress-spinner off" in joined_errors


def test_should_require_audit_workflow_checkout_to_use_pr_head_ref():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = _detag((root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8"))
    workflow = workflow.replace(
        "      - uses: actions/checkout@v6\n"
        "        with:\n"
        "          repository: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.full_name || github.repository }}\n"
        "          ref: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha }}\n",
        "      - uses: actions/checkout@v6\n",
        1,
    )

    errors = module.validate_audit_workflow_content(workflow_content=workflow)
    joined_errors = "\n".join(errors)
    assert "pull request head repository/ref" in joined_errors


def test_should_require_audit_workflow_locked_requirements_export_before_pip_audit():
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
          - name: Run cargo audit
            run: cargo audit
          - name: Run cargo deny
            run: cargo deny check
          - name: Install uv
            uses: astral-sh/setup-uv@v8.0.0
          - name: Setup Python
            run: uv python install 3.12
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
    assert "Export Python audit requirements" in joined_errors
    assert "uv export --format requirements.txt" in joined_errors
    assert "$RUNNER_TEMP/python-audit-requirements.txt" in joined_errors
    assert "--disable-pip" in joined_errors


def test_should_reject_audit_workflow_legacy_scanner_environment_steps():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = _detag((root / ".github" / "workflows" / "audit.yml").read_text(encoding="utf-8"))
    workflow = workflow.replace(
        "      - name: Export Python audit requirements\n",
        "      - name: Create Python audit environment\n"
        "        run: uv venv --python 3.12\n\n"
        "      - name: Install pip-audit\n"
        "        run: uv pip install pip-audit\n\n"
        "      - name: Legacy Run pip-audit\n"
        "        run: uv run pip-audit\n\n"
        "      - name: Export Python audit requirements\n",
        1,
    )

    errors = module.validate_audit_workflow_content(workflow_content=workflow)
    joined_errors = "\n".join(errors)
    assert "must not create a project audit environment" in joined_errors
    assert "must not install pip-audit into the project environment" in joined_errors
    assert "must not run legacy project-environment `uv run pip-audit`" in joined_errors


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
    assert "cryptography>=50.0.0" in joined_errors
    assert "pygments>=2.20.0" in joined_errors
    assert "python-multipart>=0.0.31" in joined_errors
    assert "python-dotenv>=1.2.2" in joined_errors
    assert "aiohttp>=3.14.3" in joined_errors
    assert "pyjwt>=2.13.0" in joined_errors
    assert "starlette>=1.3.1" in joined_errors
    assert "pydantic-settings>=2.14.2" in joined_errors


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
    dependencies = ["cryptography>=50.0.0"]

    [project.optional-dependencies]
    nlp = ["tritonclient[http]", "aiohttp>=3.14.3"]

    [tool.uv]
    constraint-dependencies = [
      "cryptography>=50.0.0",
      "pygments>=2.20.0",
      "python-multipart>=0.0.31",
      "python-dotenv>=1.2.2",
      "requests>=2.33.0",
      "aiohttp>=3.14.3",
      "pyjwt>=2.13.0",
      "starlette>=1.3.1",
      "pydantic-settings>=2.14.2",
    ]
    """
    errors = module.validate_uv_security_constraints(pyproject_content=textwrap.dedent(pyproject))
    assert errors == []


def test_should_reject_stale_direct_cryptography_floor_when_uv_constraint_is_secure():
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
    dependencies = ["cryptography>=48.0.1"]

    [project.optional-dependencies]
    nlp = ["tritonclient[http]", "aiohttp>=3.14.3"]

    [tool.uv]
    constraint-dependencies = [
      "cryptography>=50.0.0",
      "pygments>=2.20.0",
      "python-multipart>=0.0.31",
      "python-dotenv>=1.2.2",
      "requests>=2.33.0",
      "aiohttp>=3.14.3",
      "pyjwt>=2.13.0",
      "starlette>=1.3.1",
      "pydantic-settings>=2.14.2",
    ]
    """
    errors = module.validate_uv_security_constraints(pyproject_content=textwrap.dedent(pyproject))
    assert errors == [
        "pyproject.toml [project].dependencies missing direct security floor: cryptography>=50.0.0"
    ]


def test_should_reject_lock_only_aiohttp_floor_absent_from_the_published_nlp_extra():
    """A `[tool.uv]` constraint is lock-only; it never reaches `pip install tensor-grep[nlp]`.

    This is the exact shape that shipped green before 2026-08-03: every constraint entry present,
    `cryptography` correctly declared directly, and the aiohttp advisory floor STILL unreachable by
    a PyPI installer because `nlp` only declares `tritonclient[http]` (which itself permits
    `aiohttp>=3.8.1,<4`). Both audit gates passed on that state, so the validator is the only thing
    that can tell the two apart.
    """
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
    dependencies = ["cryptography>=50.0.0"]

    [project.optional-dependencies]
    nlp = ["tritonclient[http]"]

    [tool.uv]
    constraint-dependencies = [
      "cryptography>=50.0.0",
      "pygments>=2.20.0",
      "python-multipart>=0.0.31",
      "python-dotenv>=1.2.2",
      "requests>=2.33.0",
      "aiohttp>=3.14.3",
      "pyjwt>=2.13.0",
      "starlette>=1.3.1",
      "pydantic-settings>=2.14.2",
    ]
    """
    errors = module.validate_uv_security_constraints(pyproject_content=textwrap.dedent(pyproject))
    assert errors == [
        "pyproject.toml [project.optional-dependencies].nlp missing published security floor: "
        "aiohttp>=3.14.3 (a [tool.uv] constraint is lock-only and does not reach a PyPI installer)"
    ]


def test_should_require_dev_tooling_security_floors_for_ci_format_parity():
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

    [project.optional-dependencies]
    dev = ["ruff>=0.6"]
    bench = ["pytest>=8.0"]
    """
    errors = module.validate_dev_tooling_constraints(pyproject_content=textwrap.dedent(pyproject))
    joined_errors = "\n".join(errors)
    assert "ruff==0.15.20" in joined_errors
    assert "mypy==1.19.1" in joined_errors
    assert "pytest>=9.0.3" in joined_errors


def test_should_accept_dev_tooling_security_floors_for_ci_format_parity():
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

    [project.optional-dependencies]
    dev = ["ruff==0.15.20", "mypy==1.19.1", "pytest>=9.0.3"]
    bench = ["pytest>=9.0.3"]
    """
    errors = module.validate_dev_tooling_constraints(pyproject_content=textwrap.dedent(pyproject))
    assert errors == []


def test_should_require_exact_mypy_pin_not_a_floor_constraint():
    # Regression guard for #446 (a loosened mypy dependency merged green, then the next mypy
    # release turned main red). A floor like `mypy>=1.11` must NOT satisfy the pin -- only the
    # exact `mypy==1.19.1` pin does.
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

    [project.optional-dependencies]
    dev = ["ruff==0.15.20", "mypy>=1.11", "pytest>=9.0.3"]
    bench = ["pytest>=9.0.3"]
    """
    errors = module.validate_dev_tooling_constraints(pyproject_content=textwrap.dedent(pyproject))
    joined_errors = "\n".join(errors)
    assert "mypy==1.19.1" in joined_errors
