from __future__ import annotations

import argparse
import importlib.util
import tomllib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[0]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_release_assets_module():
    path = SCRIPT_DIR / "validate_release_assets.py"
    spec = importlib.util.spec_from_file_location("validate_release_assets", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/validate_release_assets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_from_pyproject() -> str:
    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    return str(data["project"]["version"])


def smoke_test_package_manager_bundle(*, bundle_dir: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    validators = _load_release_assets_module()

    brew_path = bundle_dir / "homebrew-tap" / "Formula" / "tensor-grep.rb"
    winget_path = (
        bundle_dir
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / expected_version
        / "oimiragieo.tensor-grep.yaml"
    )
    summary_path = bundle_dir / "PUBLISH_INSTRUCTIONS.md"
    checksums_path = bundle_dir / "BUNDLE_CHECKSUMS.txt"
    install_ps1_path = bundle_dir / "install.ps1"
    install_sh_path = bundle_dir / "install.sh"

    if not brew_path.exists():
        errors.append(f"Missing Homebrew formula in bundle: {brew_path}")
    if not winget_path.exists():
        errors.append(f"Missing winget manifest in bundle for {expected_version}: {winget_path}")
    if not install_ps1_path.exists():
        errors.append(f"Missing Windows installer script in bundle: {install_ps1_path}")
    if not install_sh_path.exists():
        errors.append(f"Missing Unix installer script in bundle: {install_sh_path}")
    if not summary_path.exists():
        errors.append(f"Missing bundle publish summary: {summary_path}")
    if not checksums_path.exists():
        errors.append(f"Missing bundle checksums manifest: {checksums_path}")

    if errors:
        return errors

    brew_content = _read(brew_path)
    winget_content = _read(winget_path)
    summary_content = _read(summary_path)

    errors.extend(
        validators.validate_homebrew_formula_contract(
            brew_content=brew_content, py_version=expected_version
        )
    )
    errors.extend(
        validators.validate_winget_manifest(
            winget_content=winget_content, py_version=expected_version
        )
    )

    expected_formula_rel = "homebrew-tap/Formula/tensor-grep.rb"
    expected_winget_rel = (
        f"winget-pkgs/manifests/o/oimiragieo/tensor-grep/{expected_version}/"
        "oimiragieo.tensor-grep.yaml"
    )
    if expected_formula_rel not in summary_content:
        errors.append("Bundle summary must include Homebrew formula path")
    if expected_winget_rel not in summary_content:
        errors.append("Bundle summary must include winget manifest path for expected version")
    if "winget validate" not in summary_content:
        errors.append("Bundle summary must include winget validation instruction")
    if "ruby -c Formula/tensor-grep.rb" not in summary_content:
        errors.append("Bundle summary must include Homebrew syntax check instruction")
    if f"git checkout -b release/tensor-grep-v{expected_version}" not in summary_content:
        errors.append("Bundle summary must include Homebrew release branch command")
    if "git add Formula/tensor-grep.rb" not in summary_content:
        errors.append("Bundle summary must include Homebrew git add command")
    expected_winget_validate = (
        f"winget validate --manifest .\\manifests\\o\\oimiragieo\\tensor-grep\\{expected_version}\\"
    )
    if expected_winget_validate not in summary_content:
        errors.append("Bundle summary must include exact winget manifest validation path")
    expected_winget_git_add = f"git add manifests/o/oimiragieo/tensor-grep/{expected_version}"
    if expected_winget_git_add not in summary_content:
        errors.append("Bundle summary must include winget git add command")
    if "brew install oimiragieo/tap/tensor-grep" not in summary_content:
        errors.append("Bundle summary must include Homebrew smoke install instruction")
    if "winget install oimiragieo.tensor-grep" not in summary_content:
        errors.append("Bundle summary must include winget smoke install instruction")
    if "tg --version" not in summary_content:
        errors.append("Bundle summary must include tg --version smoke verification")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test generated package-manager bundle content contracts."
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=Path("artifacts") / "package-manager-bundle",
        help="Path to package-manager publish bundle",
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="Expected package version; defaults to pyproject.toml project.version",
    )
    args = parser.parse_args()

    expected_version = args.expected_version or _version_from_pyproject()
    errors = smoke_test_package_manager_bundle(
        bundle_dir=args.bundle_dir, expected_version=expected_version
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print(f"Package-manager bundle smoke test passed for v{expected_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
