import tomllib
from pathlib import Path


def _closure(packages: list[dict], package_name: str) -> set[tuple]:
    package = next(item for item in packages if item["name"] == package_name)
    pending = [
        entry
        for entry in package.get("dependencies", [])
        if entry.split()[0] in {"sha2", "tempfile", "windows-sys"}
    ]
    seen = set()
    while pending:
        parts = pending.pop().split()
        choices = [
            item
            for item in packages
            if item["name"] == parts[0] and (len(parts) == 1 or item["version"] == parts[1])
        ]
        assert len(choices) == 1, parts
        item = choices[0]
        identity = (item["name"], item["version"], item.get("source"), item.get("checksum"))
        if identity not in seen:
            seen.add(identity)
            pending.extend(item.get("dependencies", []))
    return seen


def test_standalone_preflight_uses_shared_source_and_pinned_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    crate = root / "rust_core/recovery-preflight"
    manifest = tomllib.loads((crate / "Cargo.toml").read_text(encoding="utf-8"))
    assert manifest["dependencies"] == {"sha2": "=0.10.9"}
    assert manifest["dev-dependencies"] == {"tempfile": "=3.27.0"}
    assert manifest["target"]["cfg(windows)"]["dependencies"]["windows-sys"] == {
        "version": "=0.61.2",
        "features": [
            "Win32_Foundation",
            "Win32_Storage_FileSystem",
            "Win32_System_SystemInformation",
        ],
    }
    binary = next(item for item in manifest["bin"] if item["name"] == "tg-recovery-preflight")
    assert (crate / binary["path"]).resolve() == root / "rust_core/src/bin/tg_recovery_preflight.rs"
    integration = next(
        item for item in manifest["test"] if item["name"] == "test_recovery_preflight"
    )
    assert (
        crate / integration["path"]
    ).resolve() == root / "rust_core/tests/test_recovery_preflight.rs"
    root_lock = tomllib.loads((root / "rust_core/Cargo.lock").read_text(encoding="utf-8"))
    thin_lock = tomllib.loads((crate / "Cargo.lock").read_text(encoding="utf-8"))
    root_closure = _closure(root_lock["package"], "tensor_grep_rs")
    thin_closure = _closure(thin_lock["package"], "tensor-grep-recovery-preflight")
    assert thin_closure and thin_closure <= root_closure
    assert len(thin_closure) == len(thin_lock["package"]) - 1
    for _, _, source, checksum in thin_closure:
        assert source == "registry+https://github.com/rust-lang/crates.io-index"
        assert isinstance(checksum, str) and len(checksum) == 64
        assert all(character in "0123456789abcdef" for character in checksum)


def test_closure_does_not_accept_a_stale_unreachable_version() -> None:
    packages = [
        {"name": "root", "version": "1", "dependencies": ["sha2 2"]},
        {"name": "sha2", "version": "1", "source": "registry", "checksum": "old"},
        {"name": "sha2", "version": "2", "source": "registry", "checksum": "new"},
    ]
    assert ("sha2", "1", "registry", "old") not in _closure(packages, "root")


def test_source_distribution_includes_standalone_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    included = {
        entry["path"]
        for entry in project["tool"]["maturin"]["include"]
        if isinstance(entry, dict) and entry.get("format") == "sdist"
    }
    assert {
        "rust_core/recovery-preflight/Cargo.toml",
        "rust_core/recovery-preflight/Cargo.lock",
        "rust_core/tests/test_recovery_preflight.rs",
        "rust_core/rust-toolchain.toml",
    } <= included
