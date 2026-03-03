from __future__ import annotations

import argparse
import json
import urllib.request


def _parse_checksums(checksums_content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in checksums_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0]
        filename = parts[-1]
        result[filename] = digest
    return result


def validate_release_assets_payload(
    *, release_data: dict, checksums_content: str, expected_assets: list[str]
) -> list[str]:
    errors: list[str] = []
    assets = release_data.get("assets", [])
    if not isinstance(assets, list):
        return ["GitHub release payload assets field must be a list"]

    names = {
        str(asset.get("name"))
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    missing = [name for name in expected_assets if name not in names]
    for name in missing:
        errors.append(f"Missing release asset: {name}")
    expected_set = set(expected_assets)
    managed_names = {
        name for name in names if name == "CHECKSUMS.txt" or str(name).startswith("tg-")
    }
    unexpected_managed = sorted(managed_names - expected_set)
    for name in unexpected_managed:
        errors.append(f"Unexpected managed release asset: {name}")

    checksums = _parse_checksums(checksums_content)
    expected_checksum_assets = {name for name in expected_assets if name != "CHECKSUMS.txt"}
    for name in expected_assets:
        if name == "CHECKSUMS.txt":
            continue
        if name not in checksums:
            errors.append(f"CHECKSUMS.txt missing digest entry for asset: {name}")
            continue
        digest = checksums[name]
        if len(digest) != 64:
            errors.append(f"Invalid SHA256 digest length for {name} in CHECKSUMS.txt")
            continue
        try:
            int(digest, 16)
        except ValueError:
            errors.append(f"Non-hex SHA256 digest for {name} in CHECKSUMS.txt")

    for checksum_name in sorted(checksums):
        if checksum_name in expected_checksum_assets:
            continue
        if checksum_name.startswith("tg-"):
            errors.append(f"Unexpected checksum entry for unmanaged asset: {checksum_name}")
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


def verify_release_assets(*, repo: str, tag: str, token: str | None = None) -> list[str]:
    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    release_data = _github_json(api_url, token=token)

    assets = release_data.get("assets", [])
    checksums_url = None
    for asset in assets:
        if (
            isinstance(asset, dict)
            and asset.get("name") == "CHECKSUMS.txt"
            and isinstance(asset.get("browser_download_url"), str)
        ):
            checksums_url = asset["browser_download_url"]
            break

    if not checksums_url:
        return ["Missing release asset: CHECKSUMS.txt"]

    checksums_content = _download_text(checksums_url, token=token)
    expected_assets = [
        "tg-linux-amd64-cpu",
        "tg-linux-amd64-nvidia",
        "tg-macos-amd64-cpu",
        "tg-windows-amd64-cpu.exe",
        "tg-windows-amd64-nvidia.exe",
        "CHECKSUMS.txt",
    ]
    return validate_release_assets_payload(
        release_data=release_data,
        checksums_content=checksums_content,
        expected_assets=expected_assets,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify GitHub release asset matrix and checksum coverage."
    )
    parser.add_argument("--repo", default="oimiragieo/tensor-grep")
    parser.add_argument("--tag", required=True, help="Tag without refs/tags/ prefix (e.g. v1.2.3)")
    parser.add_argument("--token", help="Optional GitHub token for API/download requests")
    args = parser.parse_args()

    errors = verify_release_assets(repo=args.repo, tag=args.tag, token=args.token)
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    print(f"GitHub release assets verified for {args.repo}@{args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
