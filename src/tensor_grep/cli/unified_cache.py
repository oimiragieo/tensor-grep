"""CodeNib-style unified cache manifest with Merkle root and HMAC-SHA256 provenance.

Tracks generation Merkle root (from sorted relative paths + mtime_ns + size),
source-address contract, and cross-view freshness across ``symbols_v1``,
``graph_v1``, ``hybrid_v1``, and ``index_v1`` under ``.tg_cache/manifest_v1.json``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tensor_grep.cli.symbols_cache_auth import load_machine_key
from tensor_grep.cli.symbols_cache_io import (
    cache_lock,
    prepare_directory,
    publish_confined,
    read_confined,
)
from tensor_grep.io.confined import validate_confined_parent_prefix

MANIFEST_REL_PATH = Path(".tg_cache") / "manifest_v1.json"
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
SOURCE_ADDRESS_SCHEME = "relpath-mtime-size-v1"
CACHE_VIEWS: tuple[str, ...] = ("symbols_v1", "graph_v1", "hybrid_v1", "index_v1")
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

_VIEW_DEFAULT_PATHS: dict[str, str] = {
    "symbols_v1": ".tg_cache/symbols_v1/metadata.sqlite3",
    "graph_v1": ".tg_cache/graph_v1/incremental_edges.sqlite3",
    "hybrid_v1": ".tg_cache/hybrid_v1/index.sqlite3",
    "index_v1": ".tg_cache/index_v1/trigram.idx",
}


@dataclass(frozen=True)
class ViewFreshnessRecord:
    """Freshness and provenance state for a single `.tg_cache` view."""

    name: str
    merkle_root: str
    artifact_path: str
    schema_version: int = 1
    updated_at: float = 0.0
    ready: bool = True
    artifact_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "merkle_root": self.merkle_root,
            "artifact_path": self.artifact_path,
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "ready": self.ready,
        }
        if self.artifact_fingerprint is not None:
            payload["artifact_fingerprint"] = self.artifact_fingerprint
        return payload


@dataclass(frozen=True)
class UnifiedCacheManifest:
    """Typed projection of ``.tg_cache/manifest_v1.json``."""

    schema_version: int
    merkle_root: str
    source_address: dict[str, Any]
    views: dict[str, dict[str, Any]] = field(default_factory=dict)


def _sign_manifest(secret: bytes, payload_json: str) -> str:
    return hmac.new(
        secret,
        b"tg-unified-manifest-v1\0" + payload_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _lstat_regular(path: Path) -> tuple[int, int] | None:
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400):
        return None
    return int(st.st_mtime_ns), int(st.st_size)


def collect_source_address_entries(
    repo_root: Path,
    files: Iterable[str | Path],
) -> list[tuple[str, int, int]]:
    """Collect sorted ``(rel_posix_path, mtime_ns, size)`` entries for regular files."""
    resolved_root = Path(repo_root).resolve()
    entries_by_rel: dict[str, tuple[int, int]] = {}
    for raw in files:
        fp = Path(raw)
        abs_fp = fp if fp.is_absolute() else resolved_root / fp
        try:
            rel_posix = abs_fp.relative_to(resolved_root).as_posix()
        except ValueError:
            try:
                rel_posix = abs_fp.resolve(strict=False).relative_to(resolved_root).as_posix()
            except ValueError:
                continue
        if not rel_posix or rel_posix.startswith(".."):
            continue
        stat_pair = _lstat_regular(abs_fp)
        if stat_pair is None:
            continue
        entries_by_rel[rel_posix] = stat_pair
    return [(rel, mtime_ns, size) for rel, (mtime_ns, size) in sorted(entries_by_rel.items())]


def compute_generation_merkle_root(
    repo_root: Path,
    files: Iterable[str | Path],
) -> str:
    """Compute a deterministic SHA-256 Merkle root from sorted relpaths, mtime_ns, and size."""
    entries = collect_source_address_entries(repo_root, files)
    if not entries:
        return hashlib.sha256(b"tg-merkle-empty-v1\0").hexdigest()
    level: list[bytes] = [
        hashlib.sha256(f"leaf\0{rel}\0{mtime_ns}\0{size}".encode()).digest()
        for rel, mtime_ns, size in entries
    ]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level: list[bytes] = []
        for idx in range(0, len(level), 2):
            next_level.append(hashlib.sha256(b"node\0" + level[idx] + level[idx + 1]).digest())
        level = next_level
    return level[0].hex()


def build_source_address_contract(
    repo_root: Path,
    files: Iterable[str | Path],
) -> tuple[str, dict[str, Any]]:
    """Build generation Merkle root and source-address contract dictionary."""
    resolved_root = Path(repo_root).resolve()
    entries = collect_source_address_entries(resolved_root, files)
    if not entries:
        merkle_root = hashlib.sha256(b"tg-merkle-empty-v1\0").hexdigest()
    else:
        level: list[bytes] = [
            hashlib.sha256(f"leaf\0{rel}\0{mtime_ns}\0{size}".encode()).digest()
            for rel, mtime_ns, size in entries
        ]
        while len(level) > 1:
            if len(level) % 2 == 1:
                level.append(level[-1])
            level = [
                hashlib.sha256(b"node\0" + level[i] + level[i + 1]).digest()
                for i in range(0, len(level), 2)
            ]
        merkle_root = level[0].hex()
    contract = {
        "scheme": SOURCE_ADDRESS_SCHEME,
        "root": str(resolved_root),
        "file_count": len(entries),
        "merkle_root": merkle_root,
    }
    return merkle_root, contract


def _stat_confined_regular_artifact(repo_root: Path, path: Path) -> tuple[int, int, int] | None:
    try:
        validate_confined_parent_prefix(repo_root, path)
        st = path.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISREG(st.st_mode)
        or (getattr(st, "st_file_attributes", 0) & _REPARSE_ATTR)
        or st.st_nlink != 1
    ):
        return None
    return int(st.st_size), int(st.st_mtime_ns), int(st.st_ino)


def compute_view_artifact_fingerprint(repo_root: Path, view_name: str) -> str | None:
    """Return a confined regular-file fingerprint for ``hybrid_v1`` or ``index_v1``, else None."""
    resolved_root = Path(repo_root).resolve()
    if view_name == "hybrid_v1":
        fp = _stat_confined_regular_artifact(
            resolved_root, resolved_root / ".tg_cache" / "hybrid_v1" / "index.sqlite3"
        )
        if fp is None:
            return None
        return hashlib.sha256(f"{fp[0]}:{fp[1]}:{fp[2]}".encode()).hexdigest()
    if view_name == "index_v1":
        index_dir = resolved_root / ".tg_cache" / "index_v1"
        try:
            validate_confined_parent_prefix(resolved_root, index_dir)
            dir_st = index_dir.lstat()
        except OSError:
            return None
        if not stat.S_ISDIR(dir_st.st_mode) or (
            getattr(dir_st, "st_file_attributes", 0) & _REPARSE_ATTR
        ):
            return None
        candidates = sorted(index_dir.glob("*.tg_index"))
        if not candidates:
            return None
        digest = hashlib.sha256()
        for candidate in candidates:
            fp = _stat_confined_regular_artifact(resolved_root, candidate)
            if fp is None:
                return None
            digest.update(f"{candidate.name}:{fp[0]}:{fp[1]}:{fp[2]}\0".encode())
        return digest.hexdigest()
    return None


def load_unified_manifest(
    repo_root: Path,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any] | None:
    """Load and HMAC-verify ``.tg_cache/manifest_v1.json``; fail closed on tamper/unsigned."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return None
    resolved_root = Path(repo_root).resolve()
    signing_key = load_machine_key(resolved_root)
    if signing_key is None:
        return None
    manifest_path = resolved_root / MANIFEST_REL_PATH
    try:
        validate_confined_parent_prefix(resolved_root, manifest_path)
        initial = manifest_path.lstat()
        if (
            not stat.S_ISREG(initial.st_mode)
            or (getattr(initial, "st_file_attributes", 0) & _REPARSE_ATTR)
            or initial.st_nlink != 1
            or initial.st_size > MANIFEST_MAX_BYTES
        ):
            return None
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(manifest_path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > MANIFEST_MAX_BYTES
                or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
            ):
                return None
            raw_bytes = stream.read(MANIFEST_MAX_BYTES + 1)
            if len(raw_bytes) > MANIFEST_MAX_BYTES:
                return None
            after = os.fstat(stream.fileno())
        current = manifest_path.lstat()
        validate_confined_parent_prefix(resolved_root, manifest_path)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(current.st_mode)
            or (getattr(current, "st_file_attributes", 0) & _REPARSE_ATTR)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            return None
        envelope = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(envelope, dict):
            return None
        payload_json = envelope.get("payload")
        mac = envelope.get("mac")
        if not isinstance(payload_json, str) or not isinstance(mac, str):
            return None
        if not hmac.compare_digest(_sign_manifest(signing_key, payload_json), mac):
            return None
        data = json.loads(payload_json)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or not isinstance(data.get("merkle_root"), str)
        or not isinstance(data.get("source_address"), dict)
        or not isinstance(data.get("views"), dict)
    ):
        return None
    if data["source_address"].get("root") != str(resolved_root):
        return None
    return data


