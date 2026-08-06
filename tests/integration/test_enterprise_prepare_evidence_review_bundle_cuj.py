"""Hermetic enterprise CUJ lock: prepare → evidence emit --sign → review-bundle create/verify.

Campaign W2.b (docs/audits/2026-08-05-enterprise-launch-readiness-census.md PR-C).

Bidirectional oracles (AGENTS.md):
  GREEN — signed receipt + min-receipts 1 against HEAD → valid + exit 0
  RED   — strip evidence_receipts (recompute checksums) → valid=false + exit ≠ 0
  RED   — wrong --against SHA → fail closed
  RED   — evidence emit --sign with no key → no receipt written

Real subprocess (`python -m tensor_grep`), not CliRunner — dogfood the bootstrap front door.
Anti-hang: OS-level timeout on every child.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import audit_manifest

_SUBPROCESS_TIMEOUT_S = 90.0

_BILLING_MODULE = (
    '"""Monthly billing helpers."""\n\n\n'
    "def calculate_late_fee(balance, days_late):\n"
    '    """Compute the late fee owed on an overdue balance."""\n'
    "    return balance * 0.01 * days_late\n\n\n"
    "def apply_late_fee(account):\n"
    '    """Apply the computed late fee to an account balance."""\n'
    '    fee = calculate_late_fee(account["balance"], account["days_late"])\n'
    '    account["balance"] += fee\n'
    "    return account\n"
)
_PYPROJECT = '[project]\nname = "billing-fixture"\nversion = "0.1.0"\n'


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TG_SESSION_DAEMON_AUTOSTART"] = "0"
    # Isolate from any ambient operator signing key so the no-key RED arm is real.
    env.pop("TG_EVIDENCE_SIGNING_KEY", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _write_audit_manifest(path: Path, *, project_root: Path) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-08-05T12:00:00Z",
        "lang": "python",
        "path": str(project_root),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "billing.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": None,
    }
    canonical = dict(payload)
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(canonical, indent=2).encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture
def enterprise_repo(tmp_path: Path) -> Path:
    """Tiny git-backed billing fixture suitable for prepare + evidence binding."""
    root = tmp_path / "billing"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (root / "billing.py").write_text(_BILLING_MODULE, encoding="utf-8")
    _run_git(["init", "-b", "main", "."], cwd=root)
    _run_git(["config", "user.email", "cuj@example.com"], cwd=root)
    _run_git(["config", "user.name", "CUJ"], cwd=root)
    _run_git(["add", "."], cwd=root)
    _run_git(["commit", "-m", "initial"], cwd=root)
    return root


def test_enterprise_cuj_prepare_evidence_review_bundle_chain(
    enterprise_repo: Path, tmp_path: Path
) -> None:
    """Full launch-bar chain with positive control + three fail-closed arms."""
    work = tmp_path / "artifacts"
    work.mkdir()
    capsule_path = work / "capsule.json"
    receipt_path = work / "receipt.json"
    bundle_path = work / "review-bundle.json"
    key_path = work / "signing-key"
    pubkey_path = work / "signing-key.pub"
    manifest_path = work / "audit-manifest.json"
    missing_key = work / "missing" / "key"

    # --- prepare (agent edit-readiness surface) ---
    prep = _run_tg(
        [
            "prepare",
            str(enterprise_repo),
            "calculate_late_fee",
            "--out",
            str(capsule_path),
            "--json",
        ],
        cwd=enterprise_repo,
    )
    assert prep.returncode == 0, prep.stdout + prep.stderr
    assert capsule_path.is_file(), "prepare --out must materialize capsule.json"
    capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    assert isinstance(capsule, dict)
    assert capsule.get("primary_target") or capsule.get("navigation_pack"), capsule

    # --- evidence emit --sign fail-closed (no resolvable key) ---
    no_key_out = work / "must-not-exist.json"
    sign_fail = _run_tg(
        [
            "evidence",
            "emit",
            str(enterprise_repo),
            "--capsule",
            str(capsule_path),
            "--sign",
            "--signing-key",
            str(missing_key),
            "--out",
            str(no_key_out),
        ],
        cwd=enterprise_repo,
    )
    assert sign_fail.returncode != 0, (
        "evidence emit --sign without a resolvable key must fail closed"
        f"\nstdout={sign_fail.stdout}\nstderr={sign_fail.stderr}"
    )
    assert not no_key_out.exists(), "fail-closed --sign must not write a receipt"

    keygen = _run_tg(["evidence", "keygen", "--out", str(key_path)], cwd=enterprise_repo)
    assert keygen.returncode == 0, keygen.stdout + keygen.stderr
    assert key_path.is_file()
    if not pubkey_path.is_file():
        pub_candidates = list(work.glob("*.pub"))
        assert pub_candidates, "keygen must emit a public key for --trusted-key"
        pubkey_path = pub_candidates[0]

    emit = _run_tg(
        [
            "evidence",
            "emit",
            str(enterprise_repo),
            "--capsule",
            str(capsule_path),
            "--sign",
            "--signing-key",
            str(key_path),
            "--out",
            str(receipt_path),
        ],
        cwd=enterprise_repo,
    )
    assert emit.returncode == 0, emit.stdout + emit.stderr
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt.get("signature") or receipt.get("signing"), receipt

    # --- review-bundle create --receipt ---
    _write_audit_manifest(manifest_path, project_root=enterprise_repo)
    create = _run_tg(
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
        cwd=enterprise_repo,
    )
    assert create.returncode == 0, create.stdout + create.stderr
    assert bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle.get("evidence_receipts"), bundle

    head = _run_git(["rev-parse", "HEAD"], cwd=enterprise_repo).stdout.strip()

    # --- GREEN: verify --against HEAD --min-receipts 1 ---
    verify_ok = _run_tg(
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--against",
            head,
            "--min-receipts",
            "1",
            "--trusted-key",
            str(pubkey_path),
            "--json",
        ],
        cwd=enterprise_repo,
    )
    assert verify_ok.returncode == 0, verify_ok.stdout + verify_ok.stderr
    ok_payload = json.loads(verify_ok.stdout)
    assert ok_payload.get("valid") is True, ok_payload
    assert ok_payload.get("policy", {}).get("min_receipts_satisfied") is True, ok_payload

    # --- RED: strip receipts + recompute keyless checksums ---
    stripped = json.loads(bundle_path.read_text(encoding="utf-8"))
    stripped["evidence_receipts"] = []
    if "checksums" in stripped and isinstance(stripped["checksums"], dict):
        stripped["checksums"]["evidence_receipts"] = audit_manifest._component_checksum([])
    stripped["bundle_sha256"] = audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(stripped)
    )
    stripped_path = work / "stripped-bundle.json"
    stripped_path.write_text(json.dumps(stripped), encoding="utf-8")

    verify_stripped = _run_tg(
        [
            "review-bundle",
            "verify",
            str(stripped_path),
            "--against",
            head,
            "--min-receipts",
            "1",
            "--json",
        ],
        cwd=enterprise_repo,
    )
    assert verify_stripped.returncode != 0, verify_stripped.stdout
    stripped_payload = json.loads(verify_stripped.stdout)
    assert stripped_payload.get("valid") is False, stripped_payload

    # --- RED: wrong --against SHA ---
    wrong_sha = "0" * 40
    verify_wrong = _run_tg(
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--against",
            wrong_sha,
            "--min-receipts",
            "1",
            "--trusted-key",
            str(pubkey_path),
            "--json",
        ],
        cwd=enterprise_repo,
    )
    assert verify_wrong.returncode != 0, verify_wrong.stdout
    wrong_payload = json.loads(verify_wrong.stdout)
    assert wrong_payload.get("valid") is False, wrong_payload
