"""``tg ledger`` -- advisory, code-scoped agent-to-agent coordination (Slice 1: claims; Slice 2:
findings).

Thesis (CEO-directed feature, `tg ledger` design doc verified against origin/main@7209fad):
concurrent coding agents working the same repo need a lightweight way to ADVERTISE intent on
a symbol/file without ever BLOCKING each other. A claim is advisory only -- it is never a
lock on an edit. ``submit_claim`` always returns normally on success (even when other live
claims overlap); the caller decides what to do with that information. A dead agent's claim
simply TTL-expires, so crash-semantics need no special handling.

On-disk layout mirrors ``session_store.py`` / ``checkpoint_store.py`` in SHAPE (same q10
RMW-race fix, same audit-I2 retention-cap shape, same traversal-refusal contract):
``<root>/.tensor-grep/ledger/claims/index.json`` -- a single JSON array of claim records,
read-modify-written under :func:`tensor_grep.cli._index_lock.index_lock` (never a bare
load->mutate->write), expired-pruned on every WRITE path (``claim``/``release``), and capped
at :data:`_MAX_LIVE_CLAIMS` live records (oldest ``created_at`` evicted first) -- a DoS bound
distinct from TTL pruning, since a flood of claims with a long/default TTL would otherwise
grow the index without limit even though none of them are individually expired yet.
``list_claims`` is a pure read (mirrors ``session_store.list_sessions``): it prunes expired
entries for DISPLAY only and never writes, so listing claims cannot itself create
``.tensor-grep/ledger/`` (default-inert until the first ``claim``).

PATH scoping (claims subtree only -- fix for the CEO v1.92.1 dogfood #1 "PATH-scope footgun"):
unlike every sibling store, ``root`` for ``submit_claim``/``release_claim``/``list_claims`` is
NOT simply ``session_store._resolve_root(path)`` (the literal, caller-supplied directory) --
that was the bug. ``claim core/hooks`` and ``list .`` each independently resolved to a
DIFFERENT physical directory (``core/hooks/.tensor-grep/...`` vs ``./.tensor-grep/...``), so a
claim filed from one subtree was invisible to a list/release call from another, even within the
SAME repository, with no error or signal -- "same-PATH-everywhere" was an undocumented sharp
edge. :func:`_ledger_physical_root` fixes this by canonicalizing to the nearest ``.git``
ancestor (:func:`_discover_repo_root`) -- git is the one unambiguous, universally-understood
repo-boundary signal, so every claim/list/release call for the SAME repository now reads and
writes the SAME physical index regardless of which subtree of it ``path`` names (falls back to
today's literal-path behavior, unchanged, when no ``.git`` is found -- a non-git working
directory is not regressed, just not unified). The caller's original ``path`` is preserved
separately as each claim's ``scope`` (root-relative, POSIX-normalized via
:func:`_normalize_scope`) rather than being (mis)used as the physical storage location.
``list_claims`` rolls scope UP: listing a broader/ancestor path shows every live claim scoped to
it or to any descendant subtree (:func:`_scope_contains`, segment-wise lexical containment --
never a raw string-prefix test, so ``"core/ho"`` cannot false-match ``"core/hoodie"``);
``release_claim`` keeps its EXACT ``--claim-id``/``--symbol`` matching semantics unchanged (this
fix does not add rollup matching to release -- a release call could otherwise silently drop an
unrelated sibling agent's claim just because it happens to share a symbol name and live under an
ancestor scope), but when nothing matches, it now names what IS live elsewhere
(``unmatched_reason``/``live_claims_elsewhere``) instead of a bare, indistinguishable
``released_count: 0``. Slice 2 (``record_finding``/``find_findings`` below) deliberately keeps
plain ``session_store._resolve_root`` -- untouched by this fix, per the same footgun it has not
(yet) been reported for.

Backend Fail-Closed Contract (AGENTS.md): a lock-acquire timeout
(:class:`tensor_grep.cli._index_lock.IndexLockTimeoutError`), a symlink at the index
destination (``OSError`` from :func:`tensor_grep.cli._index_lock.atomic_write_json`), a
``--files`` entry that escapes the repo root (:class:`LedgerTraversalError`), an oversized
on-disk index (:class:`LedgerIndexTooLargeError`), or a corrupt index
(:class:`LedgerCorruptIndexError`) NEVER return a fake success -- every one of these is a
:class:`LedgerError` (or the sibling ``IndexLockTimeoutError``/``OSError``) that the CLI layer
maps to exit code 2 with nothing written. A live overlapping claim from another agent is NOT
one of these failures: it is reported in the normal return value of :func:`submit_claim`.

Slice 2 (findings) extends this module with a SEPARATE subtree, ``<root>/.tensor-grep/ledger/
findings/`` (``index.json`` + a content-addressed ``blobs/<receipt_sha256>.json`` per distinct
artifact) -- the "saves on searches / uses contracts" pillar: one agent ``record``s an
evidence-receipt/blast-radius/context-pack/repo-map artifact it already computed; a sibling
agent ``find``s it by symbol and reuses the artifact instead of recomputing, PROVIDED it is
still ``fresh`` (its captured ``revision`` -- ``commit_sha`` AND ``dirty_tree_sha256`` -- matches
the CURRENT repo state; never silently served as current otherwise) and its blob's content
still hashes to the recorded ``receipt_sha256`` (:func:`tensor_grep.cli.evidence_signing.
receipt_digest`, the same content-address/integrity function ``tg evidence`` uses). A tampered
or unreadable blob raises :class:`LedgerIntegrityError` -- ``find_findings`` never silently
omits or silently serves unverified data. Both ``record`` and ``find`` capture/recompute the
repo revision with ``exclude_prefixes=(".tensor-grep/ledger",)`` (mirrors ``evidence_receipt.
py``'s own ``exclude_prefixes`` param, added for the identical ``tg codemap --check`` self
-dirty false-positive) -- otherwise the ledger's OWN on-disk writes would make every subsequent
``find`` see the repo as dirty against itself. A wall-clock TTL (default 24h) is a backstop
bound on top of the revision-primary freshness check, mirroring claims' TTL but far
longer-lived (a reusable artifact, not a short-lived coordination signal). Default-OFF in the
sense that matters: nothing in ``tg agent``/``edit-plan`` consults the ledger automatically in
this slice -- ``record``/``find`` are explicit-invoke only.
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, TypeGuard
from uuid import uuid4

from tensor_grep.cli._index_lock import atomic_write_json, index_lock
from tensor_grep.cli.evidence_receipt import _repo_revision_identity
from tensor_grep.cli.evidence_signing import receipt_digest, verify_receipt
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

# Release-honesty diagnostic bound (PATH-scope footgun fix): when a `release` call matches
# nothing, `live_claims_elsewhere` names OTHER live claims so the caller can self-diagnose a
# wrong `--claim-id`/`--symbol` -- bounded independently of `_MAX_LIVE_CLAIMS` (the store's own
# DoS cap) so a busy, near-the-cap ledger never balloons a single release response.
_MAX_LIVE_CLAIMS_ELSEWHERE_SHOWN = 10

# Pre-parse bounded read (mirrors evidence_signing._MAX_RECEIPT_FILE_BYTES's "pre-auth
# unbounded read" rationale, AGENTS.md Security Hardening Patterns): a claims index is a
# small, cardinality-bounded (<=256 live records) JSON array; 8 MiB is generous headroom for
# that shape while still refusing to parse an unbounded file.
_MAX_INDEX_FILE_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Slice 2 (findings) constants -- a SEPARATE index/subtree from claims above (own
# `findings/index.json`, own eviction cap, own TTL), sharing only the top-level
# `<root>/.tensor-grep/ledger/` parent and the generic LEDGER_SCHEMA_VERSION.
# ---------------------------------------------------------------------------

_FINDINGS_SUBDIR = "findings"
_BLOBS_SUBDIR = "blobs"

# The ledger's OWN on-disk state must never itself make a repo read as "dirty" for freshness
# purposes -- otherwise the very act of `record`ing (which writes findings/index.json + a blob
# under .tensor-grep/ledger/) would flip EVERY subsequent `find`'s freshness check to false
# even with zero real code changes, since git would see the ledger's own new files as
# untracked. Mirrors evidence_receipt.py's `exclude_prefixes` param (added for the identical
# `tg codemap --check` self-dirty false-positive).
_LEDGER_EXCLUDE_PREFIXES = (f"{_TG_DIRNAME}/{_LEDGER_SUBDIR}",)

# Wall-clock backstop, NOT the primary freshness signal (see module docstring): revision match
# is what actually decides `fresh`; this TTL only bounds how long a record can linger on disk
# if nobody ever calls `find` against it. Far longer than a claim's TTL (a reusable artifact,
# not a short-lived coordination signal).
_DEFAULT_FINDING_TTL_SECONDS = 86400  # 24h
_FINDING_TTL_ENV = "TG_LEDGER_FINDING_TTL_SECONDS"

# DoS bound distinct from TTL pruning -- same rationale as _MAX_LIVE_CLAIMS above, sized larger
# since findings are a shared reuse cache rather than a short-lived coordination signal.
_MAX_LIVE_FINDINGS = 512

# Total on-disk bytes across all DISTINCT (content-addressed, dedup'd) finding blobs for one
# root -- independent of _MAX_LIVE_FINDINGS, since a flood of small findings could stay under
# the count cap while still accumulating unbounded disk via a few large artifacts.
_DEFAULT_MAX_TOTAL_BLOB_BYTES = 256 * 1024 * 1024  # 256 MiB
_MAX_BLOB_BYTES_ENV = "TG_LEDGER_MAX_BLOB_BYTES"

# Pre-parse bounded read for a single caller-supplied --receipt artifact (record) or a single
# stored blob (find) -- mirrors _MAX_INDEX_FILE_BYTES's rationale, sized for one JSON artifact
# (an evidence receipt / blast-radius / repo-map payload) rather than a whole index array.
_MAX_ARTIFACT_FILE_BYTES = 8 * 1024 * 1024

_VALID_ARTIFACT_KINDS = frozenset({"blast-radius", "evidence-receipt", "context-pack", "repo-map"})


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


class LedgerArtifactError(LedgerError):
    """Slice 2 (findings): a `tg ledger record` `--receipt` artifact is missing, unreadable,
    oversized, or not a JSON object -- refused before anything is written (Backend Fail-Closed
    Contract), mirroring `LedgerTraversalError`'s "refuse before write" posture above."""


