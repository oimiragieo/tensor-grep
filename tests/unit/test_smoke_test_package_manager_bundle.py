from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "smoke_test_package_manager_bundle.py"
    spec = importlib.util.spec_from_file_location("smoke_test_package_manager_bundle", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_bundle(tmp_path: Path, version: str) -> Path:
    bundle = tmp_path / "bundle"
    brew = bundle / "homebrew-tap" / "Formula" / "tensor-grep.rb"
    winget = (
        bundle
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / version
        / "oimiragieo.tensor-grep.yaml"
    )
    summary = bundle / "PUBLISH_INSTRUCTIONS.md"
    checksums = bundle / "BUNDLE_CHECKSUMS.txt"

    brew.parent.mkdir(parents=True, exist_ok=True)
    winget.parent.mkdir(parents=True, exist_ok=True)
    brew.write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.2.3"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    winget.write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    summary.write_text(
        (
            "# Package Manager Publish Bundle v1.2.3\n\n"
            "homebrew-tap/Formula/tensor-grep.rb\n"
            "winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.2.3/oimiragieo.tensor-grep.yaml\n"
            "winget validate --manifest\n"
        ),
        encoding="utf-8",
    )
    checksums.write_text("placeholder\n", encoding="utf-8")
    return bundle


def test_should_pass_for_valid_bundle_contract(tmp_path: Path):
    module = _load_module()
    bundle = _write_bundle(tmp_path, "1.2.3")
    errors = module.smoke_test_package_manager_bundle(bundle_dir=bundle, expected_version="1.2.3")
    assert errors == []


def test_should_fail_when_expected_version_manifest_folder_missing(tmp_path: Path):
    module = _load_module()
    bundle = _write_bundle(tmp_path, "1.2.2")
    errors = module.smoke_test_package_manager_bundle(bundle_dir=bundle, expected_version="1.2.3")
    assert any("Missing winget manifest in bundle for 1.2.3" in err for err in errors)


def test_should_fail_when_summary_missing_required_paths(tmp_path: Path):
    module = _load_module()
    bundle = _write_bundle(tmp_path, "1.2.3")
    (bundle / "PUBLISH_INSTRUCTIONS.md").write_text(
        "# Package Manager Publish Bundle v1.2.3\n", encoding="utf-8"
    )
    errors = module.smoke_test_package_manager_bundle(bundle_dir=bundle, expected_version="1.2.3")
    assert any("Bundle summary must include Homebrew formula path" in err for err in errors)
    assert any(
        "Bundle summary must include winget manifest path for expected version" in err
        for err in errors
    )
    assert any("Bundle summary must include winget validation instruction" in err for err in errors)
