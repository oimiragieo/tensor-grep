"""publish npm/docs jobs, success gate, public GPU proof, SHA pins."""

import importlib.util
import textwrap
from pathlib import Path

from tests.unit.test_release_assets_validation_shared import _detag


def test_should_require_create_release_download_artifacts_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    ci_workflow = _detag((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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

    release_workflow = _detag(
        (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
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


def test_should_validate_public_gpu_proof_workflow_contract():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = _detag(
        (root / ".github" / "workflows" / "public-gpu-proof.yml").read_text(encoding="utf-8")
    )

    assert module.validate_public_gpu_proof_workflow_content(workflow_content=workflow) == []


def test_should_reject_public_gpu_proof_workflow_without_environment_gate():
    import yaml

    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow_path = root / ".github" / "workflows" / "public-gpu-proof.yml"
    parsed = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = parsed["jobs"]["public-managed-gpu-proof"]
    job.pop("environment", None)
    workflow = yaml.safe_dump(parsed, sort_keys=False)

    errors = module.validate_public_gpu_proof_workflow_content(workflow_content=workflow)
    assert any(
        "Public GPU proof workflow must target `environment: public-gpu-proof`" in err
        for err in errors
    )


def test_should_reject_public_gpu_proof_workflow_without_dispatch_only_fixed_runner():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    workflow = textwrap.dedent(
        """
        name: Public GPU Proof
        on: [push, workflow_dispatch]
        permissions:
          contents: write
        jobs:
          public-managed-gpu-proof:
            runs-on: ${{ inputs.runner }}
            steps:
              - run: uv run python benchmarks/run_gpu_native_benchmarks.py
        """
    )

    errors = module.validate_public_gpu_proof_workflow_content(workflow_content=workflow)
    joined_errors = "\n".join(errors)
    assert "Public GPU proof workflow must be workflow_dispatch-only" in joined_errors
    assert "Public GPU proof workflow must request read-only contents permission" in joined_errors
    assert "Public GPU proof workflow must use fixed GPU runner labels" in joined_errors
    assert "Public GPU proof workflow must run with --public-managed-proof" in joined_errors


def test_validate_actions_sha_pinned_passes_and_exempts_rust_toolchain():
    """Supply-chain hardening: every third-party action in the repo's workflows must be
    pinned to a 40-hex commit SHA, and dtolnay/rust-toolchain@stable must stay exempt
    (its @stable is a moving ref by design)."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.validate_actions_sha_pinned() == []
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "dtolnay/rust-toolchain@stable" in ci


def test_validate_homebrew_formula_contract_validates_sha256_if_present():
    """audit MED: a sha256 in the formula must be a lowercase 64-hex digest (validate IF-PRESENT;
    the source template legitimately carries none until stamped from CHECKSUMS at bundle time)."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    base = (
        "class TensorGrep < Formula\n"
        '  TENSOR_GREP_VERSION = "1.2.3"\n'
        "  version TENSOR_GREP_VERSION\n"
    )
    bad = base + '  sha256 "NOTAHEX"\nend\n'
    assert any(
        "64-hex" in e
        for e in module.validate_homebrew_formula_contract(brew_content=bad, py_version="1.2.3")
    )
    good = base + '  sha256 "' + ("a" * 64) + '"\nend\n'
    assert not any(
        "sha256" in e
        for e in module.validate_homebrew_formula_contract(brew_content=good, py_version="1.2.3")
    )
    # source template with NO sha256 must NOT error (chicken-and-egg).
    assert not any(
        "sha256" in e
        for e in module.validate_homebrew_formula_contract(
            brew_content=base + "end\n", py_version="1.2.3"
        )
    )