class LedgerIntegrityError(LedgerError):
    """Slice 2 (findings): a recorded finding's on-disk blob is missing, unreadable, not valid
    JSON, or its recomputed content digest no longer matches the finding's recorded
    `receipt_sha256` (tampered or corrupted since it was written). `find_findings` NEVER serves
    a finding it cannot verify -- this is raised instead of silently omitting or silently
    trusting it, the same "suppression must never read as absence" posture
    `LedgerCorruptIndexError` takes for the index itself, applied to a blob's content."""


@dataclass
class ClaimRecord:
    ledger_schema_version: int
    kind: str
    claim_id: str
    agent_id: str
    symbols: list[str]
    files: list[str]
    scope: str
    intent: str
    note: str | None
    created_at: str
    expires_at: str
    ttl_seconds: int
    revision: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FindingRecord:
    """Slice 2: a content-addressed POINTER to a previously-computed artifact (an evidence
    receipt, blast-radius, context-pack, or repo-map payload), not the artifact itself -- the
    artifact bytes live once in `findings/blobs/<receipt_sha256>.json`, dedup'd by content
    hash; this record is the small, cheap-to-scan index entry `find_findings` matches on."""

    ledger_schema_version: int
    kind: str
    finding_id: str
    agent_id: str
    artifact_kind: str
    symbol: str | None
    receipt_sha256: str
    blob_relpath: str
    signed: bool
    key_id: str | None
    revision: dict[str, Any]
    created_at: str
    expires_at: str
    ttl_seconds: int

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
# PATH-scope footgun fix (CEO v1.92.1 dogfood #1): physical-root canonicalization + the claim
# `scope` concept it enables. See the module docstring's "PATH scoping" paragraph for the full
# rationale. Claims-subtree (Slice 1) only -- record_finding/find_findings (Slice 2) below
# deliberately keep plain `_resolve_root`, untouched.
# ---------------------------------------------------------------------------


