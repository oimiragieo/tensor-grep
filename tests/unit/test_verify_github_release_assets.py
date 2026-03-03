import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "verify_github_release_assets.py"
    spec = importlib.util.spec_from_file_location("verify_github_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_release_assets_payload_should_pass_for_complete_asset_matrix():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu"},
            {"name": "tg-linux-amd64-nvidia"},
            {"name": "tg-macos-amd64-cpu"},
            {"name": "tg-windows-amd64-cpu.exe"},
            {"name": "tg-windows-amd64-nvidia.exe"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = "\n".join([
        f"{'a' * 64}  tg-linux-amd64-cpu",
        f"{'b' * 64}  tg-linux-amd64-nvidia",
        f"{'c' * 64}  tg-macos-amd64-cpu",
        f"{'d' * 64}  tg-windows-amd64-cpu.exe",
        f"{'e' * 64}  tg-windows-amd64-nvidia.exe",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=[
            "tg-linux-amd64-cpu",
            "tg-linux-amd64-nvidia",
            "tg-macos-amd64-cpu",
            "tg-windows-amd64-cpu.exe",
            "tg-windows-amd64-nvidia.exe",
            "CHECKSUMS.txt",
        ],
    )
    assert errors == []


def test_validate_release_assets_payload_should_fail_on_missing_asset_or_checksum():
    module = _load_module()
    release_data = {"assets": [{"name": "tg-linux-amd64-cpu"}, {"name": "CHECKSUMS.txt"}]}
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=[
            "tg-linux-amd64-cpu",
            "tg-linux-amd64-nvidia",
            "CHECKSUMS.txt",
        ],
    )
    assert any("Missing release asset: tg-linux-amd64-nvidia" in err for err in errors)
    assert any("missing digest entry" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_unexpected_managed_asset():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu"},
            {"name": "tg-linux-amd64-nvidia"},
            {"name": "tg-macos-amd64-cpu"},
            {"name": "tg-windows-amd64-cpu.exe"},
            {"name": "tg-windows-amd64-nvidia.exe"},
            {"name": "tg-linux-arm64-cpu"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = "\n".join([
        f"{'a' * 64}  tg-linux-amd64-cpu",
        f"{'b' * 64}  tg-linux-amd64-nvidia",
        f"{'c' * 64}  tg-macos-amd64-cpu",
        f"{'d' * 64}  tg-windows-amd64-cpu.exe",
        f"{'e' * 64}  tg-windows-amd64-nvidia.exe",
        f"{'f' * 64}  tg-linux-arm64-cpu",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=[
            "tg-linux-amd64-cpu",
            "tg-linux-amd64-nvidia",
            "tg-macos-amd64-cpu",
            "tg-windows-amd64-cpu.exe",
            "tg-windows-amd64-nvidia.exe",
            "CHECKSUMS.txt",
        ],
    )
    assert any("Unexpected managed release asset: tg-linux-arm64-cpu" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_unexpected_checksum_entry():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu"},
            {"name": "tg-linux-amd64-nvidia"},
            {"name": "tg-macos-amd64-cpu"},
            {"name": "tg-windows-amd64-cpu.exe"},
            {"name": "tg-windows-amd64-nvidia.exe"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = "\n".join([
        f"{'a' * 64}  tg-linux-amd64-cpu",
        f"{'b' * 64}  tg-linux-amd64-nvidia",
        f"{'c' * 64}  tg-macos-amd64-cpu",
        f"{'d' * 64}  tg-windows-amd64-cpu.exe",
        f"{'e' * 64}  tg-windows-amd64-nvidia.exe",
        f"{'f' * 64}  tg-extra-experimental",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=[
            "tg-linux-amd64-cpu",
            "tg-linux-amd64-nvidia",
            "tg-macos-amd64-cpu",
            "tg-windows-amd64-cpu.exe",
            "tg-windows-amd64-nvidia.exe",
            "CHECKSUMS.txt",
        ],
    )
    assert any(
        "Unexpected checksum entry for unmanaged asset: tg-extra-experimental" in err
        for err in errors
    )
