from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from prepare_release_asset_paths import prepare_release_asset_paths
from validate_release_binary_artifacts import EXPECTED_BINARY_PROFILES


def verify_release_subject_attestations(
    *,
    artifacts_dir: Path,
    expected_profile: str,
    repository: str,
    run: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"Invalid GitHub repository: {repository}")
    subjects, _ = prepare_release_asset_paths(
        artifacts_dir=artifacts_dir, expected_profile=expected_profile
    )
    for subject in subjects:
        run(
            [
                "gh",
                "attestation",
                "verify",
                str(subject),
                "--repo",
                repository,
                "--signer-workflow",
                f"{repository}/.github/workflows/ci.yml",
                "--source-ref",
                "refs/heads/main",
                "--deny-self-hosted-runners",
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify every managed release subject attestation."
    )
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-profile", choices=sorted(EXPECTED_BINARY_PROFILES), required=True
    )
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        verify_release_subject_attestations(
            artifacts_dir=args.artifacts_dir,
            expected_profile=args.expected_profile,
            repository=args.repository,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