def _discover_repo_root(start: Path) -> Path:
    """Walk upward from ``start`` (an already-``_resolve_root``-resolved, existing directory)
    looking for the nearest ancestor -- including ``start`` itself -- that carries a ``.git``
    entry (a directory for a normal checkout, a FILE for a git worktree/submodule; ``.exists()``
    does not care which). ``.git`` is the one unambiguous, universally-understood repo-boundary
    signal (mirrors the ``.git``-detection half of ``repo_map._validation_repo_root``'s boundary
    logic, deliberately WITHOUT that function's npm/python/rust marker-file short-circuit -- a
    nested ``package.json``/``pyproject.toml`` inside a monorepo SUBTREE must not trap this walk
    early: ledger's coordination plane needs the OUTERMOST checkout boundary every agent shares,
    not the nearest runnable-package boundary ``_validation_repo_root`` is tuned for).

    Falls back to ``start`` UNCHANGED when no ``.git`` is found before the filesystem root (or
    the OS temp-dir boundary, mirroring ``_validation_repo_root``'s own stop condition so a
    pytest ``tmp_path`` fixture -- itself created under the system temp dir -- can never
    accidentally walk into an unrelated repo that happens to contain the temp dir) -- a non-git
    working directory keeps today's exact ``_resolve_root``-only behavior; every EXISTING test
    fixture (none of which ``git init`` by default) is therefore byte-for-byte unaffected.

    Pure filesystem ``.exists()`` stat calls only, no subprocess -- keeps ``list_claims``/
    ``release_claim`` on their pre-existing "no subprocess" cost profile. Bounded by filesystem
    depth (a handful of stat calls, never a directory walk/enumeration) -- no new DoS or
    symlink-disclosure surface, since this only tests existence of a fixed-name entry at each
    ancestor and never lists or reads directory contents."""
    try:
        temp_boundary = Path(tempfile.gettempdir()).resolve()
        current = start
        while True:
            if current == temp_boundary and start != temp_boundary:
                break
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
    except OSError:
        return start
    return start


def _ledger_physical_root(path: str) -> Path:
    """The canonical physical location for THIS repository's claims index: today's literal
    ``_resolve_root(path)`` resolution followed by the ``.git``-boundary walk-up above. Used by
    ``submit_claim``/``release_claim``/``list_claims`` ONLY -- Slice 2 keeps plain
    ``_resolve_root`` (see module docstring)."""
    return _discover_repo_root(_resolve_root(Path(path)))


def _normalize_scope(path: str, root: Path) -> str:
    """The caller's ``path`` argument, normalized to a ``root``-relative, POSIX-separated
    "scope" string -- ``"."`` for the repository root itself, ``"core/hooks"`` for a subtree.
    Pathlib normalizes Windows separators (native ``\\`` is accepted alongside ``/`` on that
    platform), a leading ``./``, and a trailing ``/`` for free once resolved through ``Path`` +
    ``.resolve()`` + ``.as_posix()`` -- no bespoke string handling needed for any of those traps.

    ``resolved`` (``path``'s own literal resolution) is always ``root`` itself or a descendant
    of it BY CONSTRUCTION: ``root`` is discovered by walking upward from exactly this same
    resolution (see ``_ledger_physical_root``), so it is always ``resolved`` or one of its
    ancestors -- the ``ValueError`` branch below is defensive only."""
    resolved = _resolve_root(Path(path))
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return "."
    return relative.as_posix()


def _entry_scope(entry: dict[str, Any]) -> str:
    """A claim record's scope, defaulting a MISSING/malformed field (an index entry written by
    a pre-fix ``tg`` binary, before ``scope`` existed) to ``"."`` -- the repo-root-wide, maximally
    -VISIBLE default, so an old on-disk claim stays visible under the new rollup filter rather
    than silently vanishing from every ``list`` the moment a caller upgrades ``tg``. Mirrors this
    module's general "never silently omit / never silently narrow visibility" posture."""
    scope = entry.get("scope")
    return scope if isinstance(scope, str) and scope else "."


