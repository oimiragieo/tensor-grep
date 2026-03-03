import importlib.util
from pathlib import Path


def _load_module(root: Path):
    script_path = root / "scripts" / "stamp_release_assets.py"
    spec = importlib.util.spec_from_file_location("stamp_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stamp_release_assets_updates_brew_and_winget(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        'class TensorGrep < Formula\n  version "0.9.0"\nend\n', encoding="utf-8"
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v0.9.0\n"
        "PackageVersion: 0.9.0\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v0.9.0/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.stamp_assets(check_only=False)

    assert rc == 0
    assert 'version "1.2.3"' in (root / "scripts" / "tensor-grep.rb").read_text(encoding="utf-8")
    winget = (root / "scripts" / "oimiragieo.tensor-grep.yaml").read_text(encoding="utf-8")
    assert "# Winget Manifest for tensor-grep v1.2.3" in winget
    assert "PackageVersion: 1.2.3" in winget
    assert (
        "    InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe"
        in winget
    )


def test_stamp_release_assets_check_mode_fails_when_drifted(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        'class TensorGrep < Formula\n  version "0.9.0"\nend\n', encoding="utf-8"
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v0.9.0\n"
        "PackageVersion: 0.9.0\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v0.9.0/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.stamp_assets(check_only=True)
    assert rc == 1
