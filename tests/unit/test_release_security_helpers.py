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
def test_generate_rust_sbom_binds_profile_features(
    tmp_path: Path, profile: str, features: list[str]
) -> None:
    manifest = tmp_path / "rust_core" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text("[package]", encoding="utf-8")
    output = tmp_path / "artifacts" / "sbom-rust.json"
    calls: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[object]:
        assert check is True
        calls.append(command)
        (manifest.parent / "sbom-rust.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    generate_rust_sbom(expected_profile=profile, output=output, manifest=manifest, run=run)
    if features:
        assert calls[0][-len(features) :] == features
    else:
        assert "--features" not in calls[0]
    assert output.read_text(encoding="utf-8") == "{}"


def test_generate_rust_sbom_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "sbom-rust.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        generate_rust_sbom(expected_profile="native-frontdoor", output=output)


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
    assert {Path(command[3]).name for command in calls} == {*NATIVE, *MANAGED_ASSETS}
    assert all("--deny-self-hosted-runners" in command for command in calls)


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