def _scope_contains(broader: str, narrower: str) -> bool:
    """``True`` when ``narrower`` is scoped EQUAL TO or WITHIN ``broader`` -- segment-wise
    (never a raw string-prefix test: ``"core/ho"`` must NOT match ``"core/hoodie"``;
    ``PurePosixPath.parts`` gives this for free). ``"."`` (the repository root) contains every
    scope, including itself. Deliberately one-directional: a claim scoped to an ANCESTOR of the
    listed path (e.g. listing ``core/hooks`` when a claim is scoped to ``core``, or to ``"."``)
    is NOT considered contained -- only DESCENDANT (or equal) scopes roll up into a broader/
    ancestor listing, matching the exact rollup direction requested (list a root, see its
    subtrees) without also claiming the reverse (list a subtree, see everything above it)."""
    if broader == ".":
        return True
    broader_parts = PurePosixPath(broader).parts
    narrower_parts = PurePosixPath(narrower).parts
    return narrower_parts[: len(broader_parts)] == broader_parts


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

    PATH SCOPING (fix for the PATH-scope footgun): ``root`` -- the PHYSICAL location of
    ``claims/index.json`` -- is now :func:`_ledger_physical_root`'s ``.git``-canonicalized
    root, not ``path`` taken literally, so this claim lands in the SAME shared index every
    ``list``/``release`` call for this repository reads, regardless of which subtree ``path``
    names. ``path`` itself is preserved as this claim's ``scope`` (see :func:`_normalize_scope`)
    for ``list_claims``'s rollup filter.
    """
    root = _ledger_physical_root(path)
    scope = _normalize_scope(path, root)
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
        scope=scope,
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


def _release_result(
    released: list[dict[str, Any]],
    remaining: list[dict[str, Any]],
    *,
    listed_scope: str,
) -> dict[str, Any]:
    """Shared shape for ``release_claim``'s two return points (the default-inert fast path and
    the normal RMW path). ``released``/``released_count`` are the pre-fix contract, UNCHANGED in
    key name and meaning. The fields below are ADDITIVE and populated only when nothing was
    released, so a caller who got what they asked for never has to look past ``released_count``
    -- but a mismatched ``--claim-id``/``--symbol`` (e.g. a claim actually scoped elsewhere),
    which used to return a bare ``released_count: 0`` indistinguishable from "there was
    genuinely nothing to release," now says why and names what IS live instead (bounded --
    :data:`_MAX_LIVE_CLAIMS_ELSEWHERE_SHOWN`), so a wrong `--claim-id`/`--symbol` self-diagnoses
    rather than failing silently. Exit code is untouched by any of this -- `release_claim` never
    raises for a zero-match outcome (see its own docstring); this only enriches the SAME
    non-raising return value."""
    if released:
        return {
            "released": released,
            "released_count": len(released),
            "listed_scope": listed_scope,
            "unmatched_reason": None,
            "live_claims_elsewhere": [],
            "live_claims_elsewhere_count": 0,
            "live_claims_elsewhere_truncated": False,
        }
    total_elsewhere = len(remaining)
    if total_elsewhere == 0:
        reason = "No live claims exist for this repository."
    else:
        reason = (
            "No live claim matched the given --claim-id/--symbol; "
            f"{total_elsewhere} live claim(s) exist in this repository -- "
            "see live_claims_elsewhere."
        )
    shown = sorted(remaining, key=lambda entry: str(entry.get("created_at", "")), reverse=True)[
        :_MAX_LIVE_CLAIMS_ELSEWHERE_SHOWN
    ]
    elsewhere = [
        {
            "claim_id": entry.get("claim_id"),
            "agent_id": entry.get("agent_id"),
            "scope": _entry_scope(entry),
            "symbols": entry.get("symbols"),
            "files": entry.get("files"),
            "intent": entry.get("intent"),
            "expires_at": entry.get("expires_at"),
        }
        for entry in shown
    ]
    return {
        "released": released,
        "released_count": 0,
        "listed_scope": listed_scope,
        "unmatched_reason": reason,
        "live_claims_elsewhere": elsewhere,
        "live_claims_elsewhere_count": total_elsewhere,
        "live_claims_elsewhere_truncated": total_elsewhere > len(elsewhere),
    }


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
    NOT an error -- always returns normally with ``released == []``; see ``_release_result``
    for the additive ``unmatched_reason``/``live_claims_elsewhere`` honesty fields that
    accompany a zero-match return.

    PATH SCOPING (fix for the PATH-scope footgun): ``root`` -- WHICH repository's shared claims
    index this call operates on -- is :func:`_ledger_physical_root`'s ``.git``-canonicalized
    root, exactly like ``submit_claim``, so a release call reads/writes the SAME index a claim
    made from a different subtree of the SAME repository did. ``path`` does NOT filter WHICH
    claim matches -- ``--claim-id``/``--symbol`` do that, deliberately unchanged (adding scope-
    rollup matching here, unlike ``list_claims``, would let a release call silently drop an
    unrelated sibling agent's claim just because it shares a symbol name under a broader/
    ancestor scope -- release stays exact-match, only its failure message gained rollup-scope
    awareness).
    """
    if not claim_id and not symbol:
        raise LedgerUsageError("tg ledger release requires --claim-id or --symbol")

    root = _ledger_physical_root(path)
    listed_scope = _normalize_scope(path, root)
    index_path = _index_path(root)
    if not index_path.exists():
        # Default-inert fast path: `index_lock` would itself create
        # `.tensor-grep/ledger/claims/` just by acquiring the lock file (its own
        # `lock_path.parent.mkdir`), even though there is provably nothing to release when
        # no index has ever been written. Short-circuit BEFORE the lock so `tg ledger
        # release` on a repo with no claims never creates ledger state -- mirrors `claim`
        # legitimately creating it once (there IS something to write) without release ever
        # doing so for nothing.
        return _release_result([], [], listed_scope=listed_scope)

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

    return _release_result(released, remaining, listed_scope=listed_scope)


