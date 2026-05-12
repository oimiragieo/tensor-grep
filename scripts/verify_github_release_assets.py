from __future__ import annotations

import argparse
import json
import time
import urllib.request


def _normalize_digest(raw_digest: str) -> str | None:
    digest = raw_digest.strip().lower()
    if not digest:
        return None
    if ":" in digest:
        algorithm, value = digest.split(":", 1)
        if algorithm != "sha256":
            return None
        digest = value
    if len(digest) != 64:
        return None
    try:
        int(digest, 16)
    except ValueError:
        return None
    return digest


def _parse_checksums(checksums_content: str) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    duplicates: list[str] = []
    for raw_line in checksums_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0]
        filename = parts[-1]
        if filename in result:
            duplicates.append(filename)
        result[filename] = digest
    return result, duplicates


def _validate_manifest_against_assets(
    *,
    manifest_content: str,
    manifest_name: str,
    required_assets: set[str],
    named_assets: dict[str, dict],
    checksum_asset_paths: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    checksum_asset_paths = checksum_asset_paths or {}
    checksums, duplicate_entries = _parse_checksums(manifest_content)
    for entry in duplicate_entries:
        errors.append(f"Duplicate checksum entry in {manifest_name} for asset: {entry}")

    expected_checksum_names: set[str] = set()
    for name in required_assets:
        checksum_name = checksum_asset_paths.get(name, name)
        expected_checksum_names.add(checksum_name)
        if checksum_name not in checksums:
            errors.append(f"{manifest_name} missing digest entry for asset: {checksum_name}")
            continue
        digest = _normalize_digest(checksums[checksum_name])
        if digest is None:
            errors.append(f"Invalid SHA256 digest length for {checksum_name} in {manifest_name}")
            continue

        asset = named_assets.get(name)
        if not isinstance(asset, dict):
            continue
        size = asset.get("size")
        if not isinstance(size, int) or size <= 0:
            errors.append(f"Release asset {name} has invalid size metadata")

        raw_asset_digest = asset.get("digest")
        if not isinstance(raw_asset_digest, str):
            errors.append(f"Release asset {name} missing GitHub digest metadata")
            continue
        asset_digest = _normalize_digest(raw_asset_digest)
        if asset_digest is None:
            errors.append(f"Release asset {name} has invalid digest metadata: {raw_asset_digest}")
            continue
        if asset_digest != digest:
            errors.append(
                f"Checksum mismatch for {name}: {manifest_name} does not match GitHub asset digest"
            )

    for checksum_name in sorted(checksums):
        if checksum_name in expected_checksum_names:
            continue
        errors.append(
            f"Unexpected checksum entry in {manifest_name} for unmanaged asset: {checksum_name}"
        )
    return errors


def validate_release_assets_payload(
    *,
    release_data: dict,
    checksums_content: str,
    bundle_checksums_content: str | None = None,
    expected_assets: list[str],
    checksum_required_assets: list[str] | None = None,
    bundle_checksum_required_assets: list[str] | None = None,
    bundle_checksum_asset_paths: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    assets = release_data.get("assets", [])
    if not isinstance(assets, list):
        return ["GitHub release payload assets field must be a list"]

    named_assets: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str):
            continue
        if name in named_assets:
            errors.append(f"Duplicate release asset entry: {name}")
        named_assets[name] = asset
    names = set(named_assets.keys())
    missing = [name for name in expected_assets if name not in names]
    for name in missing:
        errors.append(f"Missing release asset: {name}")
    expected_set = set(expected_assets)
    checksum_required_set = (
        set(checksum_required_assets) if checksum_required_assets is not None else expected_set
    )
    bundle_checksum_required_set = (
        set(bundle_checksum_required_assets)
        if bundle_checksum_required_assets is not None
        else set()
    )
    unexpected_assets = sorted(names - expected_set)
    for name in unexpected_assets:
        errors.append(f"Unexpected release asset: {name}")

    expected_checksum_assets = {name for name in checksum_required_set if name != "CHECKSUMS.txt"}
    errors.extend(
        _validate_manifest_against_assets(
            manifest_content=checksums_content,
            manifest_name="CHECKSUMS.txt",
            required_assets=expected_checksum_assets,
            named_assets=named_assets,
        )
    )

    if bundle_checksum_required_set:
        if not isinstance(bundle_checksums_content, str):
            errors.append("Missing release asset content: BUNDLE_CHECKSUMS.txt")
        else:
            expected_bundle_assets = {
                name for name in bundle_checksum_required_set if name != "BUNDLE_CHECKSUMS.txt"
            }
            errors.extend(
                _validate_manifest_against_assets(
                    manifest_content=bundle_checksums_content,
                    manifest_name="BUNDLE_CHECKSUMS.txt",
                    required_assets=expected_bundle_assets,
                    named_assets=named_assets,
                    checksum_asset_paths=bundle_checksum_asset_paths,
                )
            )
    return errors


def _github_json(url: str, token: str | None = None) -> dict:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.load(resp)


def _download_text(url: str, token: str | None = None) -> str:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as resp:
        data = resp.read()
    return data.decode("utf-8")


FULL_BINARY_ASSETS = [
    "tg-linux-amd64-cpu",
    "tg-linux-amd64-nvidia",
    "tg-macos-amd64-cpu",
    "tg-windows-amd64-cpu.exe",
    "tg-windows-amd64-nvidia.exe",
]

NATIVE_FRONTDOOR_BINARY_ASSETS = [
    "tg-linux-amd64-cpu",
    "tg-macos-amd64-cpu",
    "tg-windows-amd64-cpu.exe",
]

GPU_READY_NATIVE_FRONTDOOR_BINARY_ASSETS = [
    "tg-linux-amd64-cpu",
    "tg-linux-amd64-nvidia",
    "tg-macos-amd64-cpu",
    "tg-windows-amd64-cpu.exe",
    "tg-windows-amd64-nvidia.exe",
]

PACKAGE_MANAGER_ASSETS = [
    "tensor-grep.rb",
    "oimiragieo.tensor-grep.yaml",
    "PUBLISH_INSTRUCTIONS.md",
    "BUNDLE_CHECKSUMS.txt",
]

BINARY_ASSET_PROFILES = {
    "full": FULL_BINARY_ASSETS,
    "native-frontdoor": NATIVE_FRONTDOOR_BINARY_ASSETS,
    "native-frontdoor-gpu": GPU_READY_NATIVE_FRONTDOOR_BINARY_ASSETS,
}


def verify_release_assets(
    *,
    repo: str,
    tag: str,
    token: str | None = None,
    expected_profile: str = "full",
) -> list[str]:
    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    release_data = _github_json(api_url, token=token)

    assets = release_data.get("assets", [])
    checksums_url = None
    bundle_checksums_url = None
    for asset in assets:
        if (
            isinstance(asset, dict)
            and asset.get("name") == "CHECKSUMS.txt"
            and isinstance(asset.get("browser_download_url"), str)
        ):
            checksums_url = asset["browser_download_url"]
        if (
            isinstance(asset, dict)
            and asset.get("name") == "BUNDLE_CHECKSUMS.txt"
            and isinstance(asset.get("browser_download_url"), str)
        ):
            bundle_checksums_url = asset["browser_download_url"]

    if not checksums_url:
        return ["Missing release asset: CHECKSUMS.txt"]
    if not bundle_checksums_url:
        return ["Missing release asset: BUNDLE_CHECKSUMS.txt"]

    checksums_content = _download_text(checksums_url, token=token)
    bundle_checksums_content = _download_text(bundle_checksums_url, token=token)
    binary_assets = BINARY_ASSET_PROFILES[expected_profile]
    version = tag.removeprefix("v")
    bundle_checksum_asset_paths = {
        "tensor-grep.rb": "homebrew-tap/Formula/tensor-grep.rb",
        "oimiragieo.tensor-grep.yaml": (
            f"winget-pkgs/manifests/o/oimiragieo/tensor-grep/{version}/oimiragieo.tensor-grep.yaml"
        ),
        "PUBLISH_INSTRUCTIONS.md": "PUBLISH_INSTRUCTIONS.md",
    }
    expected_assets = [*binary_assets, *PACKAGE_MANAGER_ASSETS, "CHECKSUMS.txt"]
    checksum_required_assets = [*binary_assets, "CHECKSUMS.txt"]
    bundle_checksum_required_assets = PACKAGE_MANAGER_ASSETS
    return validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        bundle_checksums_content=bundle_checksums_content,
        expected_assets=expected_assets,
        checksum_required_assets=checksum_required_assets,
        bundle_checksum_required_assets=bundle_checksum_required_assets,
        bundle_checksum_asset_paths=bundle_checksum_asset_paths,
    )


