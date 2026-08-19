"""Docs/manifest/packaging content checks: winget, package-manager docs,
installation docs, npm installer/manifest contracts, CI-pipeline doc parity,
ast-grep version parity, README, release-docs prose, benchmarks docs, Homebrew
formula, uv security constraints, dev-tooling constraints, and semantic-release
config."""

from __future__ import annotations

import json
import re

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from .constants import _AST_GREP_VERSION_PIN_RE, RELEASE_DOC_PATHS, WINGET_SINGLETON_SCHEMA_HEADER


def validate_winget_manifest(*, winget_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    if "PLACEHOLDER" in winget_content:
        errors.append("Winget manifest contains unresolved PLACEHOLDER text")
    try:
        parsed_winget = yaml.safe_load(winget_content) or {}
    except yaml.YAMLError as exc:
        errors.append(f"Winget manifest is not valid YAML: {exc}")
        return errors
    if not isinstance(parsed_winget, dict):
        errors.append("Winget manifest must deserialize to a mapping")
        return errors

    package_version = parsed_winget.get("PackageVersion")
    if str(package_version) != py_version:
        errors.append("Winget manifest PackageVersion does not match pyproject version")

    expected_windows_url = (
        f"https://github.com/oimiragieo/tensor-grep/releases/download/v{py_version}/"
        "tg-windows-amd64-cpu.exe"
    )
    if expected_windows_url not in winget_content:
        errors.append("Winget manifest InstallerUrl does not match expected release artifact URL")

    manifest_type = parsed_winget.get("ManifestType")
    is_singleton = manifest_type == "singleton" or winget_content.startswith(
        f"{WINGET_SINGLETON_SCHEMA_HEADER}\n"
    )
    if is_singleton:
        if not winget_content.startswith(f"{WINGET_SINGLETON_SCHEMA_HEADER}\n"):
            errors.append("Winget manifest must start with the singleton 1.12.0 schema header")

        expected_top_level = {
            "PackageIdentifier": "oimiragieo.tensor-grep",
            "PackageLocale": "en-US",
            "PackageName": "tensor-grep",
            "Publisher": "oimiragieo",
            "License": "MIT",
            "ManifestType": "singleton",
            "ManifestVersion": "1.12.0",
        }
        for field_name, expected_value in expected_top_level.items():
            if parsed_winget.get(field_name) != expected_value:
                errors.append(f"Winget manifest {field_name} must be {expected_value!r}")

        for field_name in ("ShortDescription",):
            if not parsed_winget.get(field_name):
                errors.append(f"Winget manifest must define non-empty {field_name}")
    elif manifest_type == "installer":
        if parsed_winget.get("PackageIdentifier") != "oimiragieo.tensor-grep":
            errors.append("Winget manifest PackageIdentifier must be 'oimiragieo.tensor-grep'")
        if parsed_winget.get("ManifestVersion") != "1.6.0":
            errors.append("Winget manifest ManifestVersion must be '1.6.0'")

    installers = parsed_winget.get("Installers")
    if not isinstance(installers, list) or not installers:
        errors.append("Winget manifest must contain a non-empty Installers list")
        return errors

    first = installers[0]
    if not isinstance(first, dict):
        errors.append("Winget manifest first installer must be a mapping")
        return errors

    installer_url = first.get("InstallerUrl")
    if installer_url != expected_windows_url:
        errors.append("Winget manifest InstallerUrl must be nested under first installer mapping")
    installer_sha256 = first.get("InstallerSha256")
    if not isinstance(installer_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", installer_sha256
    ):
        errors.append("Winget manifest first installer must define 64-hex InstallerSha256")
    return errors


def validate_package_manager_docs(*, runbook_content: str, checklist_content: str) -> list[str]:
    errors: list[str] = []
    for heading in (
        "## Homebrew Tap Flow",
        "## Winget Flow",
        "## Rollback Procedures",
        "## Verification Commands",
    ):
        if heading not in runbook_content:
            errors.append(f"Package manager runbook missing required heading: {heading}")

    for marker in (
        "## 4. Package-manager distribution finalization",
        "## 5. Rollback runbook",
        "Homebrew",
        "Winget",
        "feat: ...` -> minor",
        "fix: ...` or `perf: ...` -> patch",
        "feat!: ...` / `fix!: ...` -> major",
        "Squash and merge",
    ):
        if marker not in checklist_content:
            errors.append(f"Release checklist missing package-manager marker: {marker}")

    for required_checklist_cmd in (
        "gh run list --limit 10",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
    ):
        if required_checklist_cmd not in checklist_content:
            errors.append(
                "Release checklist missing required operator verification command: "
                f"{required_checklist_cmd}"
            )

    for required_cmd in (
        "gh run list --limit 10",
        "uv run python scripts/prepare_package_manager_release.py --check",
        "ruby -c Formula/tensor-grep.rb",
        "winget validate --manifest",
        "winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\X.Y.Z\\",
        "uv run python scripts/verify_package_manager_bundle_checksums.py --bundle-dir",
        "uv run python scripts/smoke_test_package_manager_bundle.py --bundle-dir",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-pypi",
        "python scripts/validate_release_version_parity.py --expected-version X.Y.Z --expected-tag vX.Y.Z --check-npm",
    ):
        if required_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required verification/publish command: {required_cmd}"
            )

    for required_smoke_cmd in (
        "brew install oimiragieo/tap/tensor-grep",
        "winget install oimiragieo.tensor-grep",
        "tg --version",
    ):
        if required_smoke_cmd not in runbook_content:
            errors.append(
                f"Package manager runbook missing required smoke-install command: {required_smoke_cmd}"
            )

    for required_rollback_cmd in (
        "git revert <tap-formula-commit>",
        "git push origin <rollback-branch>",
        "brew update",
        "winget uninstall oimiragieo.tensor-grep",
    ):
        if required_rollback_cmd not in runbook_content:
            errors.append(
                "Package manager runbook missing required rollback command: "
                f"{required_rollback_cmd}"
            )

    if "npm/GitHub mismatch" not in runbook_content:
        errors.append("Package manager runbook missing npm/GitHub rollback guidance")
    return errors


