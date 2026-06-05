import importlib.util
from pathlib import Path


def _write_winget_sources(root: Path, version: str) -> None:
    winget_dir = (
        root
        / "scripts"
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / version
    )
    winget_dir.mkdir(parents=True, exist_ok=True)
    installer_url = (
        f"https://github.com/oimiragieo/tensor-grep/releases/download/v{version}/"
        "tg-windows-amd64-cpu.exe"
    )
    (winget_dir / "oimiragieo.tensor-grep.yaml").write_text(
        f"PackageIdentifier: oimiragieo.tensor-grep\nPackageVersion: {version}\n",
        encoding="utf-8",
    )
    (winget_dir / "oimiragieo.tensor-grep.installer.yaml").write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        f"PackageVersion: {version}\n"
        "InstallerType: portable\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        f"    InstallerUrl: {installer_url}\n"
        "    InstallerSha256: 2d9cd666a2140162cf640b385dcfc97a96defe93d1665c929229d185793c1589\n",
        encoding="utf-8",
    )
    (winget_dir / "oimiragieo.tensor-grep.locale.en-US.yaml").write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        f"PackageVersion: {version}\n"
        "PackageLocale: en-US\n"
        "Publisher: oimiragieo\n"
        "PackageName: tensor-grep\n"
        "License: MIT\n"
        "ShortDescription: tensor-grep\n",
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        f"PackageVersion: {version}\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        f"    InstallerUrl: {installer_url}\n"
        "    InstallerSha256: 2d9cd666a2140162cf640b385dcfc97a96defe93d1665c929229d185793c1589\n",
        encoding="utf-8",
    )


def _load_module(root: Path):
    script_path = root / "scripts" / "prepare_package_manager_release.py"
    spec = importlib.util.spec_from_file_location("prepare_package_manager_release", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_bundle_generates_homebrew_winget_and_summary(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.2.3"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    _write_winget_sources(root, "1.2.3")

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root

    out = root / "artifacts" / "bundle"
    rc = module.prepare_bundle(output_dir=out, check_only=False)
    assert rc == 0

    assert (out / "homebrew-tap" / "Formula" / "tensor-grep.rb").exists()
    assert (
        out
        / "winget-pkgs"
        / "manifests"
        / "o"
        / "oimiragieo"
        / "tensor-grep"
        / "1.2.3"
        / "oimiragieo.tensor-grep.yaml"
    ).exists()
    assert (out / "PUBLISH_INSTRUCTIONS.md").exists()
    checksums = out / "BUNDLE_CHECKSUMS.txt"
    assert checksums.exists()
    checksum_text = checksums.read_text(encoding="utf-8")
    assert "homebrew-tap/Formula/tensor-grep.rb" in checksum_text
    assert (
        "winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.2.3/oimiragieo.tensor-grep.yaml"
        in checksum_text
    )


def test_prepare_bundle_check_mode_fails_on_manifest_drift(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "0.9.0"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    _write_winget_sources(root, "1.2.3")

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.prepare_bundle(output_dir=root / "artifacts" / "bundle", check_only=True)
    assert rc == 1
