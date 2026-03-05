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
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-linux-amd64-nvidia", "size": 100, "digest": f"sha256:{'b' * 64}"},
            {"name": "tg-macos-amd64-cpu", "size": 100, "digest": f"sha256:{'c' * 64}"},
            {"name": "tg-windows-amd64-cpu.exe", "size": 100, "digest": f"sha256:{'d' * 64}"},
            {
                "name": "tg-windows-amd64-nvidia.exe",
                "size": 100,
                "digest": f"sha256:{'e' * 64}",
            },
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
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
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
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-linux-amd64-nvidia", "size": 100, "digest": f"sha256:{'b' * 64}"},
            {"name": "tg-macos-amd64-cpu", "size": 100, "digest": f"sha256:{'c' * 64}"},
            {"name": "tg-windows-amd64-cpu.exe", "size": 100, "digest": f"sha256:{'d' * 64}"},
            {
                "name": "tg-windows-amd64-nvidia.exe",
                "size": 100,
                "digest": f"sha256:{'e' * 64}",
            },
            {"name": "tg-linux-arm64-cpu", "size": 100, "digest": f"sha256:{'f' * 64}"},
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
    assert any("Unexpected release asset: tg-linux-arm64-cpu" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_unexpected_non_managed_asset():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-linux-amd64-nvidia", "size": 100, "digest": f"sha256:{'b' * 64}"},
            {"name": "tg-macos-amd64-cpu", "size": 100, "digest": f"sha256:{'c' * 64}"},
            {"name": "tg-windows-amd64-cpu.exe", "size": 100, "digest": f"sha256:{'d' * 64}"},
            {
                "name": "tg-windows-amd64-nvidia.exe",
                "size": 100,
                "digest": f"sha256:{'e' * 64}",
            },
            {"name": "tensor-grep.rb", "size": 10, "digest": f"sha256:{'f' * 64}"},
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
    assert any("Unexpected release asset: tensor-grep.rb" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_unexpected_checksum_entry():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-linux-amd64-nvidia", "size": 100, "digest": f"sha256:{'b' * 64}"},
            {"name": "tg-macos-amd64-cpu", "size": 100, "digest": f"sha256:{'c' * 64}"},
            {"name": "tg-windows-amd64-cpu.exe", "size": 100, "digest": f"sha256:{'d' * 64}"},
            {
                "name": "tg-windows-amd64-nvidia.exe",
                "size": 100,
                "digest": f"sha256:{'e' * 64}",
            },
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
        "Unexpected checksum entry in CHECKSUMS.txt for unmanaged asset: tg-extra-experimental"
        in err
        for err in errors
    )


def test_validate_release_assets_payload_should_fail_on_digest_mismatch():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'0' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=["tg-linux-amd64-cpu", "CHECKSUMS.txt"],
    )
    assert any("Checksum mismatch for tg-linux-amd64-cpu" in err for err in errors)


def test_validate_release_assets_payload_should_fail_when_digest_metadata_missing():
    module = _load_module()
    release_data = {
        "assets": [{"name": "tg-linux-amd64-cpu", "size": 100}, {"name": "CHECKSUMS.txt"}]
    }
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=["tg-linux-amd64-cpu", "CHECKSUMS.txt"],
    )
    assert any("missing GitHub digest metadata" in err for err in errors)


def test_validate_release_assets_payload_should_allow_expected_non_checksum_assets():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tensor-grep.rb", "size": 10, "digest": f"sha256:{'b' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=["tg-linux-amd64-cpu", "tensor-grep.rb", "CHECKSUMS.txt"],
        checksum_required_assets=["tg-linux-amd64-cpu", "CHECKSUMS.txt"],
    )
    assert errors == []


