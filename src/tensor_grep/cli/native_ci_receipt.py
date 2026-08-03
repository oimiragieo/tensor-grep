"""Behaviorless Round-60 seam for NativeCiReceiptV1 (Task 2A / #89).

Strict load rejects duplicate/unknown keys only after GREEN implements the
parser. The verifier accepts primitive read-only artifact source/raw paths and
must independently derive live tuple / JUnit / Rust census / digests — never
caller-supplied claims. Fail-closed stubs keep the positive control RED.
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
    """Independently derive the current-run Actions/artifact tuple.

    Behaviorless: returns empty placeholders. Semantic RED requires derivation
    from live GITHUB_* / artifact-download context, not receipt echo.
    """
    _ = environ
    return LiveActionsTuple(
        repository="",
        commit_sha="",
        workflow_run_id="",
        run_attempt="",
        job_name="",
        runner_identity_sha256="",
        artifact_namespace="",
    )


def require_empty_current_run_directory(path: Path) -> bool:
    """Receipts may be created only in a freshly empty current-run directory.

    Behaviorless: always True (accepts seeded/nonempty dirs).
    """
    _ = path
    return True


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
    _ = receipt, environ, current_run_dir, expected_attribution
    # Behaviorless: do not derive / do not accept.
    raise NotImplementedError(
        "verify_native_ci_receipt must independently derive live tuple/JUnit/Rust/"
        "digests from artifact_source paths; derivation not implemented"
    )