def list_claims(
    path: str,
    *,
    symbol: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Pure read: live (non-expired) claims scoped to ``path`` OR to any of its descendant
    subtrees (rollup -- see :func:`_scope_contains`), optionally further filtered by ``symbol``
    and/or ``agent_id``. Each returned claim carries its own ``scope`` (an old, pre-fix on-disk
    record missing the field is stamped ``"."``, per :func:`_entry_scope`). Never acquires the
    write lock and never writes -- expired entries are pruned for DISPLAY only, matching
    ``session_store.list_sessions``'s read-only shape (physical cleanup happens lazily on the
    next ``claim``/``release`` write).

    PATH SCOPING (fix for the PATH-scope footgun): ``root`` is
    :func:`_ledger_physical_root`'s ``.git``-canonicalized root, exactly like ``submit_claim``,
    so this reads the SAME shared index a claim made from a different subtree of the SAME
    repository wrote to -- ``list .`` (the default) now sees every live claim in the repo,
    regardless of which subtree each one was claimed under.
    """
    root = _ledger_physical_root(path)
    listed_scope = _normalize_scope(path, root)
    now = datetime.now(UTC)
    live = _prune_expired(_load_index(root), now=now)
    filtered = [entry for entry in live if _scope_contains(listed_scope, _entry_scope(entry))]
    if symbol is not None:
        filtered = [entry for entry in filtered if symbol in (entry.get("symbols") or [])]
    if agent_id is not None:
        filtered = [entry for entry in filtered if entry.get("agent_id") == agent_id]
    stamped = [{**entry, "scope": _entry_scope(entry)} for entry in filtered]
    return {"claims": stamped, "count": len(stamped)}


# ---------------------------------------------------------------------------
# Slice 2 (findings): on-disk paths -- a sibling subtree of claims above, sharing only the
# `<root>/.tensor-grep/ledger/` parent.
# ---------------------------------------------------------------------------


def _ledger_root_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _LEDGER_SUBDIR


def _findings_dir(root: Path) -> Path:
    return _ledger_root_dir(root) / _FINDINGS_SUBDIR


def _findings_blobs_dir(root: Path) -> Path:
    return _findings_dir(root) / _BLOBS_SUBDIR


def _findings_index_path(root: Path) -> Path:
    return _findings_dir(root) / _INDEX_FILE


def _blob_relpath(sha256: str) -> str:
    # Relative to `_ledger_root_dir(root)`, POSIX-separated so the stored string round-trips
    # through JSON and `Path` identically on Windows and POSIX.
    return f"{_FINDINGS_SUBDIR}/{_BLOBS_SUBDIR}/{sha256}.json"


def _blob_path(root: Path, sha256: str) -> Path:
    return _findings_blobs_dir(root) / f"{sha256}.json"


def _valid_sha256_hex(value: Any) -> TypeGuard[str]:
    """True only for a well-formed 64-char lowercase-hex sha256 digest. Every caller that
    builds a filesystem path from an index-derived `receipt_sha256` (`_verify_finding_blob`,
    `_distinct_blob_bytes`) MUST gate on this first: `receipt_sha256` is read back from
    `index.json`, a plain, per-repo, multi-agent-writable JSON file, so a hand-tampered index
    entry could otherwise smuggle a path-traversal string (e.g. `"../../../elsewhere"`) into
    `_blob_path` and redirect a read/stat outside `findings/blobs/`. A real sha256 hexdigest
    can never contain `/`, `.`, or any other path-meaningful character, so this check is a
    STRUCTURAL guarantee, not a blocklist.

    Typed as a `TypeGuard[str]` (not plain `bool`) so `if not _valid_sha256_hex(x): <exit>`
    narrows `x` to `str` for mypy at every call site -- `x` starts as `Any | None` (an
    `entry.get("receipt_sha256")` read off an untyped index record), and mypy does not treat
    `Any | None` as automatically compatible with the `str`-typed parameters (`_blob_path`,
    `hmac.compare_digest`, `set[str].add`) those call sites feed it into afterward."""
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _new_finding_id() -> str:
    # Mirrors _new_claim_id's shape: finding-<UTC-compact-timestamp>-<hex8>.
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"finding-{timestamp}-{uuid4().hex[:8]}"


def _configured_finding_ttl_seconds(explicit: int | None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    return _configured_positive_int(_FINDING_TTL_ENV, _DEFAULT_FINDING_TTL_SECONDS)


def _configured_max_total_blob_bytes() -> int:
    return _configured_positive_int(_MAX_BLOB_BYTES_ENV, _DEFAULT_MAX_TOTAL_BLOB_BYTES)


# ---------------------------------------------------------------------------
# Slice 2 (findings): bounded reads -- shared by the caller-supplied --receipt artifact
# (record) and a stored blob (find), each raising ITS OWN error type on the same underlying
# bound so a usage-time problem (LedgerArtifactError) is never confused with a store-integrity
# problem (LedgerIntegrityError).
# ---------------------------------------------------------------------------


def _read_bounded_file_bytes(
    path: Path, *, max_bytes: int, description: str, error_cls: type[LedgerError]
) -> bytes:
    if not path.exists():
        raise error_cls(f"{description} not found: {path}")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise error_cls(f"{description} could not be read: {path} ({exc})") from exc
    if len(raw) > max_bytes:
        raise error_cls(
            f"{description} at {path} exceeds the {max_bytes}-byte bound; refusing to read "
            "(DoS guard)."
        )
    return raw


def _parse_artifact_bytes(
    raw: bytes, *, description: str, error_cls: type[LedgerError]
) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise error_cls(f"{description} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_cls(f"{description} must be a JSON object.")
    return payload


def _read_artifact_file(path: Path) -> dict[str, Any]:
    """The caller-supplied `--receipt` file for `record_finding` -- a usage-time input, so
    every failure is `LedgerArtifactError`."""
    raw = _read_bounded_file_bytes(
        path,
        max_bytes=_MAX_ARTIFACT_FILE_BYTES,
        description="Evidence artifact",
        error_cls=LedgerArtifactError,
    )
    return _parse_artifact_bytes(
        raw, description=f"Evidence artifact at {path}", error_cls=LedgerArtifactError
    )


def _read_blob_artifact(blob_path: Path, finding_id: str) -> dict[str, Any]:
    """A previously-recorded blob read back at `find` time -- a store-integrity concern, so
    every failure is `LedgerIntegrityError` (never `LedgerArtifactError`, which is reserved for
    the caller-supplied `--receipt` input at record time)."""
    raw = _read_bounded_file_bytes(
        blob_path,
        max_bytes=_MAX_ARTIFACT_FILE_BYTES,
        description=f"Finding blob for {finding_id}",
        error_cls=LedgerIntegrityError,
    )
    return _parse_artifact_bytes(
        raw, description=f"Finding blob for {finding_id}", error_cls=LedgerIntegrityError
    )


# ---------------------------------------------------------------------------
# Slice 2 (findings): index read / write -- mirrors claims' _read_index_bytes / _load_index /
# _write_index exactly, pointed at the separate findings index.
# ---------------------------------------------------------------------------


def _read_findings_index_bytes(index_path: Path) -> bytes:
    try:
        with open(index_path, "rb") as handle:
            raw = handle.read(_MAX_INDEX_FILE_BYTES + 1)
    except FileNotFoundError:
        return b""
    if len(raw) > _MAX_INDEX_FILE_BYTES:
        raise LedgerIndexTooLargeError(
            f"Findings index at {index_path} exceeds the {_MAX_INDEX_FILE_BYTES}-byte bound; "
            "refusing to parse an unbounded file."
        )
    return raw


def _load_findings_index(root: Path) -> list[dict[str, Any]]:
    index_path = _findings_index_path(root)
    raw = _read_findings_index_bytes(index_path)
    if not raw:
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LedgerCorruptIndexError(
            f"Findings index at {index_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, list):
        raise LedgerCorruptIndexError(f"Findings index at {index_path} is not a JSON array")
    return [entry for entry in payload if isinstance(entry, dict)]


def _write_findings_index(root: Path, records: list[dict[str, Any]]) -> None:
    # atomic_write_json (and, transitively, index_lock's own lock_path.parent.mkdir) is the
    # ONLY thing that creates .tensor-grep/ledger/findings/ -- `find_findings` never does,
    # keeping a repo default-inert until the first `tg ledger record` (mirrors claims exactly).
    atomic_write_json(_findings_index_path(root), records)


# ---------------------------------------------------------------------------
# Slice 2 (findings): freshness, eviction, and blob GC
# ---------------------------------------------------------------------------


def _finding_is_fresh(finding_revision: Any, current_revision: dict[str, Any]) -> bool:
    """True only when BOTH the finding's captured revision and the CURRENT repo revision
    resolved to a real git identity (``status == "present"``) AND their ``commit_sha`` AND
    ``dirty_tree_sha256`` both match exactly.

    Unlike claims' `_revision_matches` (which returns `True`/`False`/`None` -- an honest
    "unknown" is fine there, since overlap reporting is purely advisory), `find`'s exit-code
    contract needs an unambiguous reuse/no-reuse signal: an unresolvable revision on EITHER
    side fails closed to `False` -- a finding is never silently served as current when tg
    cannot actually prove the repo hasn't moved."""
    if not isinstance(finding_revision, dict) or not isinstance(current_revision, dict):
        return False
    if finding_revision.get("status") != "present" or current_revision.get("status") != "present":
        return False
    return finding_revision.get("commit_sha") == current_revision.get(
        "commit_sha"
    ) and finding_revision.get("dirty_tree_sha256") == current_revision.get("dirty_tree_sha256")