def test_validate_release_assets_payload_should_fail_when_bundle_checksums_missing():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tensor-grep.rb", "size": 10, "digest": f"sha256:{'a' * 64}"},
            {"name": "CHECKSUMS.txt"},
            {"name": "BUNDLE_CHECKSUMS.txt"},
        ]
    }
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content="",
        bundle_checksums_content=None,
        expected_assets=["tensor-grep.rb", "CHECKSUMS.txt", "BUNDLE_CHECKSUMS.txt"],
        checksum_required_assets=["CHECKSUMS.txt"],
        bundle_checksum_required_assets=["tensor-grep.rb", "BUNDLE_CHECKSUMS.txt"],
    )
    assert any("Missing release asset content: BUNDLE_CHECKSUMS.txt" in err for err in errors)


def test_validate_release_assets_payload_should_validate_bundle_checksums_against_asset_digests():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tensor-grep.rb", "size": 10, "digest": f"sha256:{'a' * 64}"},
            {"name": "oimiragieo.tensor-grep.yaml", "size": 10, "digest": f"sha256:{'b' * 64}"},
            {"name": "PUBLISH_INSTRUCTIONS.md", "size": 10, "digest": f"sha256:{'c' * 64}"},
            {"name": "CHECKSUMS.txt"},
            {"name": "BUNDLE_CHECKSUMS.txt"},
        ]
    }
    bundle_checksums_content = "\n".join([
        f"{'a' * 64}  tensor-grep.rb",
        f"{'b' * 64}  oimiragieo.tensor-grep.yaml",
        f"{'0' * 64}  PUBLISH_INSTRUCTIONS.md",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content="",
        bundle_checksums_content=bundle_checksums_content,
        expected_assets=[
            "tensor-grep.rb",
            "oimiragieo.tensor-grep.yaml",
            "PUBLISH_INSTRUCTIONS.md",
            "CHECKSUMS.txt",
            "BUNDLE_CHECKSUMS.txt",
        ],
        checksum_required_assets=["CHECKSUMS.txt"],
        bundle_checksum_required_assets=[
            "tensor-grep.rb",
            "oimiragieo.tensor-grep.yaml",
            "PUBLISH_INSTRUCTIONS.md",
            "BUNDLE_CHECKSUMS.txt",
        ],
    )
    assert any("Checksum mismatch for PUBLISH_INSTRUCTIONS.md" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_unmanaged_bundle_checksum_entry():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tensor-grep.rb", "size": 10, "digest": f"sha256:{'a' * 64}"},
            {"name": "oimiragieo.tensor-grep.yaml", "size": 10, "digest": f"sha256:{'b' * 64}"},
            {"name": "PUBLISH_INSTRUCTIONS.md", "size": 10, "digest": f"sha256:{'c' * 64}"},
            {"name": "CHECKSUMS.txt"},
            {"name": "BUNDLE_CHECKSUMS.txt"},
        ]
    }
    bundle_checksums_content = "\n".join([
        f"{'a' * 64}  tensor-grep.rb",
        f"{'b' * 64}  oimiragieo.tensor-grep.yaml",
        f"{'c' * 64}  PUBLISH_INSTRUCTIONS.md",
        f"{'d' * 64}  unexpected-extra.txt",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content="",
        bundle_checksums_content=bundle_checksums_content,
        expected_assets=[
            "tensor-grep.rb",
            "oimiragieo.tensor-grep.yaml",
            "PUBLISH_INSTRUCTIONS.md",
            "CHECKSUMS.txt",
            "BUNDLE_CHECKSUMS.txt",
        ],
        checksum_required_assets=["CHECKSUMS.txt"],
        bundle_checksum_required_assets=[
            "tensor-grep.rb",
            "oimiragieo.tensor-grep.yaml",
            "PUBLISH_INSTRUCTIONS.md",
            "BUNDLE_CHECKSUMS.txt",
        ],
    )
    assert any(
        "Unexpected checksum entry in BUNDLE_CHECKSUMS.txt for unmanaged asset: unexpected-extra.txt"
        in err
        for err in errors
    )
