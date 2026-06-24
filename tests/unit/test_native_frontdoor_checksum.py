"""Audit HIGH (2026-06-24): the in-product `tg upgrade` path installs + executes the
native tg front-door binary, but unlike the installers (install.sh / install.ps1 /
npm/install.js, hardened in audit S4) it never verified the download against the
published CHECKSUMS.txt manifest — only a forgeable `--version` smoke test stood in
the way. These tests pin the fail-closed checksum gate.
"""

import hashlib
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main


def _candidate(asset_name: str = "tg-test-asset"):
    return cli_main._NativeFrontdoorAssetCandidate(flavor="cpu", asset_name=asset_name)


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
