"""Audit HIGH (2026-06-24): the in-product `tg upgrade` path installs + executes the
native tg front-door binary, but unlike the installers (install.sh / install.ps1 /
npm/install.js, hardened in audit S4) it never verified the download against the
published CHECKSUMS.txt manifest — only a forgeable `--version` smoke test stood in
the way. These tests pin the fail-closed checksum gate.

P0-5 (GPU Phase-0 honesty, 2026-07-14): when a caller requests the nvidia native
front-door flavor but only the cpu asset installs (nvidia unavailable, or no nvidia
asset exists for this platform at all), `_install_release_native_frontdoor` used to
install cpu silently. These tests pin the loud stderr downgrade warning.
"""

import hashlib
import json
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main


def _candidate(asset_name: str = "tg-test-asset", flavor: str = "cpu"):
    return cli_main._NativeFrontdoorAssetCandidate(flavor=flavor, asset_name=asset_name)


def test_expected_asset_sha256_parses_checksums_manifest():
    text = "abc123  tg-linux-amd64-cpu\n# a comment line\ndef456  tg-macos-arm64-cpu\n"
    assert cli_main._expected_asset_sha256(text, "tg-macos-arm64-cpu") == "def456"
    assert cli_main._expected_asset_sha256(text, "tg-linux-amd64-cpu") == "abc123"
    assert cli_main._expected_asset_sha256(text, "tg-windows-amd64-cpu") is None


def test_install_release_native_frontdoor_rejects_tampered_asset(tmp_path, monkeypatch):
    cand = _candidate()
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [(cand, "https://example.invalid/tg-test-asset")],
    )
    # The bytes an attacker actually served:
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(b"TAMPERED-BINARY"),
    )
    # The published manifest lists the legitimate (different) hash:
    legit_digest = hashlib.sha256(b"LEGIT-BINARY").hexdigest()
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda version: f"{legit_digest}  tg-test-asset\n",
    )
    # Make the existing --version smoke test pass so the ONLY thing under test is the checksum.
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "1.2.3")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *a, **k: True)

    dest = tmp_path / "tg.exe"
    with pytest.raises(RuntimeError) as exc:
        cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert "checksum mismatch" in str(exc.value).lower()
    assert not dest.exists()  # tampered binary must NOT be installed


def test_install_release_native_frontdoor_installs_verified_asset(tmp_path, monkeypatch):
    cand = _candidate()
    asset_bytes = b"LEGIT-BINARY"
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [(cand, "https://example.invalid/tg-test-asset")],
    )
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(asset_bytes),
    )
    digest = hashlib.sha256(asset_bytes).hexdigest()
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda version: f"{digest}  tg-test-asset\n",
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "1.2.3")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *a, **k: True)
    monkeypatch.setattr(cli_main, "_write_native_frontdoor_metadata", lambda *a, **k: None)

    dest = tmp_path / "tg.exe"
    result = cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert dest.read_bytes() == asset_bytes
    assert result.asset_name == "tg-test-asset"


def test_install_release_native_frontdoor_refuses_when_checksums_unavailable(tmp_path, monkeypatch):
    cand = _candidate()
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [(cand, "https://example.invalid/tg-test-asset")],
    )
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(b"unverifiable"),
    )
    # CHECKSUMS.txt could not be fetched -> nothing can be verified -> fail closed.
    monkeypatch.setattr(cli_main, "_fetch_native_frontdoor_checksums", lambda version: None)

    dest = tmp_path / "tg.exe"
    with pytest.raises(RuntimeError) as exc:
        cli_main._install_release_native_frontdoor("1.2.3", dest)

    message = str(exc.value).lower()
    assert "checksums" in message or "unverified" in message
    assert not dest.exists()


def _stub_checksum_and_version_gates(monkeypatch, *, digest: str, asset_name: str) -> None:
    """Make the checksum + --version smoke-test gates pass so the ONLY thing under test in
    the P0-5 tests below is the nvidia->cpu downgrade warning, matching the existing
    tampered/verified-asset tests' pattern of isolating one gate at a time."""
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda version: f"{digest}  {asset_name}\n",
    )
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "1.2.3")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *a, **k: True)


