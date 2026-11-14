import hashlib
import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "verify_github_release_assets.py"
    spec = importlib.util.spec_from_file_location("verify_github_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _asset_metadata(name: str, content: bytes) -> dict[str, object]:
    return {
        "name": name,
        "size": len(content),
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def _set_manifest_metadata(release_data, name: str, content: str) -> None:
    asset = next(asset for asset in release_data["assets"] if asset["name"] == name)
    asset.update(_asset_metadata(name, content.encode("utf-8")))


def test_validate_local_release_assets_binds_api_metadata(tmp_path: Path):
    module = _load_module()
    content = b"signed bytes"
    (tmp_path / "tg-linux-amd64-cpu").write_bytes(content)
    release_data = {"assets": [_asset_metadata("tg-linux-amd64-cpu", content)]}

    assert (
        module._validate_local_release_assets(
            release_data=release_data,
            expected_assets=["tg-linux-amd64-cpu"],
            artifacts_dir=tmp_path,
        )
        == []
    )


def test_validate_local_release_assets_rejects_wrong_uploaded_metadata(tmp_path: Path):
    module = _load_module()
    (tmp_path / "tg-linux-amd64-cpu").write_bytes(b"local bytes")
    release_data = {"assets": [_asset_metadata("tg-linux-amd64-cpu", b"different")]}

    errors = module._validate_local_release_assets(
        release_data=release_data,
        expected_assets=["tg-linux-amd64-cpu"],
        artifacts_dir=tmp_path,
    )
    assert any("size differs" in error for error in errors)
    assert any("digest differs" in error for error in errors)


@pytest.mark.parametrize("bad_size", [True, 1.0, None])
def test_validate_local_release_assets_rejects_noninteger_size(tmp_path: Path, bad_size: object):
    module = _load_module()
    content = b"x"
    (tmp_path / "asset.bin").write_bytes(content)
    metadata = _asset_metadata("asset.bin", content)
    metadata["size"] = bad_size

    errors = module._validate_local_release_assets(
        release_data={"assets": [metadata]},
        expected_assets=["asset.bin"],
        artifacts_dir=tmp_path,
    )
    assert any("invalid size metadata" in error for error in errors)


def test_validate_local_release_assets_rejects_missing_duplicate_and_symlink(
    tmp_path: Path,
):
    module = _load_module()
    content = b"bytes"
    release_data = {"assets": [_asset_metadata("asset.bin", content)]}

    missing = module._validate_local_release_assets(
        release_data=release_data,
        expected_assets=["asset.bin"],
        artifacts_dir=tmp_path,
    )
    assert any("found 0" in error for error in missing)

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "asset.bin").write_bytes(content)
    (tmp_path / "two" / "asset.bin").write_bytes(content)
    duplicate = module._validate_local_release_assets(
        release_data=release_data,
        expected_assets=["asset.bin"],
        artifacts_dir=tmp_path,
    )
    assert any("found 2" in error for error in duplicate)

    (tmp_path / "one" / "asset.bin").unlink()
    (tmp_path / "two" / "asset.bin").unlink()
    outside = tmp_path.parent / "outside-asset.bin"
    outside.write_bytes(content)
    try:
        (tmp_path / "asset.bin").symlink_to(outside)
    except OSError:
        return
    symlink = module._validate_local_release_assets(
        release_data=release_data,
        expected_assets=["asset.bin"],
        artifacts_dir=tmp_path,
    )
    assert any("found 0" in error for error in symlink)


def test_main_forwards_local_artifacts_dir(tmp_path: Path, monkeypatch):
    module = _load_module()
    received = {}

    def fake_verify(**kwargs):
        received.update(kwargs)
        return []

    monkeypatch.setattr(module, "verify_release_assets_with_retries", fake_verify)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_github_release_assets.py",
            "--tag",
            "v1.2.3",
            "--local-artifacts-dir",
            str(tmp_path),
        ],
    )

    assert module.main() == 0
    assert received["local_artifacts_dir"] == tmp_path


