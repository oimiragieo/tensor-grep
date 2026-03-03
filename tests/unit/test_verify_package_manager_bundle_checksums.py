from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "verify_package_manager_bundle_checksums.py"
    spec = importlib.util.spec_from_file_location(
        "verify_package_manager_bundle_checksums", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_verify_bundle_checksums_for_matching_files(tmp_path: Path):
    module = _load_module()
    file_path = tmp_path / "homebrew-tap" / "Formula" / "tensor-grep.rb"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("class TensorGrep < Formula\nend\n", encoding="utf-8")
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    (tmp_path / "BUNDLE_CHECKSUMS.txt").write_text(
        f"{digest}  homebrew-tap/Formula/tensor-grep.rb\n", encoding="utf-8"
    )

    errors = module.verify_bundle_checksums(bundle_dir=tmp_path)
    assert errors == []


def test_should_fail_when_bundle_file_missing_from_checksums(tmp_path: Path):
    module = _load_module()
    file_path = tmp_path / "homebrew-tap" / "Formula" / "tensor-grep.rb"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("class TensorGrep < Formula\nend\n", encoding="utf-8")
    (tmp_path / "BUNDLE_CHECKSUMS.txt").write_text("", encoding="utf-8")

    errors = module.verify_bundle_checksums(bundle_dir=tmp_path)
    assert any("empty or invalid" in err for err in errors)


def test_should_fail_when_bundle_checksum_mismatch(tmp_path: Path):
    module = _load_module()
    file_path = tmp_path / "winget-pkgs" / "manifest.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("PackageVersion: 1.2.3\n", encoding="utf-8")
    (tmp_path / "BUNDLE_CHECKSUMS.txt").write_text(
        f"{'0' * 64}  winget-pkgs/manifest.yaml\n", encoding="utf-8"
    )

    errors = module.verify_bundle_checksums(bundle_dir=tmp_path)
    assert any("Checksum mismatch for bundle file" in err for err in errors)
