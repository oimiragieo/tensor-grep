"""TDD for CEO#8 (enterprise close-the-loop, P1): a review bundle can embed signed
EvidenceReceipts (Change A), and `tg review-bundle verify --against <ref>` gates the whole
bundle closed on a stale, tampered, unsigned, untrusted, dirty, or unresolvable-ref receipt
(Change B) -- never "unknown, so pass".

Bidirectional-oracle discipline throughout: every GREEN case (a signed, fresh, trusted receipt)
must PASS, and every RED case must FAIL closed (``valid=False`` + CLI exit 1) -- see AGENTS.md
"Backend Fail-Closed Contract" and ``ledger_store._finding_is_fresh``'s fail-closed precedent.

Fixtures mirror the established house patterns: a real temp git repo (tests/unit/
test_evidence_receipt.py's ``git_repo`` / tests/unit/test_ledger_store.py's ``_git_init``), a real
signed EvidenceReceipt via ``evidence_receipt.build_evidence_receipt`` (the actual `tg evidence
emit --sign` producer, not a hand-rolled stand-in), and the review-bundle manifest fixture shape
from tests/unit/test_review_bundles.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import audit_manifest, evidence_receipt, evidence_signing
from tensor_grep.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures: a real temp git repo (revision identity must be genuine, not mocked)
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main", "."], cwd=root)
    _run_git(["config", "user.email", "test@example.com"], cwd=root)
    _run_git(["config", "user.name", "Test User"], cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=root)
    _run_git(["commit", "-m", "initial commit"], cwd=root)


def _commit_change(root: Path, *, filename: str = "change.txt", message: str = "advance") -> str:
    (root / filename).write_text("changed\n", encoding="utf-8")
    _run_git(["add", filename], cwd=root)
    _run_git(["commit", "-m", message], cwd=root)
    return _run_git(["rev-parse", "HEAD"], cwd=root).stdout.strip()


def _head_sha(root: Path) -> str:
    return _run_git(["rev-parse", "HEAD"], cwd=root).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    return repo


# ---------------------------------------------------------------------------
# Fixtures: audit manifest (same shape as test_review_bundles.py) + signed receipts
# ---------------------------------------------------------------------------


def _write_audit_manifest(path: Path, *, project_root: Path) -> dict[str, Any]:
    import hashlib

    payload: dict[str, Any] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-07-18T12:00:00Z",
        "lang": "python",
        "path": str(project_root),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "src/sample.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": None,
    }
    canonical = dict(payload)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(canonical, indent=2).encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _make_project_and_manifest(tmp_path: Path, *, name: str = "project") -> Path:
    project = tmp_path / name
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    manifest_path = project / ".tensor-grep" / "audit" / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    return manifest_path


def _signed_receipt_path(
    tmp_path: Path,
    repo: Path,
    *,
    key_path: Path,
    name: str = "receipt.json",
) -> Path:
    """Build+sign a REAL EvidenceReceipt (the actual `tg evidence emit --sign` producer) bound to
    `repo`'s current revision, and persist it -- never a hand-rolled receipt-shaped dict."""
    receipt = evidence_receipt.build_evidence_receipt(
        str(repo), sign=True, signing_key_path=key_path
    )
    receipt_path = tmp_path / name
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


def _unsigned_receipt_path(tmp_path: Path, repo: Path, *, name: str = "unsigned.json") -> Path:
    receipt = evidence_receipt.build_evidence_receipt(str(repo))
    receipt_path = tmp_path / name
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


# ---------------------------------------------------------------------------
# 1. Change A -- bundle carries receipts (+ back-compat)
# ---------------------------------------------------------------------------


def test_create_review_bundle_embeds_evidence_receipts_and_checksum(tmp_path: Path) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    repo = tmp_path / "receipt_repo"
    _init_git_repo(repo)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, repo, key_path=key_path)
    expected_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    bundle = audit_manifest.create_review_bundle(manifest_path, receipt_paths=[receipt_path])

    assert bundle["evidence_receipts"] == [expected_receipt]
    assert "evidence_receipts" in bundle["checksums"]
    assert bundle["checksums"]["evidence_receipts"] == audit_manifest._component_checksum(
        bundle["evidence_receipts"]
    )
    # folded into the whole-bundle digest, not bypassed
    assert bundle["bundle_sha256"] == audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(bundle)
    )


def test_create_review_bundle_without_receipts_sets_evidence_receipts_none(tmp_path: Path) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)

    bundle = audit_manifest.create_review_bundle(manifest_path)

    assert bundle["evidence_receipts"] is None
    assert "evidence_receipts" not in bundle["checksums"]


