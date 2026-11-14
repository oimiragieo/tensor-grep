from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "prepare_release_asset_paths", ROOT / "scripts" / "prepare_release_asset_paths.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

NATIVE = ("tg-linux-amd64-cpu", "tg-macos-amd64-cpu", "tg-windows-amd64-cpu.exe")


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    for name in NATIVE:
        path = root / "native" / name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    for relative in (
        "sbom-rust.json",
        "sbom-python.json",
        "CHECKSUMS.txt",
        *MODULE.PACKAGE_ASSETS,
        "package-manager-bundle/winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.2.3/oimiragieo.tensor-grep.yaml",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    return root


def test_prepare_release_asset_paths_returns_exact_managed_sets(artifacts: Path) -> None:
    subjects, uploads = MODULE.prepare_release_asset_paths(
        artifacts_dir=artifacts, expected_profile="native-frontdoor"
    )
    assert {path.name for path in subjects} == {*NATIVE, *MODULE.MANAGED_ASSETS}
    assert set(subjects).issubset(uploads)
    assert len(subjects) == 6
    assert len(uploads) == 10


@pytest.mark.parametrize("missing", ["sbom-rust.json", "CHECKSUMS.txt"])
def test_prepare_release_asset_paths_rejects_missing_managed_asset(
    artifacts: Path, missing: str
) -> None:
    (artifacts / missing).unlink()
    with pytest.raises(ValueError, match="missing or outside"):
        MODULE.prepare_release_asset_paths(
            artifacts_dir=artifacts, expected_profile="native-frontdoor"
        )


def test_prepare_release_asset_paths_rejects_duplicate_native_name(artifacts: Path) -> None:
    duplicate = artifacts / "native" / "duplicate" / NATIVE[0]
    duplicate.parent.mkdir()
    duplicate.write_bytes(b"duplicate")
    with pytest.raises(
        ValueError, match=r"Unexpected release binaries|Expected one release binary"
    ):
        MODULE.prepare_release_asset_paths(
            artifacts_dir=artifacts, expected_profile="native-frontdoor"
        )


def test_prepare_release_asset_paths_rejects_extra_native_binary(artifacts: Path) -> None:
    extra = artifacts / "native" / "tg-unknown"
    extra.write_bytes(b"extra")
    with pytest.raises(ValueError, match="Unexpected release binaries"):
        MODULE.prepare_release_asset_paths(
            artifacts_dir=artifacts, expected_profile="native-frontdoor"
        )


def test_prepare_release_asset_paths_rejects_duplicate_winget_manifest(artifacts: Path) -> None:
    duplicate = artifacts / "package-manager-bundle" / "winget-pkgs" / "manifests" / "other"
    duplicate.mkdir(parents=True)
    (duplicate / "oimiragieo.tensor-grep.yaml").write_text("duplicate", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected one Winget"):
        MODULE.prepare_release_asset_paths(
            artifacts_dir=artifacts, expected_profile="native-frontdoor"
        )


def test_prepare_release_asset_paths_rejects_symlinked_managed_asset(
    artifacts: Path, tmp_path: Path
) -> None:
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    link = artifacts / "sbom-rust.json"
    link.unlink()
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match=r"missing or outside|non-symlink"):
        MODULE.prepare_release_asset_paths(
            artifacts_dir=artifacts, expected_profile="native-frontdoor"
        )


def test_write_github_output_uses_bounded_multiline_values(artifacts: Path, tmp_path: Path) -> None:
    subjects, uploads = MODULE.prepare_release_asset_paths(
        artifacts_dir=artifacts, expected_profile="native-frontdoor"
    )
    output = tmp_path / "github-output"
    MODULE.write_github_output(output, subjects=subjects, uploads=uploads)
    text = output.read_text(encoding="utf-8")
    assert text.count("subject_paths<<SUBJECT_PATHS") == 1
    assert text.count("upload_files<<UPLOAD_FILES") == 1
    assert "tg-unknown" not in text
