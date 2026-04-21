from __future__ import annotations

import argparse
import os
import stat
import subprocess
from pathlib import Path


def _find_linux_cpu_binary(artifacts_dir: Path) -> Path | None:
    matches = sorted(path for path in artifacts_dir.rglob("tg-linux-amd64-cpu") if path.is_file())
    return matches[0] if matches else None


def _ensure_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    if current_mode & stat.S_IXUSR:
        return
    path.chmod(current_mode | stat.S_IXUSR)


def smoke_verify_linux_binary(*, artifacts_dir: Path, expected_version: str) -> list[str]:
    errors: list[str] = []
    binary = _find_linux_cpu_binary(artifacts_dir)
    if binary is None:
        return [f"Missing Linux CPU release binary under {artifacts_dir}"]

    if os.name != "nt":
        _ensure_executable(binary)

    proc = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        errors.append(f"Binary smoke test failed with exit code {proc.returncode}: {stderr}")
        return errors

    stdout = (proc.stdout or "").strip()
    expected_prefixes = (f"tensor-grep {expected_version}", f"tg {expected_version}")
    if stdout not in expected_prefixes:
        errors.append(
            "Version output mismatch: expected one of "
            + ", ".join(f"'{prefix}'" for prefix in expected_prefixes)
            + f" in '{stdout}'"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-verify Linux release binary is executable and reports expected version."
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directory containing downloaded release artifacts",
    )
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    errors = smoke_verify_linux_binary(
        artifacts_dir=args.artifacts_dir,
        expected_version=args.expected_version,
    )
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    print("Linux release binary smoke verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
