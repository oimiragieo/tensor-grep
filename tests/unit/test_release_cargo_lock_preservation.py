from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("rl")


def _run(argv: list[str], root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _checked(argv: list[str], root: Path, env: dict[str, str]) -> None:
    result = _run(argv, root, env)
    assert result.returncode == 0, (result.stdout, result.stderr)


def _configured_step() -> str:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    segments = metadata["tool"]["semantic_release"]["build_command"].split(" && ")
    candidates = [(index, shlex.split(value)) for index, value in enumerate(segments)]
    matches = [(index, argv) for index, argv in candidates if argv[0] == "cargo"]
    assert len(matches) == 1
    index, argv = matches[0]
    assert argv[-2:] == ["--manifest-path", "rust_core/Cargo.toml"]
    guard = segments[index - 1] if index else ""
    if shlex.split(guard) == ["test", "-f", "rust_core/Cargo.lock"]:
        return guard + " && " + shlex.join(argv)
    return shlex.join(argv)


def _bash() -> str:
    if os.name == "nt":
        executable = Path(os.environ["ProgramFiles"]) / "Git/bin/bash.exe"
        assert executable.is_file()
        return str(executable)
    executable = shutil.which("bash")
    assert executable is not None
    return executable


def _commit(root: Path, env: dict[str, str], message: str) -> None:
    _checked(["git", "add", "Cargo.toml", "src/lib.rs"], root, env)
    _checked(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "commit",
            "-m",
            message,
        ],
        root,
        env,
    )


def _fixture(root: Path) -> tuple[Path, dict[str, str]]:
    upstream = root / "upstream"
    (upstream / "src").mkdir(parents=True)
    (upstream / "Cargo.toml").write_text(
        '[package]\nname="fixture-dep"\nversion="0.1.0"\nedition="2021"\n',
        encoding="utf-8",
    )
    (upstream / "src/lib.rs").write_text("pub fn value() -> u8 { 1 }\n", encoding="utf-8")
    env = dict(os.environ, CARGO_HOME=str(root / "c"), CARGO_NET_OFFLINE="false")
    _checked(["git", "init", "-b", "main"], upstream, env)
    _commit(upstream, env, "Initial dependency")
    (root / "rust_core/src").mkdir(parents=True)
    (root / "rust_core/Cargo.toml").write_text(
        '[package]\nname="tensor_grep_rs"\nversion="1.0.0"\nedition="2021"\n'
        '[dependencies]\nfixture-dep={git="' + upstream.as_uri() + '"}\n',
        encoding="utf-8",
    )
    (root / "rust_core/src/lib.rs").write_text("pub fn value() -> u8 { 1 }\n", encoding="utf-8")
    _checked(["cargo", "generate-lockfile", "--manifest-path", "rust_core/Cargo.toml"], root, env)
    return upstream, env


def _stamp_and_advance(root: Path, upstream: Path, env: dict[str, str]) -> None:
    manifest = root / "rust_core/Cargo.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('version="1.0.0"', 'version="1.0.1"'),
        encoding="utf-8",
    )
    (upstream / "src/lib.rs").write_text("pub fn value() -> u8 { 2 }\n", encoding="utf-8")
    _commit(upstream, env, "Advance dependency")


def _dependencies(lock: dict) -> list[dict]:
    return [package for package in lock["package"] if package["name"] != "tensor_grep_rs"]


def _own_version(lock: dict) -> str:
    return next(
        package["version"] for package in lock["package"] if package["name"] == "tensor_grep_rs"
    )


def _validate_step(step: str) -> list[str]:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    current = _configured_step()
    assert content.count(current) == 1
    spec = importlib.util.spec_from_file_location(
        "release_lock_validator", ROOT / "scripts/validate_release_assets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_semantic_release_config(pyproject_content=content.replace(current, step))


def test_Validator_WorkspaceLockUpdate_AcceptsPreservingConfiguration() -> None:
    step = "test -f rust_core/Cargo.lock && cargo update --workspace --manifest-path rust_core/Cargo.toml"

    errors = _validate_step(step)

    assert errors == []


def test_Validator_LockRegeneration_RejectsDependencyDrift() -> None:
    step = "cargo generate-lockfile --manifest-path rust_core/Cargo.toml"

    errors = _validate_step(step)

    assert (
        "semantic_release.build_command must run `test -f rust_core/Cargo.lock && "
        "cargo update --workspace --manifest-path rust_core/Cargo.toml`"
    ) in errors


def test_ReleaseLock_VersionStamp_PreservesExistingDependencyRecords(release_root: Path) -> None:
    upstream, env = _fixture(release_root)
    original = tomllib.loads((release_root / "rust_core/Cargo.lock").read_text(encoding="utf-8"))
    _stamp_and_advance(release_root, upstream, env)

    result = _run([_bash(), "--noprofile", "--norc", "-c", _configured_step()], release_root, env)
    updated = tomllib.loads((release_root / "rust_core/Cargo.lock").read_text(encoding="utf-8"))

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert _own_version(updated) == "1.0.1"
    assert _dependencies(updated) == _dependencies(original)


def test_ReleaseLock_MissingLock_RefusesRegeneration(release_root: Path) -> None:
    _, env = _fixture(release_root)
    lock = release_root / "rust_core/Cargo.lock"
    lock.unlink()

    result = _run([_bash(), "--noprofile", "--norc", "-c", _configured_step()], release_root, env)

    assert result.returncode == 1
    assert not lock.exists()