def validate_installation_docs(*, installation_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "### Homebrew Tap Flow",
        "### Winget Flow",
        "### Repeatable Release Checklist",
        "### Rollback Playbook",
    ):
        if expected not in installation_content:
            errors.append(f"Installation docs missing package-manager section: {expected}")

    if "https://github.com/oimiragieo/tensor-grep/releases" not in installation_content:
        errors.append("Installation docs must point GitHub Releases link to oimiragieo/tensor-grep")

    if "--check-npm" not in installation_content:
        errors.append("Installation docs release automation notes must mention npm parity checks")

    for required_cmd in (
        "brew tap oimiragieo/tap",
        "brew install tensor-grep",
        "brew install oimiragieo/tap/tensor-grep",
        "winget validate --manifest",
        "winget-pkgs",
        "winget install oimiragieo.tensor-grep",
        "tg --version",
        "python scripts/verify_github_release_assets.py --repo oimiragieo/tensor-grep --tag vX.Y.Z",
        "git revert <tap-formula-commit>",
        "winget uninstall oimiragieo.tensor-grep",
    ):
        if required_cmd not in installation_content:
            errors.append(
                "Installation docs missing required package-manager command/reference: "
                f"{required_cmd}"
            )
    return errors


def validate_npm_installer_contract(*, install_js_content: str, expected_version: str) -> list[str]:
    errors: list[str] = []
    if "require('./package.json')" not in install_js_content:
        errors.append("npm/install.js must derive the release version from npm/package.json")
    if "oimiragieo/tensor-grep" not in install_js_content:
        errors.append("npm/install.js must download from oimiragieo/tensor-grep releases")
    expected_asset_markers = (
        "tg-windows-amd64-cpu.exe",
        "tg-linux-amd64-cpu",
        "tg-macos-amd64-cpu",
    )
    if not all(marker in install_js_content for marker in expected_asset_markers):
        errors.append("npm/install.js must reference current release asset names")
    if f"v{expected_version}" in install_js_content:
        errors.append("npm/install.js must not hardcode the tagged version string")
    return errors


