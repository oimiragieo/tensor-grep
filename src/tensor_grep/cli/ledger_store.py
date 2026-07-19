"""``tg ledger`` Slice 1 -- advisory, code-scoped agent-to-agent coordination claims.

Thesis (CEO-directed feature, `tg ledger` design doc verified against origin/main@7209fad):
concurrent coding agents working the same repo need a lightweight way to ADVERTISE intent on
a symbol/file without ever BLOCKING each other. A claim is advisory only -- it is never a
lock on an edit. ``submit_claim`` always returns normally on success (even when other live
claims overlap); the caller decides what to do with that information. A dead agent's claim
simply TTL-expires, so crash-semantics need no special handling.

On-disk layout mirrors ``session_store.py`` / ``checkpoint_store.py`` deliberately (same
q10 RMW-race fix, same audit-I2 retention-cap shape, same traversal-refusal contract):
``<root>/.tensor-grep/ledger/claims/index.json`` -- a single JSON array of claim records,
read-modify-written under :func:`tensor_grep.cli._index_lock.index_lock` (never a bare
load->mutate->write), expired-pruned on every WRITE path (``claim``/``release``), and capped
at :data:`_MAX_LIVE_CLAIMS` live records (oldest ``created_at`` evicted first) -- a DoS bound
distinct from TTL pruning, since a flood of claims with a long/default TTL would otherwise
grow the index without limit even though none of them are individually expired yet.
``list_claims`` is a pure read (mirrors ``session_store.list_sessions``): it prunes expired
entries for DISPLAY only and never writes, so listing claims cannot itself create
``.tensor-grep/ledger/`` (default-inert until the first ``claim``).

Backend Fail-Closed Contract (AGENTS.md): a lock-acquire timeout
(:class:`tensor_grep.cli._index_lock.IndexLockTimeoutError`), a symlink at the index
destination (``OSError`` from :func:`tensor_grep.cli._index_lock.atomic_write_json`), a
``--files`` entry that escapes the repo root (:class:`LedgerTraversalError`), an oversized
on-disk index (:class:`LedgerIndexTooLargeError`), or a corrupt index
(:class:`LedgerCorruptIndexError`) NEVER return a fake success -- every one of these is a
:class:`LedgerError` (or the sibling ``IndexLockTimeoutError``/``OSError``) that the CLI layer
maps to exit code 2 with nothing written. A live overlapping claim from another agent is NOT
one of these failures: it is reported in the normal return value of :func:`submit_claim`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from tensor_grep.cli._index_lock import atomic_write_json, index_lock
from tensor_grep.cli.evidence_receipt import _repo_revision_identity
from tensor_grep.cli.session_store import _resolve_root

LEDGER_SCHEMA_VERSION = 1

_TG_DIRNAME = ".tensor-grep"
_LEDGER_SUBDIR = "ledger"
_CLAIMS_SUBDIR = "claims"
_INDEX_FILE = "index.json"

_DEFAULT_TTL_SECONDS = 900
_TTL_ENV = "TG_LEDGER_CLAIM_TTL_SECONDS"

_AGENT_ID_ENV = "TG_LEDGER_AGENT_ID"
_FALLBACK_AGENT_ID_ENV = "TG_EVIDENCE_AGENT_ID"
_DEFAULT_AGENT_ID = "anonymous"

# DoS bound distinct from TTL pruning (see module docstring): mirrors audit I2's
# session-index retention cap (session_store._DEFAULT_SESSION_MAX) applied to claims.
_MAX_LIVE_CLAIMS = 256

# Pre-parse bounded read (mirrors evidence_signing._MAX_RECEIPT_FILE_BYTES's "pre-auth
# unbounded read" rationale, AGENTS.md Security Hardening Patterns): a claims index is a
# small, cardinality-bounded (<=256 live records) JSON array; 8 MiB is generous headroom for
# that shape while still refusing to parse an unbounded file.
_MAX_INDEX_FILE_BYTES = 8 * 1024 * 1024


class LedgerError(RuntimeError):
    """Base for every ``tg ledger`` fail-closed condition (Backend Fail-Closed Contract):
    ``submit_claim``/``release_claim``/``list_claims`` never return a fake success when one
    of these fires -- the CLI layer maps every ``LedgerError`` (plus the sibling
    ``_index_lock.IndexLockTimeoutError`` and a write-path ``OSError``, e.g. a symlink
    refusal) to exit code 2 and writes nothing."""


class LedgerUsageError(LedgerError):
    """Caller-supplied arguments cannot form a valid claim/release request (e.g. neither
    ``--symbol`` nor ``--files`` was given)."""


class LedgerTraversalError(LedgerError):
    """A ``--files`` entry resolved outside the repo root -- refused before anything is
    written, mirroring ``session_store._session_payload_path``'s absolute/``..`` refusal."""