def test_release_requests_do_not_forward_token_to_other_origins():
    module = _load_module()
    received = []

    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(("destination", self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(("origin", self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/asset")
            self.end_headers()

        def log_message(self, *_args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (origin, destination)
    ]
    for thread in threads:
        thread.start()
    try:
        url = f"http://127.0.0.1:{origin.server_port}/release"
        assert module._github_json(url, token="test-secret") == {}
        assert received == [("origin", "Bearer test-secret"), ("destination", None)]
        received.clear()
        assert module._download_text(url, token="test-secret") == "{}"
        assert received == [("origin", None), ("destination", None)]
    finally:
        for server in (origin, destination):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()


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
    _set_manifest_metadata(release_data, "CHECKSUMS.txt", checksums_content)
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


def test_validate_release_assets_payload_should_pass_for_native_frontdoor_asset_matrix():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-macos-amd64-cpu", "size": 100, "digest": f"sha256:{'b' * 64}"},
            {"name": "tg-windows-amd64-cpu.exe", "size": 100, "digest": f"sha256:{'c' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = "\n".join([
        f"{'a' * 64}  tg-linux-amd64-cpu",
        f"{'b' * 64}  tg-macos-amd64-cpu",
        f"{'c' * 64}  tg-windows-amd64-cpu.exe",
    ])
    _set_manifest_metadata(release_data, "CHECKSUMS.txt", checksums_content)
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=[
            "tg-linux-amd64-cpu",
            "tg-macos-amd64-cpu",
            "tg-windows-amd64-cpu.exe",
            "CHECKSUMS.txt",
        ],
    )
    assert errors == []


def test_should_expose_gpu_ready_native_frontdoor_release_asset_profile():
    module = _load_module()
    assert module.BINARY_ASSET_PROFILES["native-frontdoor-gpu"] == [
        "tg-linux-amd64-cpu",
        "tg-linux-amd64-nvidia",
        "tg-macos-amd64-cpu",
        "tg-windows-amd64-cpu.exe",
        "tg-windows-amd64-nvidia.exe",
    ]


def test_verify_release_assets_with_retries_should_retry_transient_request_errors(monkeypatch):
    module = _load_module()
    calls = 0
    sleeps: list[float] = []

    def fake_verify_release_assets(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient release asset propagation")
        return []

    monkeypatch.setattr(module, "verify_release_assets", fake_verify_release_assets)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    errors = module.verify_release_assets_with_retries(
        repo="oimiragieo/tensor-grep",
        tag="v1.2.3",
        token=None,
        expected_profile="native-frontdoor",
        wait_seconds=10,
        poll_interval_seconds=0.01,
    )

    assert errors == []
    assert calls == 2
    assert sleeps == [0.1]


def test_validate_release_assets_payload_should_fail_on_missing_asset_or_checksum():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    _set_manifest_metadata(release_data, "CHECKSUMS.txt", checksums_content)
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


def test_validate_release_assets_payload_should_fail_on_duplicate_asset_name_entries():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "tg-linux-amd64-cpu", "size": 101, "digest": f"sha256:{'b' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = f"{'a' * 64}  tg-linux-amd64-cpu\n"
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=["tg-linux-amd64-cpu", "CHECKSUMS.txt"],
    )
    assert any("Duplicate release asset entry: tg-linux-amd64-cpu" in err for err in errors)


def test_validate_release_assets_payload_should_fail_on_duplicate_checksum_manifest_entries():
    module = _load_module()
    release_data = {
        "assets": [
            {"name": "tg-linux-amd64-cpu", "size": 100, "digest": f"sha256:{'a' * 64}"},
            {"name": "CHECKSUMS.txt"},
        ]
    }
    checksums_content = "\n".join([
        f"{'a' * 64}  tg-linux-amd64-cpu",
        f"{'a' * 64}  tg-linux-amd64-cpu",
    ])
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=["tg-linux-amd64-cpu", "CHECKSUMS.txt"],
    )
    assert any(
        "Duplicate checksum entry in CHECKSUMS.txt for asset: tg-linux-amd64-cpu" in err
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
    _set_manifest_metadata(release_data, "CHECKSUMS.txt", checksums_content)
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


def test_validate_release_assets_payload_should_map_nested_bundle_paths_to_flat_assets():
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
        f"{'a' * 64}  homebrew-tap/Formula/tensor-grep.rb",
        (
            f"{'b' * 64}  "
            "winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.8.26/"
            "oimiragieo.tensor-grep.yaml"
        ),
        f"{'c' * 64}  PUBLISH_INSTRUCTIONS.md",
    ])
    checksums_content = "\n"
    _set_manifest_metadata(release_data, "CHECKSUMS.txt", checksums_content)
    _set_manifest_metadata(release_data, "BUNDLE_CHECKSUMS.txt", bundle_checksums_content)
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
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
        bundle_checksum_asset_paths={
            "tensor-grep.rb": "homebrew-tap/Formula/tensor-grep.rb",
            "oimiragieo.tensor-grep.yaml": (
                "winget-pkgs/manifests/o/oimiragieo/tensor-grep/1.8.26/oimiragieo.tensor-grep.yaml"
            ),
            "PUBLISH_INSTRUCTIONS.md": "PUBLISH_INSTRUCTIONS.md",
        },
    )
    assert errors == []


def test_validate_release_assets_payload_rejects_invalid_metadata_only_asset():
    module = _load_module()
    release_data = {"assets": [{"name": "asset.sigstore.json", "size": 0, "digest": "sha512:bad"}]}
    errors = module.validate_release_assets_payload(
        release_data=release_data,
        checksums_content="",
        expected_assets=["asset.sigstore.json"],
        checksum_required_assets=[],
    )
    assert any("invalid size metadata" in error for error in errors)
    assert any("invalid digest metadata" in error for error in errors)


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