def test_verify_review_bundle_fresh_receiptless_bundle_still_valid(tmp_path: Path) -> None:
    """A bundle created by the NEW code with no receipts (evidence_receipts: null) must still
    verify green -- adding the optional component must never regress the base contract."""
    manifest_path = _make_project_and_manifest(tmp_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["valid"] is True
    assert payload["checks"]["evidence_receipts"] == {
        "expected": None,
        "actual": None,
        "valid": True,
    }
    assert payload["receipts"] == []


def test_verify_review_bundle_legacy_bundle_missing_evidence_receipts_key_is_back_compat(
    tmp_path: Path,
) -> None:
    """A bundle written by a PRE-PR tg version never had an `evidence_receipts` key at all (not
    even null) -- the true back-compat case. Simulate it by building a bundle then stripping the
    key entirely and recomputing the digest exactly the way the pre-PR writer would have."""
    manifest_path = _make_project_and_manifest(tmp_path)
    bundle = audit_manifest.create_review_bundle(manifest_path)
    del bundle["evidence_receipts"]
    del bundle["checksums"]  # would already omit evidence_receipts; recompute cleanly regardless
    bundle["checksums"] = {
        component: audit_manifest._component_checksum(bundle[component])
        for component in ("audit_manifest", "scan_results", "checkpoint_metadata", "diff")
        if bundle[component] is not None
    }
    bundle["bundle_sha256"] = audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(bundle)
    )
    bundle_path = tmp_path / "legacy-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    assert "evidence_receipts" not in json.loads(bundle_path.read_text(encoding="utf-8"))

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["valid"] is True
    assert payload["receipts"] == []


# ---------------------------------------------------------------------------
# 2. Change B -- git-ref resolver (isolated, explicit root, no chdir needed)
# ---------------------------------------------------------------------------


def test_resolve_git_ref_commit_sha_resolves_head(git_repo: Path) -> None:
    sha, error = audit_manifest._resolve_git_ref_commit_sha("HEAD", root=git_repo)

    assert error is None
    assert sha == _head_sha(git_repo)


def test_resolve_git_ref_commit_sha_fails_closed_on_unknown_ref(git_repo: Path) -> None:
    sha, error = audit_manifest._resolve_git_ref_commit_sha(
        "definitely-not-a-real-ref-xyz", root=git_repo
    )

    assert sha is None
    assert error is not None


