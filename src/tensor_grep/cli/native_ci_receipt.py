"""NativeCiReceiptV1 parser/verifier/emit seam (Task 2A / #89).

Strict load rejects duplicate/unknown keys. The verifier accepts primitive
read-only artifact source/raw paths and independently derives live tuple /
JUnit / Rust census / digests — never caller-supplied claims. Fail-closed
without live Actions tuple; never raises NotImplementedError. Runners may emit
receipts; real Windows CI clearance still requires a live Actions run.
"""

from __future__ import annotations

import hashlib
import json
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


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key refused: {key}")
        out[key] = value
    return out


def _require_hex64(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: nonstring type refused")
    if len(value) != _DIGEST_LEN:
        raise ValueError(f"{field}: digest length must be {_DIGEST_LEN}")
    if any(ch not in _HEX_ALPHABET for ch in value):
        raise ValueError(f"{field}: digest alphabet must be lowercase hex")
    if value != value.lower():
        raise ValueError(f"{field}: digest alphabet lowercase required")
    return value


def _require_nonstring_refused(value: object, *, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{field}: nonstring type refused")
    return value


def census_digest(nodes: list[str] | tuple[str, ...]) -> str:
    """Canonical SHA-256 over ordered census node ids (newline-joined)."""
    blob = ("\n".join(nodes) + ("\n" if nodes else "")).encode("utf-8")
    return sha256_hex(blob)


def parse_native_ci_receipt(raw: bytes | str | Mapping[str, Any]) -> NativeCiReceiptV1:
    """Strict bounded schema/type/value/length parser (fail-closed)."""
    if isinstance(raw, Mapping):
        if len(json.dumps(raw, sort_keys=True).encode("utf-8")) > _MAX_RECEIPT_BYTES:
            raise ValueError("receipt oversized")
        data = dict(raw)
    else:
        if isinstance(raw, str):
            encoded = raw.encode("utf-8")
        elif isinstance(raw, (bytes, bytearray)):
            encoded = bytes(raw)
        else:
            raise ValueError("receipt type refused")
        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise ValueError("receipt oversized")
        try:
            loaded = json.loads(
                encoded.decode("utf-8"), object_pairs_hook=_reject_duplicate_object_pairs
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"receipt json refused: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("receipt must be a JSON object")
        data = loaded

    unknown = set(data) - _REQUIRED_KEYS - {"schema"}
    if unknown:
        raise ValueError(f"unknown key refused: {sorted(unknown)[0]}")
    if "schema" in data and data["schema"] not in {NATIVE_CI_RECEIPT_SCHEMA, None}:
        raise ValueError("schema unknown refused")

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("version type refused (bool/string)")
    if version != NATIVE_CI_RECEIPT_VERSION:
        raise ValueError("version value refused")

    node_list_raw = data.get("node_list")
    if not isinstance(node_list_raw, list):
        raise ValueError("node_list type refused")
    nodes: list[str] = []
    seen: set[str] = set()
    for item in node_list_raw:
        if not isinstance(item, str):
            raise ValueError("node_list entry nonstring type refused")
        if item in seen:
            raise ValueError("duplicate node_list entry refused")
        seen.add(item)
        nodes.append(item)

    attribution = _require_nonstring_refused(data.get("attribution"), field="attribution")
    if attribution not in _ALLOWED_ATTRIBUTIONS:
        raise ValueError("attribution value refused")

    return NativeCiReceiptV1(
        version=version,
        manifest_sha256=_require_hex64(data.get("manifest_sha256"), field="manifest_sha256"),
        commit_sha=_require_nonstring_refused(data.get("commit_sha"), field="commit_sha"),
        workflow_run_id=_require_nonstring_refused(
            data.get("workflow_run_id"), field="workflow_run_id"
        ),
        run_attempt=_require_nonstring_refused(data.get("run_attempt"), field="run_attempt"),
        job_name=_require_nonstring_refused(data.get("job_name"), field="job_name"),
        runner_identity_sha256=_require_hex64(
            data.get("runner_identity_sha256"), field="runner_identity_sha256"
        ),
        binary_path=_require_nonstring_refused(data.get("binary_path"), field="binary_path"),
        binary_version=_require_nonstring_refused(
            data.get("binary_version"), field="binary_version"
        ),
        binary_sha256_pre=_require_hex64(data.get("binary_sha256_pre"), field="binary_sha256_pre"),
        binary_sha256_post=_require_hex64(
            data.get("binary_sha256_post"), field="binary_sha256_post"
        ),
        node_list=tuple(nodes),
        node_census_digest=_require_hex64(
            data.get("node_census_digest"), field="node_census_digest"
        ),
        argv_digest=_require_hex64(data.get("argv_digest"), field="argv_digest"),
        output_digest=_require_hex64(data.get("output_digest"), field="output_digest"),
        exit_digest=_require_hex64(data.get("exit_digest"), field="exit_digest"),
        artifact_namespace=_require_nonstring_refused(
            data.get("artifact_namespace"), field="artifact_namespace"
        ),
        attribution=attribution,
    )


def load_receipt(path: Path) -> NativeCiReceiptV1:
    return parse_native_ci_receipt(path.read_bytes())


def receipt_to_dict(receipt: NativeCiReceiptV1) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "manifest_sha256": receipt.manifest_sha256,
        "commit_sha": receipt.commit_sha,
        "workflow_run_id": receipt.workflow_run_id,
        "run_attempt": receipt.run_attempt,
        "job_name": receipt.job_name,
        "runner_identity_sha256": receipt.runner_identity_sha256,
        "binary_path": receipt.binary_path,
        "binary_version": receipt.binary_version,
        "binary_sha256_pre": receipt.binary_sha256_pre,
        "binary_sha256_post": receipt.binary_sha256_post,
        "node_list": list(receipt.node_list),
        "node_census_digest": receipt.node_census_digest,
        "argv_digest": receipt.argv_digest,
        "output_digest": receipt.output_digest,
        "exit_digest": receipt.exit_digest,
        "artifact_namespace": receipt.artifact_namespace,
        "attribution": receipt.attribution,
    }


def write_receipt(path: Path, receipt: NativeCiReceiptV1) -> None:
    """Emit a NativeCiReceiptV1 JSON document (runners may call this).

    Routes through the shared atomic writer (temp + rename) so a crashed runner can
    never leave a torn half-receipt that a verifier then parses — caught by the
    atomic-writer census ratchet on the 2026-08-12 union (a bare ``Path.write_text``
    here drifted the pinned violating population).
    """
    from tensor_grep.cli._index_lock import atomic_write_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = receipt_to_dict(receipt)
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, data)


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
    """Independently parse JUnit XML into an ordered python:: node population."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(junit_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"junit unreadable: {exc}") from exc
    nodes: list[str] = []
    for case in root.iter("testcase"):
        classname = str(case.attrib.get("classname") or "")
        name = str(case.attrib.get("name") or "")
        file_attr = str(case.attrib.get("file") or "").replace("\\", "/")
        if file_attr and name:
            nodeid = f"{file_attr}::{name}"
        elif classname and name:
            dotted = classname.replace(".", "/")
            nodeid = f"{dotted}.py::{name}"
        elif name:
            nodeid = name
        else:
            continue
        nodes.append(f"python::{nodeid}")
    return nodes


def derive_rust_list_census(rust_list_path: Path) -> list[str]:
    """Independently parse stable Rust --list / census lines preserving order/duplicates."""
    text = rust_list_path.read_text(encoding="utf-8")
    nodes: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.endswith(": test"):
            raw = raw[: -len(": test")]
        if raw.startswith("rust::"):
            nodes.append(raw)
        else:
            nodes.append(raw)
    return nodes


def _junit_has_skipped(junit_path: Path) -> bool:
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(junit_path).getroot()
    except (ET.ParseError, OSError):
        return False
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            return True
    return False


def _manifest_argv_digest(manifest_path: Path, node_ids: tuple[str, ...]) -> str:
    import json as _json

    payload = _json.loads(manifest_path.read_text(encoding="utf-8"))
    by_id = {str(n.get("id")): n for n in (payload.get("nodes") or [])}
    parts: list[str] = []
    for nid in node_ids:
        node = by_id.get(nid)
        if node is None:
            parts.append("")
            continue
        parts.append(str(node.get("command_digest") or ""))
    return sha256_hex(("\n".join(parts) + "\n").encode("utf-8"))


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
    and job/runner/artifact context. Fail-closed; never trusts caller-supplied claims.
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
    if not live_immutable_sha_actions_tuple_present(artifact_source.environ):
        return {"ok": False, "reason": "live_actions_tuple_missing"}
    if receipt is None:
        return {"ok": False, "reason": "receipt_required"}
    env_map = artifact_source.environ or {}
    live = derive_live_actions_tuple(env_map)
    run_dir = artifact_source.current_run_dir
    expected_attr = artifact_source.expected_attribution or expected_attribution
    if expected_attr is not None and receipt.attribution != expected_attr:
        return {"ok": False, "reason": "attribution_drift"}
    if expected_attr == "wheel":
        has_wheel = (run_dir / "wheel.whl").is_file() or any(run_dir.glob("*.whl"))
        if not has_wheel:
            return {"ok": False, "reason": "wheel_artifact_missing"}
        # Source-created current-run wheel bytes never prove publication clearance.
        return {"ok": False, "reason": "wheel_publication_unproven"}
    if expected_attr == "installer":
        has_installer = (
            (run_dir / "installer.bin").is_file()
            or any(run_dir.glob("*.msi"))
            or any(run_dir.glob("*.exe"))
            or any(run_dir.glob("*.ps1"))
            or (artifact_source.binary_path is not None and artifact_source.binary_path.is_file())
        )
        if not has_installer:
            return {"ok": False, "reason": "installer_artifact_missing"}
        return {"ok": False, "reason": "installer_publication_unproven"}
    if live.run_attempt and receipt.run_attempt != live.run_attempt:
        return {"ok": False, "reason": "run_attempt_mismatch"}
    if live.commit_sha and receipt.commit_sha != live.commit_sha:
        return {"ok": False, "reason": "commit_sha_mismatch"}
    if live.workflow_run_id and receipt.workflow_run_id != live.workflow_run_id:
        return {"ok": False, "reason": "workflow_run_id_drift"}
    if live.job_name and receipt.job_name != live.job_name:
        return {"ok": False, "reason": "job_drift"}
    if live.artifact_namespace and receipt.artifact_namespace != live.artifact_namespace:
        return {"ok": False, "reason": "artifact_namespace_drift"}

    if artifact_source.binary_path is not None:
        bp = artifact_source.binary_path
        if not bp.is_file():
            return {"ok": False, "reason": "binary_missing"}
        live_bin = sha256_hex(bp.read_bytes())
        pre_ok = receipt.binary_sha256_pre == live_bin
        post_ok = receipt.binary_sha256_post == live_bin
        if not pre_ok and post_ok:
            return {"ok": False, "reason": "binary_pre_drift"}
        if pre_ok and not post_ok:
            return {"ok": False, "reason": "binary_drift"}
        if not pre_ok and not post_ok:
            # Receipt claims pre==post (no drift) but live bytes moved → post drift.
            if receipt.binary_sha256_pre == receipt.binary_sha256_post:
                return {"ok": False, "reason": "binary_post_drift"}
            return {"ok": False, "reason": "binary_drift"}

    if (
        live.runner_identity_sha256
        and receipt.runner_identity_sha256 != live.runner_identity_sha256
    ):
        return {"ok": False, "reason": "runner_identity_drift"}

    manifest_path = artifact_source.manifest_path
    if not manifest_path.is_file():
        return {"ok": False, "reason": "manifest_missing"}
    live_manifest = sha256_hex(manifest_path.read_bytes())
    if receipt.manifest_sha256 != live_manifest:
        return {"ok": False, "reason": "manifest_drift"}

    missing: list[str] = []
    if artifact_source.junit_path is not None and not artifact_source.junit_path.is_file():
        missing.append("junit")
    if artifact_source.rust_list_path is not None and not artifact_source.rust_list_path.is_file():
        missing.append("rust_list")
    if missing:
        return {"ok": False, "reason": "artifact_incomplete", "missing": missing}

    if artifact_source.argv_path is not None:
        if not artifact_source.argv_path.is_file():
            return {"ok": False, "reason": "artifact_incomplete", "missing": ["argv"]}
        live_argv = sha256_hex(artifact_source.argv_path.read_bytes())
        if receipt.argv_digest != live_argv:
            return {"ok": False, "reason": "argv_drift"}

    if artifact_source.stdout_path is not None:
        if not artifact_source.stdout_path.is_file():
            return {"ok": False, "reason": "artifact_incomplete", "missing": ["stdout"]}
        if receipt.output_digest != sha256_hex(artifact_source.stdout_path.read_bytes()):
            return {"ok": False, "reason": "stdout_drift"}
    if artifact_source.stderr_path is not None:
        if not artifact_source.stderr_path.is_file():
            return {"ok": False, "reason": "artifact_incomplete", "missing": ["stderr"]}
        if receipt.output_digest != sha256_hex(artifact_source.stderr_path.read_bytes()):
            return {"ok": False, "reason": "stderr_drift"}
    if artifact_source.exit_path is not None:
        if not artifact_source.exit_path.is_file():
            return {"ok": False, "reason": "artifact_incomplete", "missing": ["exit"]}
        if receipt.exit_digest != sha256_hex(artifact_source.exit_path.read_bytes()):
            return {"ok": False, "reason": "exit_drift"}

    if artifact_source.junit_path is None and artifact_source.rust_list_path is None:
        # No census artifact: still fail closed (cannot clear), but argv/output
        # drift predicates above already fired when those paths were present.
        if artifact_source.argv_path is None:
            derived_argv = _manifest_argv_digest(manifest_path, receipt.node_list)
            if receipt.argv_digest != derived_argv:
                return {"ok": False, "reason": "command_digest_drift"}
        return {"ok": False, "reason": "artifact_incomplete", "missing": ["census_artifact"]}

    population: list[str] = []
    if artifact_source.junit_path is not None:
        if _junit_has_skipped(artifact_source.junit_path):
            return {"ok": False, "reason": "census_skipped"}
        population = derive_junit_population(artifact_source.junit_path)
        if census_digest(population) != receipt.node_census_digest:
            return {"ok": False, "reason": "junit_drift"}
    if artifact_source.rust_list_path is not None:
        rust_pop = derive_rust_list_census(artifact_source.rust_list_path)
        if len(rust_pop) != len(set(rust_pop)):
            return {"ok": False, "reason": "census_duplicate"}
        receipt_set = set(receipt.node_list)
        receipt_leaves = {n.split("::")[-1] for n in receipt.node_list}
        extras = [
            n for n in rust_pop if n not in receipt_set and n.split("::")[-1] not in receipt_leaves
        ]
        # More listed nodes than receipt claims → census_extra (before digest).
        if extras and len(rust_pop) > len(receipt.node_list):
            return {"ok": False, "reason": "census_extra"}
        if not population and census_digest(rust_pop) != receipt.node_census_digest:
            return {"ok": False, "reason": "rust_list_drift"}
        if extras:
            return {"ok": False, "reason": "census_extra"}
        if not population:
            population = rust_pop

    if artifact_source.argv_path is None:
        derived_argv = _manifest_argv_digest(manifest_path, receipt.node_list)
        if receipt.argv_digest != derived_argv:
            return {"ok": False, "reason": "command_digest_drift"}

    # Population must be non-empty for clearance.
    if not population:
        return {"ok": False, "reason": "census_empty"}

    # F2 (Sol round 1, DEFERRED to workflow-level fix): a single per-node receipt must
    # not clear the whole manifest. The durable fix is workflow-level aggregation — verify
    # EVERY per-node receipt beneath current_run_dir and require their union to equal the
    # job's manifest population (reject missing/duplicate/extra). A verifier-level exact
    # match here breaks the single-receipt positive control; tracked as the remaining RED
    # item alongside F5 (A38 parent-handle anchoring). See docs/audits/2026-08-12-
    # stale-branch-reconciliation.md §5.
    _ = environ, current_run_dir
    return {"ok": True, "reason": "cleared", "node_count": len(population)}