def test_install_release_native_frontdoor_warns_on_nvidia_to_cpu_downgrade(
    tmp_path, monkeypatch, capsys
):
    # (a) requested=nvidia, nvidia download fails, cpu succeeds -> stderr carries the
    # downgrade warning + the nvidia failure reason; metadata records requested=nvidia,
    # installed asset=cpu.
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)

    nvidia_cand = _candidate("tg-linux-amd64-nvidia", flavor="nvidia")
    cpu_cand = _candidate("tg-linux-amd64-cpu", flavor="cpu")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [
            (nvidia_cand, "https://example.invalid/tg-linux-amd64-nvidia"),
            (cpu_cand, "https://example.invalid/tg-linux-amd64-cpu"),
        ],
    )

    asset_bytes = b"LEGIT-CPU-BINARY"

    def _fake_download(url: str, dest) -> None:
        if "nvidia" in url:
            raise RuntimeError("404 Not Found: no nvidia asset for this release")
        Path(dest).write_bytes(asset_bytes)

    monkeypatch.setattr(cli_main, "_download_native_frontdoor_asset", _fake_download)
    _stub_checksum_and_version_gates(
        monkeypatch,
        digest=hashlib.sha256(asset_bytes).hexdigest(),
        asset_name="tg-linux-amd64-cpu",
    )

    dest = tmp_path / "tg.exe"
    result = cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert result.flavor == "cpu"
    warning = capsys.readouterr().err
    assert "nvidia" in warning.lower()
    assert "404 not found" in warning.lower()
    assert "cpu" in warning.lower()
    assert "tg doctor" in warning.lower()

    metadata = json.loads(cli_main.native_frontdoor_metadata_path(dest).read_text(encoding="utf-8"))
    assert metadata["requested_asset_flavor"] == "nvidia"
    assert metadata["asset_flavor"] == "cpu"


def test_install_release_native_frontdoor_silent_on_default_cpu_request(
    tmp_path, monkeypatch, capsys
):
    # (b) requested=cpu (the default) -> installing cpu matches the request -> NO warning.
    monkeypatch.delenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", raising=False)
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)

    cpu_cand = _candidate("tg-linux-amd64-cpu", flavor="cpu")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [(cpu_cand, "https://example.invalid/tg-linux-amd64-cpu")],
    )
    asset_bytes = b"LEGIT-CPU-BINARY"
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(asset_bytes),
    )
    _stub_checksum_and_version_gates(
        monkeypatch,
        digest=hashlib.sha256(asset_bytes).hexdigest(),
        asset_name="tg-linux-amd64-cpu",
    )
    monkeypatch.setattr(cli_main, "_write_native_frontdoor_metadata", lambda *a, **k: None)

    dest = tmp_path / "tg.exe"
    result = cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert result.flavor == "cpu"
    assert capsys.readouterr().err == ""


def test_install_release_native_frontdoor_silent_when_nvidia_succeeds(
    tmp_path, monkeypatch, capsys
):
    # (c) requested=nvidia, nvidia succeeds on the first try -> installed flavor matches the
    # request -> NO warning.
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)

    nvidia_cand = _candidate("tg-linux-amd64-nvidia", flavor="nvidia")
    cpu_cand = _candidate("tg-linux-amd64-cpu", flavor="cpu")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [
            (nvidia_cand, "https://example.invalid/tg-linux-amd64-nvidia"),
            (cpu_cand, "https://example.invalid/tg-linux-amd64-cpu"),
        ],
    )
    asset_bytes = b"LEGIT-NVIDIA-BINARY"
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(asset_bytes),
    )
    _stub_checksum_and_version_gates(
        monkeypatch,
        digest=hashlib.sha256(asset_bytes).hexdigest(),
        asset_name="tg-linux-amd64-nvidia",
    )
    monkeypatch.setattr(cli_main, "_write_native_frontdoor_metadata", lambda *a, **k: None)

    dest = tmp_path / "tg.exe"
    result = cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert result.flavor == "nvidia"
    assert capsys.readouterr().err == ""


def test_install_release_native_frontdoor_warns_honestly_when_no_nvidia_candidate(
    tmp_path, monkeypatch, capsys
):
    # (d) SF-1: requested=nvidia but NO nvidia candidate exists at all for this platform
    # (simulating darwin / a non-amd64 arch, where _native_frontdoor_asset_candidates never
    # appends an nvidia entry) -> download_errors has NO nvidia-flavored entry to report, so
    # the warning must fall back to the honest "no NVIDIA asset is published for this
    # platform" text instead of indexing blindly into an empty/mismatched reason.
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)

    cpu_cand = _candidate("tg-macos-amd64-cpu", flavor="cpu")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda version: [(cpu_cand, "https://example.invalid/tg-macos-amd64-cpu")],
    )
    asset_bytes = b"LEGIT-CPU-BINARY"
    monkeypatch.setattr(
        cli_main,
        "_download_native_frontdoor_asset",
        lambda url, dest: Path(dest).write_bytes(asset_bytes),
    )
    _stub_checksum_and_version_gates(
        monkeypatch,
        digest=hashlib.sha256(asset_bytes).hexdigest(),
        asset_name="tg-macos-amd64-cpu",
    )
    monkeypatch.setattr(cli_main, "_write_native_frontdoor_metadata", lambda *a, **k: None)

    dest = tmp_path / "tg.exe"
    result = cli_main._install_release_native_frontdoor("1.2.3", dest)

    assert result.flavor == "cpu"
    warning = capsys.readouterr().err
    assert "no nvidia asset is published for this platform" in warning.lower()
    assert "tg doctor" in warning.lower()
