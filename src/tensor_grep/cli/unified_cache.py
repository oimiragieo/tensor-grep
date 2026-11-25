"""CodeNib-style unified cache manifest with Merkle root and HMAC-SHA256 provenance.

Tracks generation Merkle root (from sorted relative paths + mtime_ns + size),
source-address contract, and cross-view freshness across ``symbols_v1``,
``graph_v1``, ``hybrid_v1``, and ``index_v1`` under ``.tg_cache/manifest_v1.json``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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

MANIFEST_REL_PATH = Path(".tg_cache") / "manifest_v1.json"
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_MAX_BYTES = 16 * 1024 * 1024
SOURCE_ADDRESS_SCHEME = "relpath-mtime-size-v1"
CACHE_VIEWS: tuple[str, ...] = ("symbols_v1", "graph_v1", "hybrid_v1", "index_v1")

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "merkle_root": self.merkle_root,
            "artifact_path": self.artifact_path,
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "ready": self.ready,
        }


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
    if not manifest_path.exists():
        return None
    try:
        raw_bytes = read_confined(resolved_root, manifest_path, MANIFEST_MAX_BYTES)
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
    except (OSError, ValueError, TypeError):
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
) -> dict[str, bool]:
    """Return per-view freshness booleans against ``current_merkle_root``."""
    requested = tuple(views)
    if not isinstance(manifest, Mapping) or manifest.get("merkle_root") != current_merkle_root:
        return dict.fromkeys(requested, False)
    view_map = manifest.get("views")
    if not isinstance(view_map, Mapping):
        return dict.fromkeys(requested, False)
    result: dict[str, bool] = {}
    for view_name in requested:
        entry = view_map.get(view_name)
        result[view_name] = (
            isinstance(entry, Mapping)
            and entry.get("merkle_root") == current_merkle_root
            and entry.get("ready") is True
        )
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
        ).to_dict()
    return save_unified_manifest(
        repo_root,
        merkle_root,
        source_address=source_address,
        views=views_payload,
        deadline_monotonic=deadline_monotonic,
    )
