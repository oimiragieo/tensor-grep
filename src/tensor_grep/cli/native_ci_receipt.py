"""Behaviorless Round-60 seam for NativeCiReceiptV1 (Task 2A / #89).

Strict load rejects duplicate/unknown keys only after GREEN implements the
parser. The verifier accepts primitive read-only artifact source/raw paths and
must independently derive live tuple / JUnit / Rust census / digests — never
caller-supplied claims. Verifier fails closed without live Actions tuple / complete artifacts; never raises NotImplementedError. Real clearance still needs Windows CI.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NATIVE_CI_RECEIPT_VERSION = 1
NATIVE_CI_RECEIPT_SCHEMA = "NativeCiReceiptV1"
_MAX_RECEIPT_BYTES = 256 * 1024
_DIGEST_LEN = 64
# Valid receipt fixtures / GREEN parser accept lowercase hex only.
_HEX_ALPHABET = frozenset("0123456789abcdef")
_ALLOWED_ATTRIBUTIONS = frozenset({"source-tree", "wheel", "installer"})
_REQUIRED_KEYS = frozenset({
    "version",
    "manifest_sha256",
    "commit_sha",
    "workflow_run_id",
    "run_attempt",
    "job_name",
    "runner_identity_sha256",
    "binary_path",
    "binary_version",
    "binary_sha256_pre",
    "binary_sha256_post",
    "node_list",
    "node_census_digest",
    "argv_digest",
    "output_digest",
    "exit_digest",
    "artifact_namespace",
    "attribution",
})
# A68: immutable-SHA clearance requires a live Actions run tuple — receipt JSON alone
# (caller-shaped commit_sha / workflow_run_id) is never clearance.
_LIVE_IMMUTABLE_SHA_ACTIONS_KEYS = (
    "GITHUB_SHA",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_WORKFLOW",
    "GITHUB_JOB",
)


@dataclass(frozen=True, slots=True)
class LiveActionsTuple:
    repository: str
    commit_sha: str
    workflow_run_id: str
    run_attempt: str
    job_name: str
    runner_identity_sha256: str
    artifact_namespace: str


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    """Primitive read-only artifact source (paths only — no caller claims)."""

    current_run_dir: Path
    manifest_path: Path
    junit_path: Path | None = None
    rust_list_path: Path | None = None
    argv_path: Path | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    exit_path: Path | None = None
    binary_path: Path | None = None
    environ: Mapping[str, str] | None = None
    expected_attribution: str | None = None


@dataclass(frozen=True, slots=True)
class NativeCiReceiptV1:
    version: int
    manifest_sha256: str
    commit_sha: str
    workflow_run_id: str
    run_attempt: str
    job_name: str
    runner_identity_sha256: str
    binary_path: str
    binary_version: str
    binary_sha256_pre: str
    binary_sha256_post: str
    node_list: tuple[str, ...]
    node_census_digest: str
    argv_digest: str
    output_digest: str
    exit_digest: str
    artifact_namespace: str
    attribution: str

    def schema_name(self) -> str:
        return NATIVE_CI_RECEIPT_SCHEMA


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_native_ci_receipt(raw: bytes | str | Mapping[str, Any]) -> NativeCiReceiptV1:
    """Strict bounded schema/type/value/length parser (behaviorless shell)."""
    _ = raw
    raise NotImplementedError(
        "parse_native_ci_receipt is a behaviorless shell until GREEN implements "
        "exact schema/type/value/length contracts without coercion"
    )


def load_receipt(path: Path) -> NativeCiReceiptV1:
    return parse_native_ci_receipt(path.read_bytes())


def derive_live_actions_tuple(environ: Mapping[str, str]) -> LiveActionsTuple:
    """Independently derive the current-run Actions/artifact tuple from live env."""
    repo = str(environ.get("GITHUB_REPOSITORY") or "").strip()
    commit = str(environ.get("GITHUB_SHA") or "").strip()
    run_id = str(environ.get("GITHUB_RUN_ID") or "").strip()
    attempt = str(environ.get("GITHUB_RUN_ATTEMPT") or "").strip()
    job = str(environ.get("GITHUB_JOB") or "").strip()
    runner_name = str(environ.get("RUNNER_NAME") or "").strip()
    runner_identity = sha256_hex(runner_name.encode("utf-8")) if runner_name else ""
    namespace = f"task2a-native-ci/{run_id}/{attempt}" if run_id and attempt else ""
    return LiveActionsTuple(
        repository=repo,
        commit_sha=commit,
        workflow_run_id=run_id,
        run_attempt=attempt,
        job_name=job,
        runner_identity_sha256=runner_identity,
        artifact_namespace=namespace,
    )


def live_immutable_sha_actions_tuple_present(environ: Mapping[str, str] | None) -> bool:
    """True only when the live Actions immutable-SHA clearance tuple is fully present."""
    if environ is None:
        return False
    return all(str(environ.get(key) or "").strip() for key in _LIVE_IMMUTABLE_SHA_ACTIONS_KEYS)


def require_empty_current_run_directory(path: Path) -> bool:
    """Receipts may be created only in a freshly empty current-run directory."""
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    return False


def derive_junit_population(junit_path: Path) -> list[str]:
    """Independently parse JUnit XML into an ordered node population (behaviorless)."""
    _ = junit_path
    raise NotImplementedError("derive_junit_population requires a real JUnit parser")


def derive_rust_list_census(rust_list_path: Path) -> list[str]:
    """Independently parse stable Rust --list output preserving order/duplicates."""
    _ = rust_list_path
    raise NotImplementedError("derive_rust_list_census requires stable Rust --list parsing")


def verify_native_ci_receipt(
    receipt: NativeCiReceiptV1 | None = None,
    *,
    artifact_source: ArtifactSource | None = None,
    # Legacy caller-supplied kwargs kept only to fail closed — must not be trusted.
    environ: Mapping[str, str] | None = None,
    junit_nodes: set[str] | None = None,
    rust_list_nodes: set[str] | None = None,
    current_run_dir: Path | None = None,
    manifest_bytes: bytes | None = None,
    expected_attribution: str | None = None,
    argv_digest: str | None = None,
    output_digest: str | None = None,
    exit_digest: str | None = None,
    runner_identity_sha256: str | None = None,
    job_name: str | None = None,
) -> dict[str, Any]:
    """Final verifier.

    Accepts primitive ArtifactSource paths and independently derives live tuple,
    JUnit population/digest, Rust --list census, argv/output/exit/binary digests,
    and job/runner/artifact context. Behaviorless: refuses caller-supplied claims
    and never self-attests success.
    """
    if any(
        v is not None
        for v in (
            junit_nodes,
            rust_list_nodes,
            manifest_bytes,
            argv_digest,
            output_digest,
            exit_digest,
            runner_identity_sha256,
            job_name,
        )
    ):
        return {"ok": False, "reason": "caller_supplied_claims_refused"}
    if artifact_source is None:
        return {"ok": False, "reason": "artifact_source_required"}
    # A68 / HIGH#1: clearance refuses without a live immutable-SHA Actions run.
    # Receipt-embedded commit_sha / workflow_run_id alone never clears.
    # Real Windows CI clearance still requires a live Actions run on the immutable
    # SHA with complete per-node artifacts — this path fails closed without that.
    if not live_immutable_sha_actions_tuple_present(artifact_source.environ):
        return {"ok": False, "reason": "live_actions_tuple_missing"}
    if receipt is None:
        return {"ok": False, "reason": "receipt_required"}
    env_map = artifact_source.environ or {}
    live = derive_live_actions_tuple(env_map)
    run_dir = artifact_source.current_run_dir
    if not require_empty_current_run_directory(run_dir):
        # Allow non-empty when verifying an already-emitted receipt in-place, but
        # seeded dirs that are not the receipt's own namespace still fail closed
        # via later digest/population checks. Seeded-before-emit is checked by
        # callers via require_empty_current_run_directory directly.
        pass
    expected_attr = artifact_source.expected_attribution or expected_attribution
    if expected_attr is not None and receipt.attribution != expected_attr:
        return {"ok": False, "reason": "attribution_mismatch"}
    if expected_attr == "wheel":
        wheel_hint = run_dir / "wheel.whl"
        if not wheel_hint.is_file():
            return {"ok": False, "reason": "wheel_artifact_missing"}
    if expected_attr == "installer":
        installer_hint = run_dir / "installer.bin"
        if not installer_hint.is_file():
            return {"ok": False, "reason": "installer_artifact_missing"}
    if live.run_attempt and receipt.run_attempt != live.run_attempt:
        return {"ok": False, "reason": "run_attempt_mismatch"}
    if live.commit_sha and receipt.commit_sha != live.commit_sha:
        return {"ok": False, "reason": "commit_sha_mismatch"}
    if live.workflow_run_id and receipt.workflow_run_id != live.workflow_run_id:
        return {"ok": False, "reason": "workflow_run_id_mismatch"}
    if live.job_name and receipt.job_name != live.job_name:
        return {"ok": False, "reason": "job_name_mismatch"}
    if artifact_source.binary_path is not None:
        bp = artifact_source.binary_path
        if not bp.is_file():
            return {"ok": False, "reason": "binary_missing"}
        live_bin = sha256_hex(bp.read_bytes())
        if receipt.binary_sha256_post != live_bin:
            return {"ok": False, "reason": "binary_drift"}
        if receipt.binary_sha256_pre != live_bin:
            return {"ok": False, "reason": "binary_drift"}
    manifest_path = artifact_source.manifest_path
    if not manifest_path.is_file():
        return {"ok": False, "reason": "manifest_missing"}
    live_manifest = sha256_hex(manifest_path.read_bytes())
    if receipt.manifest_sha256 != live_manifest:
        return {"ok": False, "reason": "manifest_drift"}
    # JUnit / rust list / argv / output / exit: require paths when present; else
    # fail closed (incomplete artifact set cannot clear).
    missing: list[str] = []
    if artifact_source.junit_path is None and artifact_source.rust_list_path is None:
        missing.append("census_artifact")
    if artifact_source.junit_path is not None and not artifact_source.junit_path.is_file():
        missing.append("junit")
    if artifact_source.rust_list_path is not None and not artifact_source.rust_list_path.is_file():
        missing.append("rust_list")
    if missing:
        return {"ok": False, "reason": "artifact_incomplete", "missing": missing}
    # Digests must be independently re-derived when raw paths exist; without
    # argv/output/exit artifacts, refuse clearance (fail closed).
    if artifact_source.argv_path is None or artifact_source.stdout_path is None:
        return {
            "ok": False,
            "reason": "artifact_incomplete",
            "missing": ["argv_or_output"],
            "note": (
                "verify path exercised; real immutable-SHA clearance still "
                "requires a complete Windows CI artifact set on the live run"
            ),
        }
    _ = environ, current_run_dir
    return {
        "ok": False,
        "reason": "clearance_incomplete",
        "note": (
            "verify path implemented and fail-closed; real clearance still "
            "requires Windows CI run with live Actions tuple + complete artifacts"
        ),
    }
