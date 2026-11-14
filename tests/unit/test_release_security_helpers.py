from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_release_rust_sbom import generate_rust_sbom  # noqa: E402
from prepare_release_asset_paths import MANAGED_ASSETS, PACKAGE_ASSETS  # noqa: E402
from verify_release_subject_attestations import (  # noqa: E402
    verify_release_subject_attestations,
)

NATIVE = ("tg-linux-amd64-cpu", "tg-macos-amd64-cpu", "tg-windows-amd64-cpu.exe")
requires_dir_fd = pytest.mark.skipif(
    not {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd),
    reason="release helper requires POSIX directory descriptors",
)


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    for name in NATIVE:
        path = root / "native" / name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    for relative in (
        *MANAGED_ASSETS,
        *PACKAGE_ASSETS,
        "package-manager-bundle/winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.2.3/oimiragieo.tensor-grep.yaml",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("profile", "features"),
    [("native-frontdoor", []), ("native-frontdoor-gpu", ["--features", "cuda"])],
)
@requires_dir_fd
def test_generate_rust_sbom_binds_profile_features(
    tmp_path: Path, profile: str, features: list[str]
) -> None:
    manifest = tmp_path / "rust_core" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("[package]", encoding="utf-8")
    output = tmp_path / "artifacts" / "sbom-rust.json"
    output.parent.mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[object]:
        assert check is True
        calls.append(command)
        (manifest.parent / "sbom-rust.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    generate_rust_sbom(expected_profile=profile, root=tmp_path, run=run)
    assert calls == [
        [
            "cargo",
            "cyclonedx",
            "--manifest-path",
            str(manifest),
            "--format",
            "json",
            "--target",
            "all",
            "--override-filename",
            "sbom-rust",
            "--no-default-features",
            *features,
        ]
    ]
    assert output.read_text(encoding="utf-8") == "{}"


@requires_dir_fd
def test_generate_rust_sbom_refuses_existing_output(tmp_path: Path) -> None:
    (tmp_path / "rust_core").mkdir()
    output = tmp_path / "artifacts" / "sbom-rust.json"
    output.parent.mkdir()
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path)


@requires_dir_fd
def test_generate_rust_sbom_does_not_clobber_raced_output(tmp_path: Path) -> None:
    manifest = tmp_path / "rust_core" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("[package]", encoding="utf-8")
    output = tmp_path / "artifacts" / "sbom-rust.json"
    output.parent.mkdir()

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[object]:
        assert check is True
        (manifest.parent / "sbom-rust.json").write_text("generated", encoding="utf-8")
        output.write_text("keep", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(FileExistsError):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path, run=run)
    assert output.read_text(encoding="utf-8") == "keep"


@requires_dir_fd
def test_generate_rust_sbom_rejects_symlinked_output_directory(tmp_path: Path) -> None:
    (tmp_path / "rust_core").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, tmp_path / "artifacts", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="confined"):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path)


@requires_dir_fd
def test_generate_rust_sbom_rejects_symlinked_manifest_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Cargo.toml").write_text("[package]", encoding="utf-8")
    try:
        os.symlink(outside, tmp_path / "rust_core", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="confined"):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path)


@requires_dir_fd
def test_generate_rust_sbom_rejects_symlinked_output_leaf(tmp_path: Path) -> None:
    (tmp_path / "rust_core").mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    try:
        os.symlink(outside, artifacts / "sbom-rust.json")
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="overwrite"):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path)
    assert outside.read_text(encoding="utf-8") == "keep"


@requires_dir_fd
def test_generate_rust_sbom_rejects_subprocess_parent_swap(tmp_path: Path) -> None:
    rust_dir = tmp_path / "rust_core"
    rust_dir.mkdir()
    (rust_dir / "Cargo.toml").write_text("[package]", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[object]:
        assert check is True
        (rust_dir / "sbom-rust.json").write_text("generated", encoding="utf-8")
        artifacts.rename(tmp_path / "original-artifacts")
        os.symlink(outside, artifacts, target_is_directory=True)
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(ValueError, match="parent identity changed"):
        generate_rust_sbom(expected_profile="native-frontdoor", root=tmp_path, run=run)
    assert not (outside / "sbom-rust.json").exists()


def test_verify_release_subject_attestations_covers_exact_subject_set(artifacts: Path) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[object]:
        assert check is True
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    verify_release_subject_attestations(
        artifacts_dir=artifacts,
        expected_profile="native-frontdoor",
        repository="oimiragieo/tensor-grep",
        run=run,
    )
    subjects = [*(artifacts / "native" / name / name for name in sorted(NATIVE))]
    subjects.extend(artifacts / name for name in MANAGED_ASSETS)
    assert calls == [
        [
            "gh",
            "attestation",
            "verify",
            str(subject),
            "--repo",
            "oimiragieo/tensor-grep",
            "--signer-workflow",
            "oimiragieo/tensor-grep/.github/workflows/ci.yml",
            "--source-ref",
            "refs/heads/main",
            "--deny-self-hosted-runners",
        ]
        for subject in subjects
    ]


def test_verify_release_subject_attestations_rejects_symlink(
    artifacts: Path, tmp_path: Path
) -> None:
    link = artifacts / "sbom-rust.json"
    link.unlink()
    try:
        os.symlink(tmp_path / "outside", link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match=r"missing or outside|non-symlink"):
        verify_release_subject_attestations(
            artifacts_dir=artifacts,
            expected_profile="native-frontdoor",
            repository="oimiragieo/tensor-grep",
        )