def validate_npm_manifest_contract(
    *, package_json_content: str, available_paths: set[str]
) -> list[str]:
    errors: list[str] = []
    try:
        npm_manifest = json.loads(package_json_content)
    except json.JSONDecodeError as exc:
        return [f"npm/package.json is not valid JSON: {exc}"]
    if not isinstance(npm_manifest, dict):
        return ["npm/package.json must decode to an object"]

    if "main" in npm_manifest:
        errors.append(
            "npm/package.json must not declare `main`; the package ships a CLI wrapper only"
        )

    bin_field = npm_manifest.get("bin")
    if isinstance(bin_field, str):
        bin_targets = [bin_field]
    elif isinstance(bin_field, dict):
        bin_targets = [str(target) for target in bin_field.values()]
    else:
        errors.append("npm/package.json must declare a `bin` mapping for the CLI wrapper")
        bin_targets = []

    normalized_paths = {str(path).replace("\\", "/").lstrip("./") for path in available_paths}
    for target in sorted(set(bin_targets)):
        normalized_target = target.replace("\\", "/").lstrip("./")
        if normalized_target not in normalized_paths:
            errors.append(f"npm/package.json bin target must exist in npm/: {normalized_target}")

    dependencies = npm_manifest.get("dependencies", {})
    if dependencies is None:
        dependencies = {}
    if not isinstance(dependencies, dict):
        errors.append("npm/package.json dependencies must be an object when present")
    elif dependencies:
        errors.append(
            "npm/package.json wrapper runtime dependencies must be empty: "
            + ", ".join(sorted(str(name) for name in dependencies))
        )

    return errors


def validate_ci_pipeline_doc_contract(
    *, ci_pipeline_content: str, benchmark_workflow_content: str
) -> list[str]:
    errors: list[str] = []
    if (
        "name: Benchmarks" in benchmark_workflow_content
        and "benchmark.yml" not in ci_pipeline_content
    ):
        errors.append(
            "docs/CI_PIPELINE.md must document the live benchmark workflow (`benchmark.yml`)"
        )
    if "name: Benchmarks" in benchmark_workflow_content and "Benchmarks" not in ci_pipeline_content:
        errors.append("docs/CI_PIPELINE.md must describe the Benchmarks workflow responsibilities")
    return errors


def validate_ast_grep_version_parity(
    *, ci_workflow_content: str, benchmark_workflow_content: str
) -> list[str]:
    """ci.yml's agent-readiness ast-grep probe and benchmark.yml's ast-grep comparator must run the
    SAME ast-grep CLI version -- the two files only pin it in a comment as "matches the pinned
    version" with no enforcement, so one file's version can drift from the other silently."""
    errors: list[str] = []
    ci_match = _AST_GREP_VERSION_PIN_RE.search(ci_workflow_content)
    benchmark_match = _AST_GREP_VERSION_PIN_RE.search(benchmark_workflow_content)
    if ci_match is None:
        errors.append(
            "ci.yml must pin an ast-grep CLI version via `cargo install ast-grep --version <ver>`"
        )
    if benchmark_match is None:
        errors.append(
            "benchmark.yml must pin an ast-grep CLI version via "
            "`cargo install ast-grep --version <ver>`"
        )
    if (
        ci_match is not None
        and benchmark_match is not None
        and ci_match.group(1) != benchmark_match.group(1)
    ):
        errors.append(
            "ast-grep CLI version pin mismatch: ci.yml pins "
            f"{ci_match.group(1)} but benchmark.yml pins {benchmark_match.group(1)} "
            "(agent-readiness's ast-grep probe and the benchmark comparator must run the same "
            "ast-grep build)"
        )
    return errors


