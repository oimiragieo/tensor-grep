import importlib.util
from pathlib import Path


def _load_module(root: Path):
    script_path = root / "scripts" / "prepare_package_manager_release.py"
    spec = importlib.util.spec_from_file_location("prepare_package_manager_release", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_bundle_generates_homebrew_winget_installers_and_summary(tmp_path):
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
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    (root / "scripts" / "install.ps1").write_text("Write-Host stable\n", encoding="utf-8")
    (root / "scripts" / "install.sh").write_text("echo stable\n", encoding="utf-8")

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
    assert (out / "install.ps1").exists()
    assert (out / "install.sh").exists()
    assert (out / "PUBLISH_INSTRUCTIONS.md").exists()
    checksums = out / "BUNDLE_CHECKSUMS.txt"
    assert checksums.exists()
    checksum_text = checksums.read_text(encoding="utf-8")
    assert "install.ps1" in checksum_text
    assert "install.sh" in checksum_text
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
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "PackageIdentifier: oimiragieo.tensor-grep\n"
        "PackageVersion: 1.2.3\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        "    InstallerType: portable\n"
        "    InstallerUrl: "
        "https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.prepare_bundle(output_dir=root / "artifacts" / "bundle", check_only=True)
    assert rc == 1