def _distinct_blob_bytes(root: Path, records: list[dict[str, Any]]) -> int:
    """Sum of on-disk sizes for the DISTINCT `receipt_sha256` values referenced by `records` --
    a blob shared (dedup'd) by two findings counts once, not twice. A blob that is referenced
    but missing/unreadable contributes 0 rather than raising -- eviction accounting must never
    itself become a fail-closed surface; `find_findings` is what raises on a missing blob.
    Skips (never `.stat()`s) an entry whose `receipt_sha256` is not `_valid_sha256_hex` -- same
    path-traversal concern `_verify_finding_blob` guards against, applied here so a
    hand-tampered index can't redirect this accounting `.stat()` outside `findings/blobs/`
    either."""
    seen: set[str] = set()
    total = 0
    for entry in records:
        sha = entry.get("receipt_sha256")
        # Split into two separate guards (rather than `if not _valid_sha256_hex(sha) or sha in
        # seen: continue`) so the TypeGuard narrowing unambiguously applies: a single negated
        # -guard-with-early-`continue` is the canonical shape mypy narrows through; folding a
        # second, type-unrelated condition into the same `or` is not guaranteed to.
        if not _valid_sha256_hex(sha):
            continue
        if sha in seen:
            continue
        seen.add(sha)
        try:
            total += _blob_path(root, sha).stat().st_size
        except OSError:
            pass
    return total


