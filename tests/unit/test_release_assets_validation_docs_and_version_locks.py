"""README / lockfile / release-doc / winget consistency contracts."""

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


def test_should_require_uv_lock_editable_version_to_match_pyproject():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._version_from_pyproject = lambda: "1.3.2"
    module._version_from_cargo = lambda: "1.3.2"
    module._version_from_cargo_lock = lambda: "1.3.2"
    real_read = module._read

    def fake_read(path: Path) -> str:
        if path == module.ROOT / "npm" / "package.json":
            return (
                '{"version": "1.3.2", '
                '"repository": {"url": "git+https://github.com/oimiragieo/tensor-grep.git"}}'
            )
        if path == module.ROOT / "uv.lock":
            return (
                "[[package]]\n"
                'name = "tensor-grep"\n'
                'version = "1.3.1"\n'
                'source = { editable = "." }\n'
            )
        return real_read(path)

    module._read = fake_read
    errors = module.validate_all()
    assert any(
        "uv.lock editable tensor-grep version does not match pyproject version" in err
        for err in errors
    )


def test_should_require_cargo_lock_version_to_match_pyproject():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._version_from_pyproject = lambda: "1.3.2"
    module._version_from_cargo = lambda: "1.3.2"
    module._version_from_cargo_lock = lambda: "1.3.1"
    module._version_from_uv_lock = lambda: "1.3.2"
    real_read = module._read

    def fake_read(path: Path) -> str:
        if path == module.ROOT / "npm" / "package.json":
            return (
                '{"version": "1.3.2", '
                '"repository": {"url": "git+https://github.com/oimiragieo/tensor-grep.git"}}'
            )
        return real_read(path)

    module._read = fake_read
    errors = module.validate_all()
    assert any(
        "rust_core/Cargo.lock tensor_grep_rs version does not match pyproject version" in err
        for err in errors
    )


def test_should_require_semantic_release_build_to_refresh_uv_lock():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pyproject_content = textwrap.dedent(
        """
        [project]
        name = "tensor-grep"
        version = "1.7.1"

        [tool.semantic_release]
        version_toml = [
            "pyproject.toml:project.version",
            "rust_core/Cargo.toml:package.version",
        ]
        build_command = "python scripts/stamp_release_assets.py && pip install uv && uv build"
        version_variables = [
            "src/tensor_grep/cli/main.py:pkg_version",
            "npm/package.json:version",
            "scripts/tensor-grep.rb:TENSOR_GREP_VERSION",
            "scripts/oimiragieo.tensor-grep.yaml:PackageVersion",
            "scripts/oimiragieo.tensor-grep.yaml:InstallerUrl",
            "AGENTS.md:release_docs_current_tag:tf",
            "README.md:release_docs_current_tag:tf",
            "SKILL.md:release_docs_current_tag:tf",
            "docs/SESSION_HANDOFF.md:release_docs_current_tag:tf",
            "docs/CONTINUATION_PLAN.md:release_docs_current_tag:tf",
            "docs/CONTRACTS.md:release_docs_current_tag:tf",
        ]
        """
    )

    errors = module.validate_semantic_release_config(pyproject_content=pyproject_content)
    joined_errors = "\n".join(errors)

    assert (
        "semantic_release.build_command must run `uv lock --upgrade-package tensor-grep`"
        in joined_errors
    )
    assert "semantic_release.build_command must stage `uv.lock`" in joined_errors
    assert (
        "semantic_release.build_command must run "
        "`cargo generate-lockfile --manifest-path rust_core/Cargo.toml`"
    ) in joined_errors
    assert "semantic_release.build_command must stage `rust_core/Cargo.lock`" in joined_errors
    assert "semantic_release.build_command must stage release docs after stamping" in joined_errors


def test_should_reject_release_docs_with_stale_latest_release_labels():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    errors = module.validate_release_docs_current_prose(
        documents={
            "README.md": (
                "release_docs_current_tag: v1.10.7\n"
                "Latest tagged GitHub release: [`v1.10.6`](https://example.test/v1.10.6).\n"
                "Latest complete PyPI release: [`v1.10.6`](https://example.test/v1.10.6).\n"
            ),
            "docs/SESSION_HANDOFF.md": (
                "release_docs_current_tag: v1.10.7\n"
                "- Latest tagged version: `v1.10.6`\n"
                "- Latest complete PyPI version: `v1.10.6`\n"
            ),
        },
        expected_version="1.10.7",
    )

    assert any("stale latest tagged GitHub release" in error for error in errors)
    assert any("stale latest complete PyPI release" in error for error in errors)
    assert any("stale latest tagged version" in error for error in errors)
    assert any("stale latest complete PyPI version" in error for error in errors)