def test_resolve_git_ref_commit_sha_fails_closed_outside_a_git_repo(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()

    sha, error = audit_manifest._resolve_git_ref_commit_sha("HEAD", root=not_a_repo)

    assert sha is None
    assert error is not None


# ---------------------------------------------------------------------------
# 3. Change B -- verify --against: GREEN + RED cases (bidirectional oracle)
# ---------------------------------------------------------------------------


def test_verify_review_bundle_green_signed_fresh_trusted_receipt_against_head(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    keypair = evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)

    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    payload = audit_manifest.verify_review_bundle(
        bundle_path,
        against="HEAD",
        trusted_public_keys=[keypair["public_key"]],
        require_trusted=True,
        root=git_repo,
    )

    assert payload["valid"] is True
    assert payload["against"]["valid"] is True
    assert payload["against"]["resolved_commit_sha"] == _head_sha(git_repo)
    assert payload["receipts"][0]["valid"] is True
    assert payload["receipts"][0]["signature"]["checks"]["key_trusted"] is True
    assert payload["receipts"][0]["freshness"]["valid"] is True


def test_verify_review_bundle_red_stale_receipt_after_new_commit(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    _commit_change(git_repo)  # repo moves on; the receipt is now stale

    payload = audit_manifest.verify_review_bundle(bundle_path, against="HEAD", root=git_repo)

    assert payload["valid"] is False
    assert payload["receipts"][0]["freshness"]["valid"] is False
    assert payload["receipts"][0]["valid"] is False


def test_verify_review_bundle_red_tampered_receipt_survives_recomputed_bundle_checksums(
    tmp_path: Path, git_repo: Path
) -> None:
    """Defense in depth: even an attacker sophisticated enough to recompute the KEYLESS
    bundle-level checksums after tampering an embedded receipt still cannot forge the receipt's
    own Ed25519 signature over the new content."""
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["evidence_receipts"][0]["revision"]["dirty"] = True
    bundle["checksums"]["evidence_receipts"] = audit_manifest._component_checksum(
        bundle["evidence_receipts"]
    )
    bundle["bundle_sha256"] = audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(bundle)
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["bundle_integrity"]["valid"] is True  # attacker recomputed these successfully
    assert payload["checks"]["evidence_receipts"]["valid"] is True
    assert payload["receipts"][0]["signature"]["checks"]["digest_valid"] is False
    assert payload["receipts"][0]["valid"] is False
    assert payload["valid"] is False


def test_verify_review_bundle_red_unsigned_receipt_under_require_trusted(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    receipt_path = _unsigned_receipt_path(tmp_path, git_repo)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    payload = audit_manifest.verify_review_bundle(bundle_path, require_trusted=True)

    assert payload["valid"] is False
    assert payload["receipts"][0]["valid"] is False
    assert payload["receipts"][0]["signature"]["signed"] is False


def test_verify_review_bundle_red_untrusted_key_under_require_trusted(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    signer_key = tmp_path / "signer.key"
    evidence_signing.generate_keypair(signer_key)
    other_key = tmp_path / "other.key"
    other_keypair = evidence_signing.generate_keypair(other_key)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=signer_key)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    payload = audit_manifest.verify_review_bundle(
        bundle_path,
        trusted_public_keys=[other_keypair["public_key"]],
        require_trusted=True,
    )

    assert payload["valid"] is False
    assert payload["receipts"][0]["signature"]["checks"]["key_trusted"] is False
    assert payload["receipts"][0]["valid"] is False


def test_verify_review_bundle_red_dirty_receipt_vs_committed_head(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    (git_repo / "scratch.tmp").write_text("uncommitted\n", encoding="utf-8")  # dirty worktree
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["revision"]["dirty"] is True
    assert receipt["revision"]["commit_sha"] == _head_sha(git_repo)  # same commit, dirty tree

    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    payload = audit_manifest.verify_review_bundle(bundle_path, against="HEAD", root=git_repo)

    assert payload["valid"] is False
    assert payload["receipts"][0]["freshness"]["valid"] is False
    assert payload["receipts"][0]["freshness"]["receipt_dirty"] is True


def test_verify_review_bundle_red_unresolvable_against_ref_fails_closed_even_without_receipts(
    tmp_path: Path, git_repo: Path
) -> None:
    """Trap #2: an unresolvable --against ref must fail closed regardless of whether the bundle
    carries any receipts at all -- never "unknown, so skip the check"."""
    manifest_path = _make_project_and_manifest(tmp_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    payload = audit_manifest.verify_review_bundle(
        bundle_path, against="definitely-not-a-real-ref-xyz", root=git_repo
    )

    assert payload["against"]["valid"] is False
    assert payload["against"]["resolved_commit_sha"] is None
    assert payload["against"]["error"]
    assert payload["valid"] is False


def test_verify_review_bundle_bundle_sha256_byte_flip_still_fails_with_receipts(
    tmp_path: Path, git_repo: Path
) -> None:
    """The evidence_receipts addition must not bypass the pre-existing whole-bundle integrity
    gate (task #9 in the plan): a bare bundle_sha256 tamper with everything else untouched still
    fails, exactly like the receipt-less case in test_review_bundles.py."""
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["bundle_sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered), encoding="utf-8")

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["checks"]["evidence_receipts"]["valid"] is True
    assert payload["bundle_integrity"]["valid"] is False
    assert payload["valid"] is False


# ---------------------------------------------------------------------------
# 4. CLI wiring: --receipt / --against / --trusted-key / --require-trusted (+ exit codes)
# ---------------------------------------------------------------------------


def test_cli_review_bundle_create_receipt_flag_embeds_receipt(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(manifest_path),
            "--receipt",
            str(receipt_path),
            "--output",
            str(bundle_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["evidence_receipts"] is not None
    assert len(payload["evidence_receipts"]) == 1


def test_cli_review_bundle_create_text_mode_echoes_evidence_receipts_component(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(manifest_path),
            "--receipt",
            str(receipt_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "evidence_receipts" in result.output


def test_cli_review_bundle_verify_against_green_exits_zero(
    tmp_path: Path, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    keypair = evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    monkeypatch.chdir(git_repo)
    result = runner.invoke(
        app,
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--against",
            "HEAD",
            "--trusted-key",
            keypair["public_key"],
            "--require-trusted",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is True


def test_cli_review_bundle_verify_against_stale_exits_one_json(
    tmp_path: Path, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )
    _commit_change(git_repo)

    monkeypatch.chdir(git_repo)
    result = runner.invoke(
        app, ["review-bundle", "verify", str(bundle_path), "--against", "HEAD", "--json"]
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is False


def test_cli_review_bundle_verify_trusted_key_without_require_trusted_warns_on_stderr(
    tmp_path: Path, git_repo: Path
) -> None:
    manifest_path = _make_project_and_manifest(tmp_path)
    key_path = tmp_path / "key"
    keypair = evidence_signing.generate_keypair(key_path)
    receipt_path = _signed_receipt_path(tmp_path, git_repo, key_path=key_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--trusted-key",
            keypair["public_key"],
            "--json",
        ],
    )

    assert "without --require-trusted" in result.output


def test_cli_review_bundle_verify_unresolvable_against_exits_one_real_subprocess(
    tmp_path: Path, git_repo: Path
) -> None:
    """Real-binary/integration tier (not just CliRunner): drive the actual `python -m
    tensor_grep.cli.main` entry point as a subprocess for the fail-closed --against exit-code
    contract, mirroring tests/unit/test_cli_modes.py's `[sys.executable, "-m",
    "tensor_grep.cli.main", ...]` real-process pattern."""
    manifest_path = _make_project_and_manifest(tmp_path)
    bundle_path = tmp_path / "bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "review-bundle",
            "verify",
            str(bundle_path),
            "--against",
            "definitely-not-a-real-ref-xyz",
            "--json",
        ],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert payload["against"]["valid"] is False
