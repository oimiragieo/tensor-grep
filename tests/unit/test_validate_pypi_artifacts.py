from __future__ import annotations

import importlib.util
import tarfile
import zipfile
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "validate_pypi_artifacts.py"
    spec = importlib.util.spec_from_file_location("validate_pypi_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _requires_dist_block(requires_dist: list[str] | None) -> str:
    return "".join(f"Requires-Dist: {entry}\n" for entry in requires_dist or [])


def _write_wheel(
    path: Path,
    version: str,
    tag: str,
    *,
    include_console_script: bool = True,
    requires_dist: list[str] | None = None,
) -> Path:
    wheel_name = f"tensor_grep-{version}-cp311-abi3-{tag}.whl"
    wheel_path = path / wheel_name
    dist_info = f"tensor_grep-{version}.dist-info/METADATA"
    entry_points = f"tensor_grep-{version}.dist-info/entry_points.txt"
    metadata = (
        f"Metadata-Version: 2.1\nName: tensor-grep\nVersion: {version}\n"
        + _requires_dist_block(requires_dist)
    )
    with zipfile.ZipFile(wheel_path, "w") as zf:
        zf.writestr(dist_info, metadata)
        if include_console_script:
            zf.writestr(
                entry_points,
                "[console_scripts]\ntg = tensor_grep.cli.main:app\n",
            )
    return wheel_path


def _write_sdist(path: Path, version: str, *, requires_dist: list[str] | None = None) -> Path:
    sdist_name = f"tensor_grep-{version}.tar.gz"
    sdist_path = path / sdist_name
    pkg_info_path = f"tensor_grep-{version}/PKG-INFO"
    pkg_info = (
        f"Metadata-Version: 2.1\nName: tensor-grep\nVersion: {version}\n"
        + _requires_dist_block(requires_dist)
    )
    with tarfile.open(sdist_path, "w:gz") as tf:
        info = tarfile.TarInfo(pkg_info_path)
        data = pkg_info.encode("utf-8")
        info.size = len(data)
        import io

        tf.addfile(info, io.BytesIO(data))
    return sdist_path


def test_should_validate_matching_artifacts(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_wheel(tmp_path, version, "win_amd64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert errors == []


def test_should_fail_when_wheel_version_mismatches(tmp_path: Path):
    module = _load_module()
    _write_wheel(tmp_path, "0.11.0", "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, "0.11.1", "macosx_11_0_x86_64")
    _write_wheel(tmp_path, "0.11.1", "win_amd64")
    _write_sdist(tmp_path, "0.11.1")

    errors = module.validate(
        dist_dir=tmp_path,
        version="0.11.1",
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("Wheel filename version mismatch" in err for err in errors)


def test_should_fail_when_required_platform_wheel_missing(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("Missing required wheel platform artifact: windows" == err for err in errors)


def test_should_build_hash_matrix_for_all_artifacts(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    wheel = _write_wheel(tmp_path, version, "manylinux_2_34_x86_64")
    sdist = _write_sdist(tmp_path, version)

    matrix = module.build_hash_matrix(tmp_path)

    assert set(matrix.keys()) == {wheel.name, sdist.name}
    for digest in matrix.values():
        assert len(digest) == 64


def test_should_fail_when_wheel_missing_tg_console_script(tmp_path: Path):
    module = _load_module()
    version = "0.11.1"
    _write_wheel(tmp_path, version, "manylinux_2_34_x86_64", include_console_script=False)
    _write_wheel(tmp_path, version, "macosx_11_0_x86_64")
    _write_wheel(tmp_path, version, "win_amd64")
    _write_sdist(tmp_path, version)

    errors = module.validate(
        dist_dir=tmp_path,
        version=version,
        require_platforms=["linux", "macos", "windows"],
    )

    assert any("missing tg console script entry point" in err for err in errors)


_PLATFORMS = ["linux", "macos", "windows"]
_PYPROJECT = """
[project]
name = "tensor-grep"
version = "0.11.1"
dependencies = [
  "cryptography>=50.0.0",
  "pyjwt>=2.15.0",
  "importlib-metadata<8.5.0; python_version < '3.12'",
  "tritonclient[http]>=2.40",
]

[project.optional-dependencies]
nlp = ["aiohttp>=3.14.3", "urllib3>=2.8.0"]
"""
# Raw formatting of a real published wheel (v1.123.4): a space before the semicolon and single
# quotes around the extra name.
_GOOD_REQUIRES = [
    "cryptography>=50.0.0",
    "pyjwt>=2.15.0",
    "importlib-metadata<8.5.0 ; python_version < '3.12'",
    "tritonclient[http]>=2.40",
    "aiohttp>=3.14.3 ; extra == 'nlp'",
    "urllib3>=2.8.0 ; extra == 'nlp'",
]


def _build_dist(tmp_path: Path, wheel_requires: list[str], sdist_requires: list[str]) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    for tag in ("manylinux_2_34_x86_64", "macosx_11_0_x86_64", "win_amd64"):
        _write_wheel(dist, "0.11.1", tag, requires_dist=wheel_requires)
    _write_sdist(dist, "0.11.1", requires_dist=sdist_requires)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_PYPROJECT, encoding="utf-8")
    return dist


def _validate(tmp_path: Path, dist: Path):
    return _load_module().validate(
        dist_dir=dist,
        version="0.11.1",
        require_platforms=_PLATFORMS,
        pyproject_path=tmp_path / "pyproject.toml",
    )


def test_should_accept_built_metadata_that_carries_every_pyproject_requirement(tmp_path: Path):
    dist = _build_dist(tmp_path, _GOOD_REQUIRES, _GOOD_REQUIRES)

    assert _validate(tmp_path, dist) == []


def test_should_fail_when_a_wheel_drops_the_pyjwt_floor(tmp_path: Path):
    # The 2026-10-02 shape: pyproject declares the floor but the artifact users install does not.
    without = [r for r in _GOOD_REQUIRES if not r.startswith("pyjwt")]
    dist = _build_dist(tmp_path, without, _GOOD_REQUIRES)

    errors = _validate(tmp_path, dist)

    assert any("pyjwt>=2.15.0" in err and "wheel" in err.lower() for err in errors), errors


def test_should_fail_when_a_wheel_carries_a_stale_floor(tmp_path: Path):
    stale = ["pyjwt>=2.13.0" if r.startswith("pyjwt") else r for r in _GOOD_REQUIRES]
    dist = _build_dist(tmp_path, stale, _GOOD_REQUIRES)

    errors = _validate(tmp_path, dist)

    assert any("pyjwt>=2.15.0" in err for err in errors), errors


def test_should_fail_when_an_extra_requirement_loses_its_extra_marker(tmp_path: Path):
    # urllib3 leaking into the BASE install (no extra marker) is a different artifact from the
    # one pyproject describes -- and a missing nlp floor would reach nobody.
    leaked = ["urllib3>=2.8.0" if r.startswith("urllib3") else r for r in _GOOD_REQUIRES]
    dist = _build_dist(tmp_path, leaked, _GOOD_REQUIRES)

    errors = _validate(tmp_path, dist)

    assert any("urllib3>=2.8.0" in err and "nlp" in err for err in errors), errors


def test_should_fail_when_the_sdist_drops_a_requirement(tmp_path: Path):
    without = [r for r in _GOOD_REQUIRES if not r.startswith("urllib3")]
    dist = _build_dist(tmp_path, _GOOD_REQUIRES, without)

    errors = _validate(tmp_path, dist)

    assert any("urllib3>=2.8.0" in err and "sdist" in err.lower() for err in errors), errors


def test_should_skip_the_requirements_check_without_a_pyproject(tmp_path: Path):
    # Back-compat for callers (and older tests) that never pass pyproject_path.
    dist = _build_dist(tmp_path, [], [])

    errors = _load_module().validate(dist_dir=dist, version="0.11.1", require_platforms=_PLATFORMS)

    assert errors == []


def test_main_enables_the_requirements_check_against_the_repo_pyproject():
    # CI runs `python scripts/validate_pypi_artifacts.py` with no flags; the check must be on.
    module = _load_module()
    default = module.DEFAULT_PYPROJECT_PATH

    assert default.name == "pyproject.toml" and default.is_file()