def test_should_allow_latest_complete_pypi_lag_when_current_tag_publication_failed():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    caveat = (
        "`v1.11.0` asset/PyPI publication did not complete; "
        "`publish-success-gate` failed and PyPI latest remains `1.10.10`."
    )
    errors = module.validate_release_docs_current_prose(
        documents={
            "README.md": (
                "release_docs_current_tag: v1.11.0\n"
                "Latest tagged GitHub release: [`v1.11.0`](https://example.test/v1.11.0).\n"
                "Latest complete PyPI release: [`v1.10.10`](https://example.test/v1.10.10).\n"
                f"{caveat}\n"
            ),
            "docs/SESSION_HANDOFF.md": (
                "release_docs_current_tag: v1.11.0\n"
                "- Latest tagged version: `v1.11.0`\n"
                "- Latest complete PyPI version: `v1.10.10`\n"
                f"{caveat}\n"
            ),
        },
        expected_version="1.11.0",
    )

    assert errors == []


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


def test_should_reject_readme_current_release_asset_list_with_gpu_binaries():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = """
    # tensor-grep

    `tensor-grep` has first class support on Windows, macOS and Linux.

    Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md).

    ## Canonical Docs

    - [docs/benchmarks.md](docs/benchmarks.md)
    - [docs/tool_comparison.md](docs/tool_comparison.md)
    - [docs/gpu_crossover.md](docs/gpu_crossover.md)
    - [docs/routing_policy.md](docs/routing_policy.md)
    - [docs/harness_api.md](docs/harness_api.md)
    - [docs/harness_cookbook.md](docs/harness_cookbook.md)
    - [docs/installation.md](docs/installation.md)
    - [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)

    Current release assets include:
    * `tg-windows-amd64-cpu.exe`
    * `tg-windows-amd64-nvidia.exe`
    * `tg-linux-amd64-cpu`
    * `tg-linux-amd64-nvidia`
    * `tg-macos-amd64-cpu`
    """
    errors = module.validate_readme_contract(readme_content=readme)
    assert any(
        "README current release asset list must only advertise CPU front doors" in err
        for err in errors
    )


def test_should_reject_readme_faster_than_rg_positioning():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readme = """
    # tensor-grep

    `tensor-grep` has first class support on Windows, macOS and Linux.

    Harness consumers should use the documented public contracts in [docs/harness_api.md](docs/harness_api.md).

    ## Canonical Docs

    - [docs/benchmarks.md](docs/benchmarks.md)
    - [docs/tool_comparison.md](docs/tool_comparison.md)
    - [docs/gpu_crossover.md](docs/gpu_crossover.md)
    - [docs/routing_policy.md](docs/routing_policy.md)
    - [docs/harness_api.md](docs/harness_api.md)
    - [docs/harness_cookbook.md](docs/harness_cookbook.md)
    - [docs/installation.md](docs/installation.md)
    - [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)

    `tensor-grep` is designed to win on larger files, repeated queries, AST workflows,
    and harness loops.
    """

    errors = module.validate_readme_contract(readme_content=readme)
    assert any("README must position tg as agent-native code intelligence" in err for err in errors)


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
        "# yaml-language-server: "
        "$schema=https://aka.ms/winget-manifest.singleton.1.12.0.schema.json\n"
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "PackageLocale: en-US\n"
        "PackageName: tensor-grep\n"
        "Publisher: oimiragieo\n"
        "License: MIT\n"
        "ShortDescription: Fast search for agent workflows\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
        "    InstallerSha256: "
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "ManifestType: singleton\n"
        "ManifestVersion: 1.12.0\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert errors == []


def test_should_fail_winget_manifest_when_installer_sha256_missing():
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
    assert any("InstallerSha256" in err for err in errors)


def test_should_fail_winget_manifest_when_installer_url_not_nested():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", script_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    winget = (
        "# yaml-language-server: "
        "$schema=https://aka.ms/winget-manifest.singleton.1.12.0.schema.json\n"
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "PackageLocale: en-US\n"
        "PackageName: tensor-grep\n"
        "Publisher: oimiragieo\n"
        "License: MIT\n"
        "ShortDescription: Fast search for agent workflows\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerSha256: "
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n"
        "ManifestType: singleton\n"
        "ManifestVersion: 1.12.0\n"
    )
    errors = module.validate_winget_manifest(winget_content=winget, py_version="1.2.3")
    assert any("InstallerUrl must be nested under first installer mapping" in err for err in errors)
