from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "rust_core" / "deny.toml"
TIMEOUT_SECONDS = 120


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _require_success(label: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit {result.returncode}:\n{result.stdout}{result.stderr}"
        )


def _fixture_manifest(directory: Path, name: str, version: str) -> Path:
    package = directory / f"{name}-{version}"
    (package / "src").mkdir(parents=True)
    (package / "src" / "lib.rs").write_text("pub fn fixture() {}\n", encoding="utf-8")
    manifest = package / "Cargo.toml"
    manifest.write_text(
        f'[package]\nname = "{name}"\nversion = "{version}"\nedition = "2021"\nlicense = "Zlib"\n',
        encoding="utf-8",
    )
    return manifest


def _check_fixture(
    cargo: str,
    manifest: Path,
    *,
    expected_success: bool,
    expected_name: str,
    expected_version: str,
) -> None:
    generated = _run(
        [cargo, "generate-lockfile", "--offline", "--manifest-path", str(manifest)],
        cwd=manifest.parent,
        timeout=30,
    )
    _require_success(f"offline fixture lock for {expected_name} {expected_version}", generated)
    checked = _run(
        [
            cargo,
            "deny",
            "--color",
            "never",
            "--config",
            str(CONFIG),
            "--manifest-path",
            str(manifest),
            "--frozen",
            "check",
            "licenses",
        ],
        cwd=manifest.parent,
        timeout=30,
    )
    output = (checked.stdout + checked.stderr).lower()
    if expected_success:
        if checked.returncode != 0 or "licenses ok" not in output:
            raise RuntimeError(
                f"expected allowed {expected_name} {expected_version}, got {checked.returncode}:\n"
                f"{checked.stdout}{checked.stderr}"
            )
    elif (
        checked.returncode != 4
        or f"{expected_name.lower()} v{expected_version}" not in output
        or "zlib" not in output
        or "license is not explicitly allowed" not in output
    ):
        raise RuntimeError(
            f"expected license refusal for {expected_name} {expected_version}, "
            f"got {checked.returncode}:\n{checked.stdout}{checked.stderr}"
        )
    print(f"PASS: {expected_name} {expected_version} license scope", flush=True)


def main() -> int:
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("cargo is required for the Foldhash license scope check")
    deny = _run([cargo, "deny", "--version"], cwd=ROOT, timeout=30)
    _require_success("cargo deny version", deny)
    print(f"Using {deny.stdout.strip()}", flush=True)

    repository = _run(
        [
            cargo,
            "deny",
            "--color",
            "never",
            "--config",
            str(CONFIG),
            "--manifest-path",
            str(ROOT / "rust_core" / "Cargo.toml"),
            "--frozen",
            "check",
            "licenses",
        ],
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
    )
    _require_success("repository graph license check", repository)
    if "licenses ok" not in (repository.stdout + repository.stderr).lower():
        raise RuntimeError(
            f"repository graph license check omitted success diagnostic:\n"
            f"{repository.stdout}{repository.stderr}"
        )
    print("PASS: repository dependency graph licenses", flush=True)

    temp_root: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="foldhash-license-scope-") as temporary:
            temp_root = Path(temporary).resolve()
            if temp_root == ROOT or ROOT in temp_root.parents:
                raise RuntimeError("fixture temporary directory must be outside the checkout")
            fixtures = (
                ("foldhash", "0.2.0", True),
                ("foldhash", "0.2.1", False),
                ("unrelated-license-fixture", "0.2.0", False),
            )
            for name, version, expected_success in fixtures:
                manifest = _fixture_manifest(temp_root, name, version)
                _check_fixture(
                    cargo,
                    manifest,
                    expected_success=expected_success,
                    expected_name=name,
                    expected_version=version,
                )
    finally:
        if temp_root is not None and temp_root.exists():
            raise RuntimeError(f"owned fixture directory was not removed: {temp_root}")

    print("PASS: exact Foldhash Zlib exception scope", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
