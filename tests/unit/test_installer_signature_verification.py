"""Unit tests for installer pinned-tag URLs, ARM64 detection, Cosign verification, and channels."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_channel_verifier():
    script_path = ROOT / "scripts" / "verify_release_channels.py"
    spec = importlib.util.spec_from_file_location("verify_release_channels", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installers_use_pinned_tags_never_mutable_main() -> None:
    install_sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "tensor-grep.git@main" not in install_sh
    assert "tensor-grep.git@main" not in install_ps1
    assert "tensor-grep.git@v${TAG_VERSION}" in install_sh
    assert "tensor-grep.git@v$tagVersion" in install_ps1
    assert "/releases/download/v${INSTALLED_VERSION}/" in install_sh
    assert "/releases/download/v$installedVersion/" in install_ps1


def test_install_sh_and_homebrew_detect_arm64_targets() -> None:
    install_sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    brew_rb = (ROOT / "scripts" / "tensor-grep.rb").read_text(encoding="utf-8")

    assert "Linux:aarch64:cpu|Linux:arm64:cpu" in install_sh
    assert "tg-linux-arm64-cpu" in install_sh
    assert "Darwin:arm64:cpu|Darwin:aarch64:cpu" in install_sh
    assert "tg-macos-arm64-cpu" in install_sh

    for marker in (
        "on_arm",
        "on_intel",
        "Hardware::CPU.arm?",
        "tg-macos-arm64-cpu",
        "tg-linux-arm64-cpu",
    ):
        assert marker in brew_rb


def test_installers_support_cosign_verification_and_help_flags() -> None:
    install_sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    for marker in (
        "--help",
        "--version",
        "--verify-cosign",
        "--no-verify-cosign",
        "_verify_cosign_bundle",
        "cosign verify-blob",
        ".sigstore.json",
    ):
        assert marker in install_sh

    for marker in (
        "[switch]$VerifyCosign",
        "[switch]$Help",
        "Test-CosignSignature",
        "verify-blob",
        ".sigstore.json",
    ):
        assert marker in install_ps1


def test_npm_eliminates_postinstall_and_declares_all_five_optional_packages() -> None:
    pkg = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert "postinstall" not in (pkg.get("scripts") or {})
    opt = pkg.get("optionalDependencies") or {}
    expected = {
        "@tensor-grep/cli-win32-x64",
        "@tensor-grep/cli-darwin-arm64",
        "@tensor-grep/cli-darwin-x64",
        "@tensor-grep/cli-linux-x64",
        "@tensor-grep/cli-linux-arm64",
    }
    assert set(opt) == expected
    tg_js = (ROOT / "npm" / "bin" / "tg.js").read_text(encoding="utf-8")
    for pkg_name in expected:
        assert pkg_name in tg_js


def test_security_md_includes_enterprise_sections() -> None:
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for required in (
        "security@tensor-grep.dev",
        "Response SLA",
        "Supported Versions",
        "Zero-Telemetry Guarantee",
        "Air-Gapped Verification",
        "cosign verify-blob",
        "gh attestation verify",
        "sbom-rust.json",
    ):
        assert required in sec


def test_verify_release_channels_dry_run_passes_and_catches_drift(tmp_path: Path) -> None:
    verifier = _load_channel_verifier()
    assert verifier.verify_release_channels(ROOT) == []

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_release_channels.py"), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "all 5 targets and channels valid" in completed.stdout

    # Negative control: mutated npm/package.json with postinstall must fail
    fake_root = tmp_path / "repo"
    (fake_root / ".github" / "workflows").mkdir(parents=True)
    (fake_root / "scripts").mkdir(parents=True)
    (fake_root / "npm" / "packages").mkdir(parents=True)
    (fake_root / "rust_core").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", fake_root / "pyproject.toml")
    shutil.copy2(
        ROOT / ".github" / "workflows" / "ci.yml", fake_root / ".github" / "workflows" / "ci.yml"
    )
    shutil.copy2(ROOT / "scripts" / "tensor-grep.rb", fake_root / "scripts" / "tensor-grep.rb")
    shutil.copy2(
        ROOT / "scripts" / "oimiragieo.tensor-grep.yaml",
        fake_root / "scripts" / "oimiragieo.tensor-grep.yaml",
    )
    shutil.copy2(ROOT / "rust_core" / "Cargo.toml", fake_root / "rust_core" / "Cargo.toml")
    shutil.copy2(ROOT / "Dockerfile", fake_root / "Dockerfile")
    shutil.copytree(ROOT / "npm" / "packages", fake_root / "npm" / "packages", dirs_exist_ok=True)
    bad_npm = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    bad_npm["scripts"] = {"postinstall": "node install.js"}
    (fake_root / "npm" / "package.json").write_text(json.dumps(bad_npm), encoding="utf-8")

    errors = verifier.verify_release_channels(fake_root)
    assert any("postinstall" in err for err in errors)