def _evict_findings_over_cap(
    root: Path,
    records: list[dict[str, Any]],
    *,
    max_records: int | None = None,
    max_total_blob_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Oldest-`created_at`-first eviction under TWO independent bounds (mirrors claims'
    `_evict_oldest_over_cap` for the count cap; adds a dedup-aware byte cap on top, since a
    flood of small findings could stay under the count cap while still accumulating unbounded
    disk via a few large artifacts). Both parameters resolve from module constants/env INSIDE
    the body (not as default argument values) so a test can monkeypatch
    `_MAX_LIVE_FINDINGS`/`TG_LEDGER_MAX_BLOB_BYTES` and have it take effect -- mirrors
    `_evict_oldest_over_cap`'s own documented rationale."""
    effective_max_records = _MAX_LIVE_FINDINGS if max_records is None else max_records
    effective_max_bytes = (
        _configured_max_total_blob_bytes() if max_total_blob_bytes is None else max_total_blob_bytes
    )
    # Floored at _MAX_ARTIFACT_FILE_BYTES regardless of source (env config or an explicit
    # override): a byte cap smaller than the largest single artifact tg will ever accept
    # (_read_artifact_file's own bound) would let the while-loop below evict the record
    # `record_finding` JUST wrote in the SAME call, before it ever durably persists -- a
    # "success" response for a finding that was never actually kept. This is a total-DISK
    # -USAGE bound, not a per-artifact size limit (that is _MAX_ARTIFACT_FILE_BYTES's own job),
    # so silently flooring a misconfigured value here mirrors _configured_ttl_seconds's own
    # `max(1, ...)` floor idiom rather than letting it produce silent, user-visible data loss.
    effective_max_bytes = max(effective_max_bytes, _MAX_ARTIFACT_FILE_BYTES)

    ordered = sorted(records, key=lambda entry: str(entry.get("created_at", "")))
    if len(ordered) > effective_max_records:
        ordered = ordered[-effective_max_records:]
    while ordered and _distinct_blob_bytes(root, ordered) > effective_max_bytes:
        ordered.pop(0)
    return ordered


def _gc_orphaned_blobs(root: Path, live_records: list[dict[str, Any]]) -> None:
    """Delete any blob file under `findings/blobs/` not referenced by ANY record in
    `live_records` -- called after every write with the JUST-WRITTEN live set, so a blob is
    only ever removed once its last referencing finding has expired or been evicted. Best
    -effort: an individual file that cannot be listed/removed (permissions, concurrent
    deletion) does not fail the write that triggered GC."""
    blobs_dir = _findings_blobs_dir(root)
    if not blobs_dir.is_dir():
        return
    referenced = {
        entry["receipt_sha256"]
        for entry in live_records
        if isinstance(entry.get("receipt_sha256"), str)
    }
    try:
        blob_files = list(blobs_dir.iterdir())
    except OSError:
        return
    for blob_file in blob_files:
        if not blob_file.is_file():
            continue
        stem = (
            blob_file.name[: -len(".json")] if blob_file.name.endswith(".json") else blob_file.name
        )
        if stem not in referenced:
            try:
                blob_file.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Slice 2 (findings): public API
# ---------------------------------------------------------------------------


def record_finding(
    path: str,
    *,
    receipt_path: str | None = None,
    artifact_kind: str = "evidence-receipt",
    symbol: str | None = None,
    agent_id: str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Ingest an evidence-receipt/blast-radius/context-pack/repo-map artifact JSON as a
    content-addressed finding pointer other agents can `find` and reuse instead of
    recomputing.

    The artifact's own bytes are stored ONCE at `findings/blobs/<receipt_sha256>.json`
    (`receipt_sha256` derived via :func:`tensor_grep.cli.evidence_signing.receipt_digest` --
    the same content-address/integrity function `tg evidence` uses); recording the identical
    artifact twice is idempotent and dedupes to the same blob. Raises ONLY on a fail-closed
    condition (:class:`LedgerUsageError` for a missing `--receipt`/bad `--artifact-kind`,
    :class:`LedgerArtifactError` for a missing/oversized/non-JSON artifact file, or the sibling
    `IndexLockTimeoutError`/write-path `OSError`), in which case nothing is written.
    """
    if not receipt_path or not receipt_path.strip():
        raise LedgerUsageError("tg ledger record requires --receipt <path-to-artifact.json>")
    if artifact_kind not in _VALID_ARTIFACT_KINDS:
        raise LedgerUsageError(
            f"--artifact-kind must be one of {sorted(_VALID_ARTIFACT_KINDS)}, got {artifact_kind!r}"
        )

    root = _resolve_root(Path(path))
    artifact_file = Path(receipt_path).expanduser().resolve()
    artifact = _read_artifact_file(artifact_file)

    artifact_sha256 = receipt_digest(artifact)
    signing_block = artifact.get("signing")
    signature_block = artifact.get("signature")
    # Presence-only check (no cryptography, no trust decision) -- `signed`/`key_id` are cheap,
    # self-claimed metadata captured for free at record time. The actual trust decision
    # (`key_trusted` against TG_EVIDENCE_TRUSTED_KEYS) happens at `find` time, per the same "an
    # embedded key proves consistency, never authenticity" rule evidence_signing.py itself
    # documents -- record time has no reason to require the `cryptography` package at all.
    signed = isinstance(signing_block, dict) and isinstance(signature_block, dict)
    key_id: str | None = None
    # Re-check inline (redundant with `signed` above at runtime, but `if signed:` alone does not
    # let mypy narrow `signing_block` -- narrowing through an intermediate bool variable is not
    # tracked, only a direct `isinstance(...)` in the `if` condition itself is) so
    # `signing_block.get(...)` below sees `signing_block: dict[str, Any]`, not `Any | None`.
    if isinstance(signing_block, dict) and isinstance(signature_block, dict):
        candidate_key_id = signing_block.get("key_id")
        if isinstance(candidate_key_id, str) and candidate_key_id:
            key_id = candidate_key_id

    resolved_agent_id = resolve_agent_id(agent_id)
    resolved_symbol = symbol.strip() if symbol and symbol.strip() else None
    resolved_ttl = _configured_finding_ttl_seconds(ttl_seconds)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=resolved_ttl)

    record = FindingRecord(
        ledger_schema_version=LEDGER_SCHEMA_VERSION,
        kind="finding",
        finding_id=_new_finding_id(),
        agent_id=resolved_agent_id,
        artifact_kind=artifact_kind,
        symbol=resolved_symbol,
        receipt_sha256=artifact_sha256,
        blob_relpath=_blob_relpath(artifact_sha256),
        signed=signed,
        key_id=key_id,
        revision=_repo_revision_identity(root, exclude_prefixes=_LEDGER_EXCLUDE_PREFIXES),
        created_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
        ttl_seconds=resolved_ttl,
    )

    index_path = _findings_index_path(root)
    with index_lock(index_path):
        blob_path = _blob_path(root, artifact_sha256)
        if not blob_path.exists():
            # Content-addressed: identical bytes always land at the identical path, so this is
            # idempotent by construction. The existence check is a pure write-avoidance
            # optimization (skip a redundant fsync'd rewrite of bytes already on disk); it is
            # not required for correctness. Safe under concurrent writers to the SAME root
            # because this whole block runs under `index_lock`, serializing with every other
            # record/GC on this root.
            atomic_write_json(blob_path, artifact)
        existing = _load_findings_index(root)
        live = _prune_expired(existing, now=now)
        live.append(record.to_dict())
        live = _evict_findings_over_cap(root, live)
        _write_findings_index(root, live)
        _gc_orphaned_blobs(root, live)

    return {"finding": record.to_dict()}


def _verify_finding_blob(
    root: Path, entry: dict[str, Any], *, trusted_public_keys: list[str] | None
) -> dict[str, Any]:
    """Read the finding's blob back, recompute its content digest, and refuse (raise) if it no
    longer matches the recorded `receipt_sha256` -- integrity-checks ONLY findings that are
    actually about to be served (called after the symbol/artifact_kind/freshness filters in
    `find_findings`), never the whole index. For a `signed` finding, additionally attaches
    `key_trusted` by re-verifying against the CALLER's current trusted-key set (which may
    differ from whatever was configured at record time).

    The blob path is derived from the RECORDED `receipt_sha256` via `_blob_path` -- the SAME
    helper `record_finding` uses to WRITE it -- never from the index's own `blob_relpath`
    string. `blob_relpath` stays on the record purely for display/debugging; trusting it as a
    READ path would let a hand-tampered `index.json` (a plain, per-repo, multi-agent-writable
    JSON file) redirect this read to an arbitrary same-user JSON file. Content-addressing the
    read closes that: the path is a pure function of a `_valid_sha256_hex`-checked digest, so
    there is nothing for a tampered index to redirect."""
    finding_id = str(entry.get("finding_id"))
    expected_sha256 = entry.get("receipt_sha256")
    if not _valid_sha256_hex(expected_sha256):
        raise LedgerIntegrityError(
            f"Finding {finding_id} has a malformed receipt_sha256 on record (not a 64-char "
            "hex sha256 digest) -- refusing to derive a blob path from it."
        )
    blob_path = _blob_path(root, expected_sha256)
    artifact = _read_blob_artifact(blob_path, finding_id)
    actual_sha256 = receipt_digest(artifact)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise LedgerIntegrityError(
            f"Finding {finding_id} blob content does not match its recorded receipt_sha256 "
            "(tampered or corrupted)."
        )

    result = dict(entry)
    if entry.get("signed"):
        verify_result = verify_receipt(artifact, trusted_public_keys=trusted_public_keys)
        result["key_trusted"] = verify_result["checks"]["key_trusted"]
    else:
        result["key_trusted"] = None
    return result


def find_findings(
    path: str,
    *,
    symbol: str,
    artifact_kind: str | None = None,
    fresh_only: bool = False,
    trusted_public_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Look up previously recorded findings for `symbol` (optionally narrowed to
    `artifact_kind`) so a sibling agent can reuse an artifact instead of recomputing it.

    Pure read: like `list_claims`, never acquires the write lock, never writes, and never
    creates `.tensor-grep/ledger/findings/`. Every returned finding is stamped `fresh` (its
    captured revision matches the CURRENT repo state -- see `_finding_is_fresh`) and has passed
    a blob integrity check (see `_verify_finding_blob`); a finding whose blob fails that check
    raises :class:`LedgerIntegrityError` rather than being silently dropped or silently served.
    `fresh_only=True` restricts the RETURNED set to fresh findings only (stale ones are neither
    integrity-checked nor included); without it, both fresh and stale findings are returned
    (each individually stamped), for inspection.

    Returns `{"findings": [...], "count": N, "any_fresh": bool}` -- `any_fresh` is what the CLI
    layer's exit-code contract (0 = reuse, 1 = recompute) branches on, independent of whether
    `fresh_only` was passed.
    """
    if not symbol or not symbol.strip():
        raise LedgerUsageError("tg ledger find requires --symbol")
    resolved_symbol = symbol.strip()

    root = _resolve_root(Path(path))
    now = datetime.now(UTC)
    # Display-only prune, exactly like list_claims: expired findings are excluded from the
    # result but the index is never rewritten by a pure read (physical cleanup happens lazily
    # on the next `record`).
    live = _prune_expired(_load_findings_index(root), now=now)

    matched = [
        entry
        for entry in live
        if entry.get("symbol") == resolved_symbol
        and (artifact_kind is None or entry.get("artifact_kind") == artifact_kind)
    ]

    current_revision = _repo_revision_identity(root, exclude_prefixes=_LEDGER_EXCLUDE_PREFIXES)
    to_serve: list[dict[str, Any]] = []
    for entry in matched:
        fresh = _finding_is_fresh(entry.get("revision"), current_revision)
        if fresh_only and not fresh:
            continue
        stamped = dict(entry)
        stamped["fresh"] = fresh
        to_serve.append(stamped)

    verified = [
        _verify_finding_blob(root, entry, trusted_public_keys=trusted_public_keys)
        for entry in to_serve
    ]

    return {
        "findings": verified,
        "count": len(verified),
        "any_fresh": any(entry["fresh"] for entry in verified),
    }