def verify_release_assets_with_retries(
    *,
    repo: str,
    tag: str,
    token: str | None = None,
    expected_profile: str = "full",
    wait_seconds: float = 0.0,
    poll_interval_seconds: float = 5.0,
) -> list[str]:
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    errors: list[str] = []
    while True:
        try:
            errors = verify_release_assets(
                repo=repo,
                tag=tag,
                token=token,
                expected_profile=expected_profile,
            )
        except Exception as exc:
            errors = [f"GitHub release asset request failed: {exc}"]
        if not errors or time.monotonic() >= deadline:
            return errors
        time.sleep(max(poll_interval_seconds, 0.1))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify GitHub release asset matrix and checksum coverage."
    )
    parser.add_argument("--repo", default="oimiragieo/tensor-grep")
    parser.add_argument("--tag", required=True, help="Tag without refs/tags/ prefix (e.g. v1.2.3)")
    parser.add_argument("--token", help="Optional GitHub token for API/download requests")
    parser.add_argument(
        "--expected-profile",
        choices=sorted(BINARY_ASSET_PROFILES),
        default="full",
        help="Expected GitHub release binary asset matrix profile.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=0.0,
        help="Retry verification for up to this many seconds.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=5.0,
        help="Delay between verification retries when --wait-seconds is set.",
    )
    args = parser.parse_args()

    errors = verify_release_assets_with_retries(
        repo=args.repo,
        tag=args.tag,
        token=args.token,
        expected_profile=args.expected_profile,
        wait_seconds=args.wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    print(f"GitHub release assets verified for {args.repo}@{args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