def validate_readme_contract(*, readme_content: str) -> list[str]:
    errors: list[str] = []
    # the canonical-docs section heading is matched case-insensitively (the README uses
    # "## Canonical docs"); the per-doc links below are the substantive requirement.
    if "## canonical docs" not in readme_content.lower():
        errors.append("README missing canonical docs section heading")
    for expected in (
        "[docs/benchmarks.md](docs/benchmarks.md)",
        "[docs/tool_comparison.md](docs/tool_comparison.md)",
        "[docs/gpu_crossover.md](docs/gpu_crossover.md)",
        "[docs/routing_policy.md](docs/routing_policy.md)",
        "[docs/harness_api.md](docs/harness_api.md)",
        "[docs/harness_cookbook.md](docs/harness_cookbook.md)",
    ):
        if expected not in readme_content:
            errors.append(f"README missing canonical docs reference: {expected}")

    if "[docs/installation.md](docs/installation.md)" not in readme_content:
        errors.append(
            "README must link installation docs: [docs/installation.md](docs/installation.md)"
        )

    if (
        "Windows, macOS, and Linux" not in readme_content
        and "Windows, macOS and Linux" not in readme_content
    ):
        errors.append("README must state platform support (Windows, macOS, Linux)")

    if "[docs/harness_api.md](docs/harness_api.md)" not in readme_content:
        errors.append("README must direct harness consumers to docs/harness_api.md")

    banned_positioning = [
        "designed to win on larger files",
        "faster than rg",
        "faster than `rg`",
        "GPU-ready",
        "GPU-accelerated",
    ]
    for fragment in banned_positioning:
        if fragment in readme_content:
            errors.append(
                "README must position tg as agent-native code intelligence with rg as "
                f"the cold exact-text baseline; found `{fragment}`"
            )

    if "Current release assets include:" in readme_content:
        asset_block = readme_content.split("Current release assets include:", 1)[1].split(
            "Operational notes:", 1
        )[0]
        gpu_asset_names = {
            "tg-linux-amd64-nvidia",
            "tg-windows-amd64-nvidia.exe",
        }
        advertised_gpu_assets = sorted(
            asset_name for asset_name in gpu_asset_names if asset_name in asset_block
        )
        if advertised_gpu_assets:
            errors.append(
                "README current release asset list must only advertise CPU front doors; "
                "unexpected GPU asset names: " + ", ".join(advertised_gpu_assets)
            )

    return errors


def validate_release_docs_current_prose(
    *, documents: dict[str, str], expected_version: str
) -> list[str]:
    errors: list[str] = []
    expected_tag = f"v{expected_version}"

    def allows_complete_public_release_lag(content: str, found_tag: str) -> bool:
        found_version = found_tag.removeprefix("v")
        publication_failed = (
            "asset/PyPI publication did not complete" in content
            or "`publish-pypi` did not complete" in content
        )
        return (
            publication_failed
            and "`publish-success-gate` failed" in content
            and f"PyPI latest remains `{found_version}`" in content
        )

    current_patterns = [
        re.compile(
            r"current `(?P<tag>v\d+\.\d+\.\d+)` "
            r"(?P<subject>shell/version resolution|positioning|release line)"
        ),
        re.compile(
            r"current tagged (?P<subject>version|release state) is "
            r"`(?P<tag>v\d+\.\d+\.\d+)`"
        ),
    ]
    latest_release_patterns = [
        (
            "latest tagged GitHub release",
            re.compile(
                r"Latest tagged GitHub release:\s*\[`(?P<tag>v\d+\.\d+\.\d+)`\]",
                re.IGNORECASE,
            ),
        ),
        (
            "latest complete PyPI release",
            re.compile(
                r"Latest complete PyPI release:\s*\[`(?P<tag>v\d+\.\d+\.\d+)`\]",
                re.IGNORECASE,
            ),
        ),
        (
            "latest tagged version",
            re.compile(
                r"Latest tagged version:\s*`(?P<tag>v\d+\.\d+\.\d+)`",
                re.IGNORECASE,
            ),
        ),
        (
            "latest complete PyPI version",
            re.compile(
                r"Latest complete PyPI version:\s*`(?P<tag>v\d+\.\d+\.\d+)`",
                re.IGNORECASE,
            ),
        ),
    ]
    for path, content in documents.items():
        marker = f"release_docs_current_tag: {expected_tag}"
        if "release_docs_current_tag:" in content and marker not in content:
            errors.append(f"{path} release_docs_current_tag must be {expected_tag}")
        for pattern in current_patterns:
            for match in pattern.finditer(content):
                found = match.group("tag")
                if found != expected_tag:
                    errors.append(
                        f"{path} contains stale current release prose `{found}` for "
                        f"{match.group('subject')}; expected `{expected_tag}`"
                    )
        for subject, pattern in latest_release_patterns:
            for match in pattern.finditer(content):
                found = match.group("tag")
                if found != expected_tag:
                    if subject.startswith(
                        "latest complete PyPI"
                    ) and allows_complete_public_release_lag(content, found):
                        continue
                    errors.append(
                        f"{path} contains stale {subject} `{found}`; expected `{expected_tag}`"
                    )
    return errors


