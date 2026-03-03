from __future__ import annotations

import importlib.util
from pathlib import Path


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