def check_view_freshness(
    manifest: Mapping[str, Any] | None,
    current_merkle_root: str,
    views: Iterable[str] = CACHE_VIEWS,
    *,
    repo_root: Path | None = None,
) -> dict[str, bool]:
    """Return per-view freshness booleans against ``current_merkle_root``."""
    requested = tuple(views)
    if not isinstance(manifest, Mapping) or manifest.get("merkle_root") != current_merkle_root:
        return dict.fromkeys(requested, False)
    view_map = manifest.get("views")
    if not isinstance(view_map, Mapping):
        return dict.fromkeys(requested, False)
    resolved_root: Path | None = None
    if repo_root is not None:
        resolved_root = Path(repo_root).resolve()
    else:
        source_addr = manifest.get("source_address")
        raw_root = source_addr.get("root") if isinstance(source_addr, Mapping) else None
        if isinstance(raw_root, str) and raw_root:
            resolved_root = Path(raw_root).resolve()
    result: dict[str, bool] = {}
    for view_name in requested:
        entry = view_map.get(view_name)
        is_fresh = (
            isinstance(entry, Mapping)
            and entry.get("merkle_root") == current_merkle_root
            and entry.get("ready") is True
        )
        if is_fresh and view_name in ("hybrid_v1", "index_v1"):
            if resolved_root is None:
                is_fresh = False
            else:
                fp = compute_view_artifact_fingerprint(resolved_root, view_name)
                recorded_fp = (
                    entry.get("artifact_fingerprint") if isinstance(entry, Mapping) else None
                )
                if fp is None or not isinstance(recorded_fp, str) or recorded_fp != fp:
                    is_fresh = False
        result[view_name] = is_fresh
    return result