def validate_benchmarks_docs(*, benchmarks_content: str) -> list[str]:
    errors: list[str] = []
    for expected in (
        "## Benchmark Matrix",
        "| Surface | Script | Default artifact |",
        "`benchmarks/run_benchmarks.py`",
        "`benchmarks/run_tool_comparison_benchmarks.py`",
        "`benchmarks/run_hot_query_benchmarks.py`",
        "`benchmarks/run_ast_rewrite_benchmarks.py`",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required matrix contract: {expected}")

    for expected in (
        "## Artifact Conventions",
        "`suite`",
        "`artifact`",
        "`environment`",
        "`generated_at_epoch_s`",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required artifact convention: {expected}")

    for expected in (
        "## Acceptance Rules",
        "Do not update benchmark docs or claims until the relevant artifact has been rerun on the accepted line.",
        "Compare against the current accepted baseline, not memory.",
        "Keep backend labels explicit in artifacts so routing claims are auditable.",
    ):
        if expected not in benchmarks_content:
            errors.append(f"Benchmark docs missing required acceptance rule: {expected}")

    return errors


def validate_homebrew_formula_contract(*, brew_content: str, py_version: str) -> list[str]:
    errors: list[str] = []
    has_direct_version = f'version "{py_version}"' in brew_content
    has_constant_version = f'TENSOR_GREP_VERSION = "{py_version}"' in brew_content
    if not has_direct_version and not has_constant_version:
        errors.append("Homebrew formula version does not match pyproject version")

    if "TENSOR_GREP_VERSION =" not in brew_content:
        errors.append("Homebrew formula must use explicit TENSOR_GREP_VERSION assignment")

    if "version TENSOR_GREP_VERSION" not in brew_content:
        errors.append("Homebrew formula must declare `version TENSOR_GREP_VERSION`")

    # audit MED: a Homebrew formula with a versioned URL must carry a 64-hex sha256 so `brew
    # install` verifies the binary. The source template carries none (the binary digests only
    # exist post-build; they are stamped into the published bundle formula from CHECKSUMS.txt).
    # Validate IF-PRESENT: any sha256 that exists must be a lowercase 64-hex digest.
    for match in re.finditer(r'(?m)^\s*sha256 "([^"]*)"', brew_content):
        digest = match.group(1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(
                f"Homebrew formula sha256 must be a lowercase 64-hex digest, found '{digest}'"
            )

    return errors


def validate_uv_security_constraints(*, pyproject_content: str) -> list[str]:
    errors: list[str] = []
    try:
        pyproject_data = tomllib.loads(pyproject_content)
    except tomllib.TOMLDecodeError as exc:
        return [f"pyproject.toml is not valid TOML: {exc}"]

    tool_config = pyproject_data.get("tool", {})
    if not isinstance(tool_config, dict):
        tool_config = {}
    uv_config = tool_config.get("uv", {})
    if not isinstance(uv_config, dict):
        uv_config = {}
    constraint_dependencies = uv_config.get("constraint-dependencies", [])
    if not isinstance(constraint_dependencies, list):
        return ["pyproject.toml [tool.uv].constraint-dependencies must be a list"]

    expected_constraints = {
        "aiohttp>=3.14.3",
        "cryptography>=50.0.0",
        "pyjwt>=2.13.0",
        "pygments>=2.20.0",
        "python-multipart>=0.0.31",
        "python-dotenv>=1.2.2",
        "requests>=2.33.0",
        "starlette>=1.3.1",
        "pydantic-settings>=2.14.2",
    }
    missing_constraints = sorted(
        expected_constraints - {str(entry) for entry in constraint_dependencies}
    )
    if missing_constraints:
        errors.append(
            "pyproject.toml [tool.uv].constraint-dependencies missing security floor entries: "
            + ", ".join(missing_constraints)
        )

    project_config = pyproject_data.get("project", {})
    if not isinstance(project_config, dict):
        project_config = {}
    project_dependencies = project_config.get("dependencies", [])
    required_direct_dependency = "cryptography>=50.0.0"
    if not isinstance(project_dependencies, list) or required_direct_dependency not in {
        str(entry) for entry in project_dependencies
    }:
        errors.append(
            "pyproject.toml [project].dependencies missing direct security floor: "
            + required_direct_dependency
        )

    # A `[tool.uv] constraint-dependencies` entry governs THIS repo's local resolution only. It is
    # NOT published metadata, so it does nothing for `pip install tensor-grep[...]`. Any advisory
    # floor whose package is reachable from a PUBLISHED extra must therefore ALSO be declared in
    # that extra, or the floor silently fails to reach users while every gate still reports green.
    # `nlp` pulls `tritonclient[http]`, whose own metadata permits `aiohttp>=3.8.1,<4`.
    required_extra_floors = {"nlp": "aiohttp>=3.14.3"}
    optional_dependencies = project_config.get("optional-dependencies", {})
    if not isinstance(optional_dependencies, dict):
        optional_dependencies = {}
    for extra_name, required_floor in sorted(required_extra_floors.items()):
        extra_entries = optional_dependencies.get(extra_name, [])
        if not isinstance(extra_entries, list) or required_floor not in {
            str(entry) for entry in extra_entries
        }:
            errors.append(
                f"pyproject.toml [project.optional-dependencies].{extra_name} missing published "
                f"security floor: {required_floor} (a [tool.uv] constraint is lock-only and does "
                "not reach a PyPI installer)"
            )
    return errors


def validate_dev_tooling_constraints(*, pyproject_content: str) -> list[str]:
    errors: list[str] = []
    try:
        pyproject_data = tomllib.loads(pyproject_content)
    except tomllib.TOMLDecodeError as exc:
        return [f"pyproject.toml is not valid TOML: {exc}"]

    optional_dependencies = pyproject_data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(optional_dependencies, dict):
        optional_dependencies = {}
    dev_dependencies = optional_dependencies.get("dev", [])
    if not isinstance(dev_dependencies, list):
        return ["pyproject.toml [project.optional-dependencies].dev must be a list"]
    bench_dependencies = optional_dependencies.get("bench", [])
    if not isinstance(bench_dependencies, list):
        return ["pyproject.toml [project.optional-dependencies].bench must be a list"]

    expected_ruff_pin = "ruff==0.15.20"
    if expected_ruff_pin not in {str(entry) for entry in dev_dependencies}:
        errors.append(
            "pyproject.toml [project.optional-dependencies].dev must pin "
            f"`{expected_ruff_pin}` for CI/local formatter parity"
        )
    # Governance for the #446 fix: an unpinned/floor mypy dependency (e.g. `mypy>=1.11`) can
    # silently resolve a newer mypy release into CI, reproducing the #446 red-main incident.
    expected_mypy_pin = "mypy==1.19.1"
    if expected_mypy_pin not in {str(entry) for entry in dev_dependencies}:
        errors.append(
            "pyproject.toml [project.optional-dependencies].dev must pin "
            f"`{expected_mypy_pin}` for CI/local type-check parity"
        )
    expected_pytest_floor = "pytest>=9.0.3"
    for group_name, dependencies in (("dev", dev_dependencies), ("bench", bench_dependencies)):
        if expected_pytest_floor not in {str(entry) for entry in dependencies}:
            errors.append(
                "pyproject.toml [project.optional-dependencies]."
                f"{group_name} must include `{expected_pytest_floor}` for audit parity"
            )
    return errors


def validate_semantic_release_config(*, pyproject_content: str) -> list[str]:
    errors: list[str] = []
    try:
        pyproject_data = tomllib.loads(pyproject_content)
    except tomllib.TOMLDecodeError as exc:
        return [f"pyproject.toml is not valid TOML: {exc}"]

    semantic_release = pyproject_data.get("tool", {}).get("semantic_release", {})
    build_command = str(semantic_release.get("build_command", ""))
    stamp_position = build_command.find("scripts/stamp_release_assets.py")
    if stamp_position < 0:
        errors.append(
            "semantic_release.build_command must run scripts/stamp_release_assets.py before build"
        )
    release_docs_add_command = "git add " + " ".join(RELEASE_DOC_PATHS)
    release_docs_add_position = build_command.find(release_docs_add_command)
    if release_docs_add_position < 0:
        errors.append(
            "semantic_release.build_command must stage release docs after stamping: "
            f"`{release_docs_add_command}`"
        )
    elif stamp_position >= 0 and release_docs_add_position < stamp_position:
        errors.append("semantic_release.build_command must stage release docs after stamping")

    lock_command = "uv lock --upgrade-package tensor-grep"
    lock_position = build_command.find(lock_command)
    git_add_position = build_command.find("git add uv.lock")
    cargo_lock_command = "cargo generate-lockfile --manifest-path rust_core/Cargo.toml"
    cargo_lock_position = build_command.find(cargo_lock_command)
    cargo_lock_add_position = build_command.find("git add rust_core/Cargo.lock")
    build_position = build_command.rfind("uv build")
    if lock_position < 0:
        errors.append(f"semantic_release.build_command must run `{lock_command}`")
    if git_add_position < 0:
        errors.append("semantic_release.build_command must stage `uv.lock`")
    if cargo_lock_position < 0:
        errors.append(f"semantic_release.build_command must run `{cargo_lock_command}`")
    if cargo_lock_add_position < 0:
        errors.append("semantic_release.build_command must stage `rust_core/Cargo.lock`")
    if build_position >= 0 and lock_position >= 0 and build_position < lock_position:
        errors.append("semantic_release.build_command must refresh `uv.lock` before `uv build`")
    if lock_position >= 0 and git_add_position >= 0 and git_add_position < lock_position:
        errors.append("semantic_release.build_command must stage `uv.lock` after refreshing it")
    if build_position >= 0 and git_add_position >= 0 and build_position < git_add_position:
        errors.append("semantic_release.build_command must stage `uv.lock` before `uv build`")
    if build_position >= 0 and cargo_lock_position >= 0 and build_position < cargo_lock_position:
        errors.append(
            "semantic_release.build_command must refresh `rust_core/Cargo.lock` before `uv build`"
        )
    if (
        cargo_lock_position >= 0
        and cargo_lock_add_position >= 0
        and cargo_lock_add_position < cargo_lock_position
    ):
        errors.append(
            "semantic_release.build_command must stage `rust_core/Cargo.lock` after refreshing it"
        )
    if (
        build_position >= 0
        and cargo_lock_add_position >= 0
        and build_position < cargo_lock_add_position
    ):
        errors.append(
            "semantic_release.build_command must stage `rust_core/Cargo.lock` before `uv build`"
        )
    if (
        build_position >= 0
        and release_docs_add_position >= 0
        and build_position < release_docs_add_position
    ):
        errors.append("semantic_release.build_command must stage release docs before `uv build`")

    version_toml = semantic_release.get("version_toml", [])
    version_variables = semantic_release.get("version_variables", [])
    required_toml_entries = {
        "pyproject.toml:project.version",
        "rust_core/Cargo.toml:package.version",
    }
    required_variable_entries = {
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
    }
    missing_toml = sorted(required_toml_entries - set(version_toml))
    missing_variables = sorted(required_variable_entries - set(version_variables))
    if missing_toml:
        errors.append("semantic_release.version_toml missing entries: " + ", ".join(missing_toml))
    if missing_variables:
        errors.append(
            "semantic_release.version_variables missing entries: " + ", ".join(missing_variables)
        )

    return errors
