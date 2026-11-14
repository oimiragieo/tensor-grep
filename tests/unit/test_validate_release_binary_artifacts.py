from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_release_binary_artifacts.py"
    spec = importlib.util.spec_from_file_location("validate_release_binary_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"binary")


def test_should_validate_expected_release_binary_matrix(tmp_path: Path):
    module = _load_module()
    _touch(tmp_path / "binary-Linux-cpu" / "tg-linux-amd64-cpu")
    _touch(tmp_path / "binary-Linux-nvidia" / "tg-linux-amd64-nvidia")
    _touch(tmp_path / "binary-macOS-cpu" / "tg-macos-amd64-cpu")
    _touch(tmp_path / "binary-Windows-cpu" / "tg-windows-amd64-cpu.exe")
    _touch(tmp_path / "binary-Windows-nvidia" / "tg-windows-amd64-nvidia.exe")

    errors = module.validate_artifacts(tmp_path)
    assert errors == []


def test_should_validate_native_frontdoor_release_binary_profile(tmp_path: Path):
    module = _load_module()
    _touch(tmp_path / "release-native-ubuntu-latest" / "tg-linux-amd64-cpu")
    _touch(tmp_path / "release-native-macos-15-intel" / "tg-macos-amd64-cpu")
    _touch(tmp_path / "release-native-windows-latest" / "tg-windows-amd64-cpu.exe")

    errors = module.validate_artifacts(tmp_path, expected_profile="native-frontdoor")
    assert errors == []


def test_should_validate_gpu_ready_native_frontdoor_release_binary_profile(tmp_path: Path):
    module = _load_module()
    assert "native-frontdoor-gpu" in module.EXPECTED_BINARY_PROFILES
    _touch(tmp_path / "release-native-ubuntu-latest-cpu" / "tg-linux-amd64-cpu")
    _touch(tmp_path / "release-native-ubuntu-latest-nvidia" / "tg-linux-amd64-nvidia")
    _touch(tmp_path / "release-native-macos-15-intel-cpu" / "tg-macos-amd64-cpu")
    _touch(tmp_path / "release-native-windows-latest-cpu" / "tg-windows-amd64-cpu.exe")
    _touch(tmp_path / "release-native-windows-latest-nvidia" / "tg-windows-amd64-nvidia.exe")

    errors = module.validate_artifacts(tmp_path, expected_profile="native-frontdoor-gpu")
    assert errors == []


def test_should_fail_when_binary_is_missing(tmp_path: Path):
    module = _load_module()
    _touch(tmp_path / "binary-Linux-cpu" / "tg-linux-amd64-cpu")

    errors = module.validate_artifacts(tmp_path)
    assert any("Missing expected release binaries" in err for err in errors)


def test_should_emit_checksum_manifest(tmp_path: Path):
    module = _load_module()
    _touch(tmp_path / "binary-Linux-cpu" / "tg-linux-amd64-cpu")
    _touch(tmp_path / "binary-Linux-nvidia" / "tg-linux-amd64-nvidia")
    _touch(tmp_path / "binary-macOS-cpu" / "tg-macos-amd64-cpu")
    _touch(tmp_path / "binary-Windows-cpu" / "tg-windows-amd64-cpu.exe")
    _touch(tmp_path / "binary-Windows-nvidia" / "tg-windows-amd64-nvidia.exe")
    out = tmp_path / "CHECKSUMS.txt"

    module.write_checksums(tmp_path, out)
    content = out.read_text(encoding="utf-8")

    assert "tg-linux-amd64-cpu" in content
    assert "tg-windows-amd64-cpu.exe" in content


def test_should_include_only_both_managed_sboms_in_checksum_manifest(tmp_path: Path):
    module = _load_module()
    _touch(tmp_path / "tg-linux-amd64-cpu")
    rust_sbom = tmp_path / "sbom-rust.json"
    python_sbom = tmp_path / "sbom-python.json"
    rust_sbom.write_text("rust", encoding="utf-8")
    python_sbom.write_text("python", encoding="utf-8")
    out = tmp_path / "CHECKSUMS.txt"

    module.write_checksums(
        tmp_path,
        out,
        additional_assets=[rust_sbom, python_sbom],
    )

    names = {line.split("  ", 1)[1] for line in out.read_text(encoding="utf-8").splitlines()}
    assert names == {"tg-linux-amd64-cpu", "sbom-rust.json", "sbom-python.json"}


@pytest.mark.parametrize(
    "names",
    [
        ["sbom-rust.json"],
        ["sbom-rust.json", "other.json"],
        ["sbom-rust.json", "sbom-rust.json"],
    ],
)
def test_should_reject_unmanaged_or_incomplete_checksum_assets(
    tmp_path: Path, names: list[str]
) -> None:
    module = _load_module()
    assets = []
    for index, name in enumerate(names):
        asset = tmp_path / str(index) / name
        _touch(asset)
        assets.append(asset)

    with pytest.raises(ValueError, match="both managed SBOM files"):
        module.write_checksums(tmp_path, tmp_path / "CHECKSUMS.txt", additional_assets=assets)


def test_should_reject_missing_managed_checksum_asset(tmp_path: Path) -> None:
    module = _load_module()
    rust_sbom = tmp_path / "sbom-rust.json"
    rust_sbom.write_text("rust", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing additional release checksum asset"):
        module.write_checksums(
            tmp_path,
            tmp_path / "CHECKSUMS.txt",
            additional_assets=[rust_sbom, tmp_path / "sbom-python.json"],
        )


def test_main_wires_managed_sboms_into_checksum_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    for name in module.NATIVE_FRONTDOOR_EXPECTED_BINARIES:
        _touch(tmp_path / name)
    rust_sbom = tmp_path / "sbom-rust.json"
    python_sbom = tmp_path / "sbom-python.json"
    rust_sbom.write_text("rust", encoding="utf-8")
    python_sbom.write_text("python", encoding="utf-8")
    out = tmp_path / "CHECKSUMS.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_release_binary_artifacts.py",
            "--artifacts-dir",
            str(tmp_path),
            "--checksums-out",
            str(out),
            "--expected-profile",
            "native-frontdoor",
            "--additional-checksum-asset",
            str(rust_sbom),
            "--additional-checksum-asset",
            str(python_sbom),
        ],
    )

    assert module.main() == 0
    assert "sbom-rust.json" in out.read_text(encoding="utf-8")