class LedgerIndexTooLargeError(LedgerError):
    """The on-disk claims index exceeds :data:`_MAX_INDEX_FILE_BYTES` -- refuses to parse an
    unbounded file rather than reading it fully into memory first."""


class LedgerCorruptIndexError(LedgerError):
    """The on-disk claims index is not a valid JSON array. Fails closed rather than silently
    treating unreadable data as "no claims" -- suppression must never read as absence."""


@dataclass
class ClaimRecord:
    ledger_schema_version: int
    kind: str
    claim_id: str
    agent_id: str
    symbols: list[str]
    files: list[str]
    intent: str
    note: str | None
    created_at: str
    expires_at: str
    ttl_seconds: int
    revision: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# small local helpers (kept local rather than imported cross-module -- mirrors
# evidence_receipt.py's own stated rationale: each is a few lines and the cli/*.py modules
# deliberately do not share tiny helpers across siblings)
# ---------------------------------------------------------------------------


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _configured_ttl_seconds(explicit: int | None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    return _configured_positive_int(_TTL_ENV, _DEFAULT_TTL_SECONDS)


def resolve_agent_id(explicit: str | None) -> str:
    """``--agent-id`` else ``TG_LEDGER_AGENT_ID`` else ``TG_EVIDENCE_AGENT_ID`` else a stable
    fallback. Recorded verbatim, never inferred from process/user identity -- callers must not
    put secrets in ``--agent-id``/``--note`` (the value is written to a plaintext, per-repo,
    multi-agent-readable JSON file)."""
    if explicit is not None:
        stripped = explicit.strip()
        if stripped:
            return stripped
    for env_var in (_AGENT_ID_ENV, _FALLBACK_AGENT_ID_ENV):
        env_value = os.environ.get(env_var)
        if env_value is not None:
            stripped = env_value.strip()
            if stripped:
                return stripped
    return _DEFAULT_AGENT_ID


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _new_claim_id() -> str:
    # Mirrors session_store._new_session_id's shape exactly: session-<utc-compact>-<root>-
    # <hex8> -> claim-<utc-compact>-<hex8> (no root segment: a claim's root is already
    # implicit in which index.json it lives in, unlike a session id which must stay unique
    # and human-legible across a shared session cache keyed only by id).
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"claim-{timestamp}-{uuid4().hex[:8]}"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_relative_file(root: Path, raw: str) -> str:
    """Root-relative path containment check for a caller-supplied ``--files`` entry. Mirrors
    ``session_store._session_payload_path``'s absolute/``..`` traversal refusal. Never touches
    the filesystem beyond ``.resolve()`` (lexical + existing-segment symlink resolution) -- a
    claim may legitimately name a file that does not exist yet (a planned new file)."""
    candidate = Path(raw)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise LedgerTraversalError(f"Refusing claim --files entry outside repo root: {raw!r}")
    root_resolved = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise LedgerTraversalError(f"Refusing claim --files entry outside repo root: {raw!r}")
    return candidate.as_posix()


# ---------------------------------------------------------------------------
# on-disk paths
# ---------------------------------------------------------------------------


def _ledger_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _LEDGER_SUBDIR / _CLAIMS_SUBDIR


def _index_path(root: Path) -> Path:
    return _ledger_dir(root) / _INDEX_FILE


# ---------------------------------------------------------------------------
# index read / write
# ---------------------------------------------------------------------------


def _read_index_bytes(index_path: Path) -> bytes:
    try:
        with open(index_path, "rb") as handle:
            raw = handle.read(_MAX_INDEX_FILE_BYTES + 1)
    except FileNotFoundError:
        return b""
    if len(raw) > _MAX_INDEX_FILE_BYTES:
        raise LedgerIndexTooLargeError(
            f"Claims index at {index_path} exceeds the {_MAX_INDEX_FILE_BYTES}-byte bound; "
            "refusing to parse an unbounded file."
        )
    return raw


def _load_index(root: Path) -> list[dict[str, Any]]:
    index_path = _index_path(root)
    raw = _read_index_bytes(index_path)
    if not raw:
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LedgerCorruptIndexError(
            f"Claims index at {index_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise LedgerCorruptIndexError(f"Claims index at {index_path} is not a JSON array")
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_index(root: Path, records: list[dict[str, Any]]) -> None:
    # atomic_write_json (and, transitively, index_lock's own lock_path.parent.mkdir) is the
    # ONLY thing that creates .tensor-grep/ledger/claims/ -- reads never do, which is what
    # keeps a repo default-inert until the first `tg ledger claim`.
    atomic_write_json(_index_path(root), records)


def _prune_expired(records: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """Keep only records whose ``expires_at`` parses AND is still in the future. A record
    with a missing/unparseable ``expires_at`` (corrupt write, foreign edit, pre-schema
    record) is dropped rather than kept forever -- consistent with the "a dead agent's claim
    just TTL-expires" design thesis: an unparseable expiry is treated the same as an already
    -expired one, not as an immortal one."""
    live: list[dict[str, Any]] = []
    for entry in records:
        expires_at = _parse_iso(entry.get("expires_at"))
        if expires_at is None:
            continue
        if expires_at > now:
            live.append(entry)
    return live


def _evict_oldest_over_cap(
    records: list[dict[str, Any]], *, max_records: int | None = None
) -> list[dict[str, Any]]:
    # `max_records` is resolved from the module constant INSIDE the body (not as the
    # parameter's default value) so a test can `monkeypatch.setattr(ledger_store,
    # "_MAX_LIVE_CLAIMS", N)` and have it take effect -- a default-argument value would be
    # bound once at function-definition time and never see a post-import monkeypatch.
    effective_max = _MAX_LIVE_CLAIMS if max_records is None else max_records
    if len(records) <= effective_max:
        return records
    ordered = sorted(records, key=lambda entry: str(entry.get("created_at", "")))
    return ordered[-effective_max:]


def _revision_matches(other: Any, mine: dict[str, Any]) -> bool | None:
    """``True``/``False`` only when BOTH sides have a resolved git identity; ``None`` (never
    guessed) when either side's ``_repo_revision_identity`` came back ``unavailable`` -- an
    honest "unknown" instead of a fabricated match/mismatch, mirroring how the rest of this
    codebase treats an unavailable evidence block."""
    if not isinstance(other, dict) or not isinstance(mine, dict):
        return None
    if other.get("status") != "present" or mine.get("status") != "present":
        return None
    return other.get("commit_sha") == mine.get("commit_sha")


def _find_overlaps(
    live_records: list[dict[str, Any]], new_record: ClaimRecord
) -> list[dict[str, Any]]:
    """Live claims from OTHER agent_ids whose symbols/files literally intersect the new
    claim. Never includes the caller's own other claims (self-overlap is not interesting --
    an agent claiming two overlapping things is not a coordination conflict)."""
    new_symbols = set(new_record.symbols)
    new_files = set(new_record.files)
    overlaps: list[dict[str, Any]] = []
    for entry in live_records:
        if entry.get("agent_id") == new_record.agent_id:
            continue
        entry_symbols = set(entry.get("symbols") or [])
        entry_files = set(entry.get("files") or [])
        symbol_overlap = sorted(new_symbols & entry_symbols)
        file_overlap = sorted(new_files & entry_files)
        if not symbol_overlap and not file_overlap:
            continue
        overlaps.append({
            "claim_id": entry.get("claim_id"),
            "agent_id": entry.get("agent_id"),
            "symbols": symbol_overlap,
            "files": file_overlap,
            "intent": entry.get("intent"),
            "expires_at": entry.get("expires_at"),
            "revision_matches": _revision_matches(entry.get("revision"), new_record.revision),
        })
    return overlaps


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def submit_claim(
    path: str,
    *,
    symbols: list[str] | None = None,
    files: list[str] | None = None,
    intent: str = "edit",
    note: str | None = None,
    ttl_seconds: int | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Record a new advisory claim and report live overlaps from OTHER agents.

    NEVER blocks: this function always returns normally on success, including when other
    agents hold live overlapping claims -- those are reported in the returned ``overlaps``
    list for the caller to act on, not raised as an error. It raises ONLY on a fail-closed
    condition (:class:`LedgerError` subclass, or the sibling ``IndexLockTimeoutError`` /
    write-path ``OSError``), in which case nothing is written.
    """
    root = _resolve_root(Path(path))
    resolved_symbols = _dedupe_preserve_order(
        symbol.strip() for symbol in (symbols or []) if symbol and symbol.strip()
    )
    resolved_files = _dedupe_preserve_order(
        _normalize_relative_file(root, raw) for raw in (files or []) if raw and raw.strip()
    )
    if not resolved_symbols and not resolved_files:
        raise LedgerUsageError("tg ledger claim requires at least one --symbol or a --files entry")

    resolved_agent_id = resolve_agent_id(agent_id)
    resolved_ttl = _configured_ttl_seconds(ttl_seconds)
    resolved_intent = (intent or "edit").strip() or "edit"
    resolved_note = note.strip() if note and note.strip() else None
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=resolved_ttl)

    record = ClaimRecord(
        ledger_schema_version=LEDGER_SCHEMA_VERSION,
        kind="claim",
        claim_id=_new_claim_id(),
        agent_id=resolved_agent_id,
        symbols=resolved_symbols,
        files=resolved_files,
        intent=resolved_intent,
        note=resolved_note,
        created_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
        ttl_seconds=resolved_ttl,
        revision=_repo_revision_identity(root),
    )

    index_path = _index_path(root)
    with index_lock(index_path):
        existing = _load_index(root)
        live = _prune_expired(existing, now=now)
        overlaps = _find_overlaps(live, record)
        live.append(record.to_dict())
        live = _evict_oldest_over_cap(live)
        _write_index(root, live)

    return {"claim": record.to_dict(), "overlaps": overlaps}


def release_claim(
    path: str,
    *,
    claim_id: str | None = None,
    symbol: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Release a claim by exact ``claim_id`` (any agent may release a claim it knows the
    opaque id for -- the id itself is the authorization, e.g. an orchestrator garbage
    -collecting a crashed sub-agent's claim) or by ``symbol`` (scoped to the resolved
    ``agent_id``'s OWN live claims only, so a common/guessable symbol name can never release
    another agent's claim by accident).

    Releasing zero matching claims (already released, already expired, or never existed) is
    NOT an error -- always returns normally with ``released == []``.
    """
    if not claim_id and not symbol:
        raise LedgerUsageError("tg ledger release requires --claim-id or --symbol")

    root = _resolve_root(Path(path))
    index_path = _index_path(root)
    if not index_path.exists():
        # Default-inert fast path: `index_lock` would itself create
        # `.tensor-grep/ledger/claims/` just by acquiring the lock file (its own
        # `lock_path.parent.mkdir`), even though there is provably nothing to release when
        # no index has ever been written. Short-circuit BEFORE the lock so `tg ledger
        # release` on a repo with no claims never creates ledger state -- mirrors `claim`
        # legitimately creating it once (there IS something to write) without release ever
        # doing so for nothing.
        return {"released": [], "released_count": 0}

    resolved_agent_id = resolve_agent_id(agent_id)
    now = datetime.now(UTC)
    with index_lock(index_path):
        existing = _load_index(root)
        live = _prune_expired(existing, now=now)
        remaining: list[dict[str, Any]] = []
        released: list[dict[str, Any]] = []
        for entry in live:
            matches_id = claim_id is not None and entry.get("claim_id") == claim_id
            matches_symbol = (
                symbol is not None
                and entry.get("agent_id") == resolved_agent_id
                and symbol in (entry.get("symbols") or [])
            )
            if matches_id or matches_symbol:
                released.append(entry)
            else:
                remaining.append(entry)
        if len(remaining) != len(existing):
            _write_index(root, remaining)

    return {"released": released, "released_count": len(released)}


def list_claims(
    path: str,
    *,
    symbol: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Pure read: live (non-expired) claims for the current root, optionally filtered by
    ``symbol`` and/or ``agent_id``. Never acquires the write lock and never writes -- expired
    entries are pruned for DISPLAY only, matching ``session_store.list_sessions``'s read-only
    shape (physical cleanup happens lazily on the next ``claim``/``release`` write)."""
    root = _resolve_root(Path(path))
    now = datetime.now(UTC)
    live = _prune_expired(_load_index(root), now=now)
    filtered = live
    if symbol is not None:
        filtered = [entry for entry in filtered if symbol in (entry.get("symbols") or [])]
    if agent_id is not None:
        filtered = [entry for entry in filtered if entry.get("agent_id") == agent_id]
    return {"claims": filtered, "count": len(filtered)}