def is_manifest_fresh(
    manifest: Mapping[str, Any] | None,
    current_merkle_root: str,
    required_views: Iterable[str] = (),
) -> bool:
    """Return True iff the manifest and all ``required_views`` match ``current_merkle_root``."""
    if not isinstance(manifest, Mapping) or manifest.get("merkle_root") != current_merkle_root:
        return False
    required = tuple(required_views)
    if not required:
        return True
    freshness = check_view_freshness(manifest, current_merkle_root, required)
    return all(freshness.values())


def save_unified_manifest(
    repo_root: Path,
    merkle_root: str,
    *,
    source_address: Mapping[str, Any] | None = None,
    views: Mapping[str, Mapping[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
) -> bool:
    """Sign and atomically publish ``.tg_cache/manifest_v1.json`` via confined I/O."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return False
    resolved_root = Path(repo_root).resolve()
    signing_key = load_machine_key(resolved_root)
    if signing_key is None:
        return False
    manifest_path = resolved_root / MANIFEST_REL_PATH
    try:
        prepare_directory(resolved_root, manifest_path.parent)
        gitignore_path = manifest_path.parent / ".gitignore"
        try:
            publish_confined(resolved_root, gitignore_path, b"*\n", only_if_missing=True)
        except FileExistsError:
            read_confined(resolved_root, gitignore_path, 65536)

        with cache_lock(resolved_root, manifest_path, deadline_monotonic):
            existing = load_unified_manifest(resolved_root, deadline_monotonic=deadline_monotonic)
            merged_views: dict[str, dict[str, Any]] = {}
            if existing is not None and existing.get("merkle_root") == merkle_root:
                for k, v in existing.get("views", {}).items():
                    if isinstance(v, Mapping):
                        merged_views[str(k)] = dict(v)
            if views is not None:
                for view_name, view_data in views.items():
                    merged_views[str(view_name)] = dict(view_data)
            for view_name, entry in merged_views.items():
                if view_name in ("hybrid_v1", "index_v1"):
                    fp = compute_view_artifact_fingerprint(resolved_root, view_name)
                    recorded_fp = entry.get("artifact_fingerprint")
                    if fp is None or not isinstance(recorded_fp, str) or recorded_fp != fp:
                        entry["ready"] = False

            resolved_source_address = (
                dict(source_address)
                if source_address is not None
                else {
                    "scheme": SOURCE_ADDRESS_SCHEME,
                    "root": str(resolved_root),
                    "merkle_root": merkle_root,
                }
            )
            payload_obj = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "merkle_root": merkle_root,
                "source_address": resolved_source_address,
                "views": merged_views,
            }
            payload_json = json.dumps(
                payload_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            envelope = json.dumps(
                {"payload": payload_json, "mac": _sign_manifest(signing_key, payload_json)},
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            if len(envelope) > MANIFEST_MAX_BYTES:
                return False
            publish_confined(resolved_root, manifest_path, envelope)
        return True
    except (OSError, ValueError):
        return False


def update_view_states(
    repo_root: Path,
    merkle_root: str,
    view_updates: Mapping[str, tuple[bool, int]],
    *,
    source_address: Mapping[str, Any] | None = None,
    deadline_monotonic: float | None = None,
    artifact_fingerprints: Mapping[str, str | None] | None = None,
) -> bool:
    """Update one or more view freshness entries in ``manifest_v1.json``."""
    now = time.time()
    views_payload: dict[str, dict[str, Any]] = {}
    for view_name, (ready, schema_ver) in view_updates.items():
        views_payload[view_name] = ViewFreshnessRecord(
            name=view_name,
            merkle_root=merkle_root,
            artifact_path=_VIEW_DEFAULT_PATHS.get(view_name, f".tg_cache/{view_name}"),
            schema_version=schema_ver,
            updated_at=now,
            ready=ready,
            artifact_fingerprint=(
                artifact_fingerprints.get(view_name) if artifact_fingerprints is not None else None
            ),
        ).to_dict()
    return save_unified_manifest(
        repo_root,
        merkle_root,
        source_address=source_address,
        views=views_payload,
        deadline_monotonic=deadline_monotonic,
    )
