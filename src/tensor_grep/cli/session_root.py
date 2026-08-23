"""Project-root resolution for the session store and the warm daemon.

Split out of `session_store.py` in 2026-08 because the file-size ratchet correctly refused the
growth: this concern arrived with a lot of load-bearing prose (every rule below is a receipt from
a defect that shipped or was one commit from shipping), and it is genuinely a separate concern
from storing sessions. `session_store` re-exports these names, so every existing importer --
`session_daemon`, `ledger_store`, `evidence_receipt` -- keeps working unchanged.

There are TWO resolutions here and confusing them has already caused one regression:

* `_resolve_root`   -- WHERE STATE LIVES. Anchors a subtree to its project root, so a session
                       opened in `src/` is visible from the repo root and the warm daemon is
                       reachable from anywhere in the tree.
* `_resolve_literal_dir` -- WHAT DIRECTORY THE CALLER NAMED. No anchoring. `ledger_store` derives
                       root-relative scope strings from it; anchoring there silently collapsed
                       every subtree claim onto the repo root.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TG_DIRNAME = ".tensor-grep"
_SESSIONS_SUBDIR = "sessions"
_INDEX_FILE = "index.json"

#: Language manifests that identify a project root ONLY when no VCS root exists above them.
#: Deliberately NOT including `.git` here -- see `_find_project_root`.
_PROJECT_MANIFEST_MARKERS = ("pyproject.toml", "Cargo.toml", "package.json")

#: How far `_find_project_root` may climb. A project root is near by construction; this stops a
#: deep path from walking all the way to the filesystem root.
_MAX_PROJECT_ROOT_ASCENT = 24


def _shared_territory_roots() -> frozenset[Path]:
    """Directories that are NEVER a project root, however many markers they carry.

    The system temp dir and the user home dir are SHARED TERRITORY: unrelated trees live side by
    side under them. Anchoring to one makes every such tree share a single session store and a
    single daemon -- the identical hazard the innermost-`.git` rule fixes for nested checkouts,
    reached through the manifest pass instead.

    This is not hypothetical and was not caught by review. An earlier revision documented it as an
    accepted residual risk; it then failed a real warm-daemon test, because `%TEMP%` on this
    machine holds a stray `Cargo.toml` and `$HOME` a `package.json`. Measured before the fix:
    `<temp>/tmpXXXX/proj` resolved to `<temp>` itself, which silently routed the client away from
    the daemon its own test had just started. An accepted risk that a test can trip is a defect.
    """
    # NEVER `continue` past an unresolvable candidate. This is a DENY set: dropping an entry
    # silently re-opens the exact escape the function exists to close, and the caller cannot tell
    # a complete deny set from a truncated one. The silent-loss census ratchet caught precisely
    # this shape here (`tests/unit/test_silent_loss_census_ratchet.py`) and it was right to --
    # an OSError resolving TEMP would have let every tree under TEMP share one store again.
    #
    # So an unresolvable candidate degrades to its UNRESOLVED path instead of vanishing. That is
    # strictly better than dropping it: the literal path still matches a caller who names the same
    # spelling, so the deny rule keeps working for the common case rather than failing open.
    roots: set[Path] = set()
    for candidate in (tempfile.gettempdir(), os.path.expanduser("~")):
        try:
            roots.add(Path(candidate).resolve())
        except OSError:  # pragma: no cover - unresolvable HOME/TEMP
            roots.add(Path(candidate))
    return frozenset(roots)


def _find_project_root(start: Path) -> Path | None:
    """Walk UP from `start` to the project root: the INNERMOST `.git`, else the nearest manifest.

    The two-pass shape is load-bearing. A single nearest-marker walk over
    `(".git", "pyproject.toml", "Cargo.toml", "package.json")` picks whichever marker appears
    first in the CLOSEST directory, which in this repo resolves:

        rust_core/src -> rust_core   (rust_core/Cargo.toml)   WRONG
        npm           -> npm         (npm/package.json)       WRONG
        src           -> tensor-grep (root pyproject.toml)    right, by luck

    -- i.e. it fixes `src/` and silently leaves `rust_core/` and `npm/` with the original defect,
    which is worse than not fixing it: a partial fix that looks complete. Measured against the
    real tree before this function was written.

    A dedicated VCS pass gives `tensor-grep` for all of `rust_core/src`, `src`, `npm` and `docs`
    (measured) -- none of those carry their own `.git`, so the innermost-`.git` rule below returns
    the checkout root for every one of them. Returns None when nothing matches, so an ad-hoc directory outside any
    project keeps today's behaviour instead of being silently relocated.

    Nesting note: the INNERMOST `.git` wins. A codex audit (2026-08-22) caught the first draft
    preferring the OUTERMOST one, which made a standalone project vendored inside another checkout
    anchor to the OUTER repo -- two unrelated projects then SHARE one session store and one daemon.
    Reproduced before the fix: `<outer>/vendor/standalone/src` resolved to `<outer>`.
    A git SUBMODULE has a `.git` FILE (not a directory), so it also anchors to itself under this
    rule; that is the same answer the outermost rule gave only by accident of this repo having no
    submodules, and self-anchoring is the safer default (an isolated store, never a shared one).
    """
    # THE WALK IS BOUNDED. An unbounded climb reaches the filesystem root and can pick up markers
    # in shared territory -- measured on this box, `C:\Users\<user>\AppData\Local\Temp` contains a
    # stray `Cargo.toml` and `C:\Users\<user>` contains a `package.json`, so a scratch directory
    # under TEMP resolves to a "project root" inside the user's profile, putting a session store
    # in the home directory and letting two unrelated trees SHARE one store.
    #
    # The bound does NOT eliminate that case (the offending marker is only ~5 levels up, well
    # inside any sane cap) -- it is honest about what it does: it stops the walk from running to
    # the filesystem root on a deep path. The residual risk is documented rather than papered
    # over: a caller operating inside a directory that has a stray manifest above it and no
    # closer project marker will anchor to that stray manifest. Every real checkout has a `.git`
    # or its own manifest nearer than any such stray, and the VCS pass runs first.
    ladder = [start, *start.parents][:_MAX_PROJECT_ROOT_ASCENT]

    shared = _shared_territory_roots()

    for candidate in ladder:
        if candidate in shared:
            continue
        if (candidate / ".git").exists():
            return candidate  # FIRST hit == innermost; see the nesting note above

    for candidate in ladder:
        if candidate in shared:
            continue
        for marker in _PROJECT_MANIFEST_MARKERS:
            if (candidate / marker).exists():
                return candidate
    return None


def _resolve_literal_dir(path: Path) -> Path:
    """`path` resolved to an existing DIRECTORY, with no project anchoring.

    This is exactly what `_resolve_root` did before the G4.1/G4.2 anchoring landed, and it is
    split out because anchoring silently changed the meaning of a value two ledger call sites
    depend on. `ledger_store._normalize_scope` derives a root-RELATIVE scope string from it: once
    `_resolve_root` began returning the project root, `claim core/hooks` recorded its scope as
    "." instead of "core/hooks" -- every subtree claim collapsing onto the repo root, silently.

    Caught by `test_claim_subpath_rolls_up_into_root_list`, which passes on `origin/main`. The
    lesson is the same one the scan/store split in `open_session` records: one helper served two
    different questions ("where does state live?" and "what literal directory did the caller
    name?"), and anchoring is correct for the first and wrong for the second. Callers now say
    which they mean.
    """
    resolved = path.expanduser().resolve()
    return resolved if resolved.is_dir() else resolved.parent


def _resolve_root(path: Path) -> Path:
    # G4.1/G4.2 (2026-08-23): this used to return the caller's path as-is, so `tg ... src` got its
    # OWN session store under src/.tensor-grep/ and could not see a daemon started at the repo
    # root. Measured on published v1.111.7: `session show <id>` worked from src/ and returned
    # "Session not found" one directory up, while `session list` from the root returned 64
    # sessions NOT containing the new one -- a confidently wrong answer, not an error. The same
    # cause made the warm daemon unreachable from a subtree: two identical `tg defs src ...` calls
    # against a RUNNING daemon gave hits=0 entries=0 and cache_misses=0, and zero MISSES proves
    # the daemon was never consulted at all (control: the same query at `.` gave misses=1, then a
    # repeat gave hits=1).
    #
    # Anchoring to the project root makes both lookups agree regardless of which directory inside
    # the project the caller passes. No marker found -> keep the old behaviour.
    resolved = path.expanduser().resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    return _find_project_root(start) or start


def _sessions_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _SESSIONS_SUBDIR


def _index_path(root: Path) -> Path:
    return _sessions_dir(root) / _INDEX_FILE


def _session_payload_path(root: Path, session_id: str) -> Path:
    # Audit HIGH (path traversal): session_id reaches this join from the CLI, the MCP
    # tg_session_show/refresh tools, and the token-authenticated daemon. An absolute or
    # `..`-shaped id resets pathlib's join and escapes the sessions dir — arbitrary .json
    # read via get_session, destructive overwrite via refresh_session. Refuse absolute /
    # `..` ids and assert the resolved payload path stays inside the sessions dir before
    # any read/write. Generated ids (`session-<ts>-<root>-<hex>`) always pass.
    candidate = Path(session_id)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"Refusing session id outside sessions dir: {session_id!r}")
    sessions_dir = _sessions_dir(root)
    sessions_dir_resolved = sessions_dir.resolve()
    resolved = (sessions_dir / f"{session_id}.json").resolve()
    if resolved != sessions_dir_resolved and sessions_dir_resolved not in resolved.parents:
        raise ValueError(f"Refusing session id outside sessions dir: {session_id!r}")
    return resolved


_SESSION_NEARBY_LOOKUP_ENV = "TG_SESSION_NEARBY_LOOKUP"


def _nearby_lookup_enabled() -> bool:
    # audit S9: default to confined (explicit-root) lookups; only widen to parent/sibling
    # discovery when the operator explicitly opts in.
    raw_value = os.environ.get(_SESSION_NEARBY_LOOKUP_ENV)
    if raw_value is None:
        return False
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _nearby_session_roots(path: str = ".") -> list[Path]:
    root = _resolve_root(Path(path))
    candidates: list[Path] = [root]
    candidates.extend(parent for parent in root.parents if parent != root)
    try:
        candidates.extend(child for child in root.iterdir() if child.is_dir())
    except OSError:
        pass

    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if _index_path(resolved).exists():
            roots.append(resolved)
    return roots


def _session_root_for_payload(session_id: str, path: str = ".") -> Path:
    root = _resolve_root(Path(path))
    if _session_payload_path(root, session_id).exists():
        return root
    # audit S9: nearby (parent/sibling) discovery silently loads payloads from outside the
    # requested root. Keep it off unless explicitly enabled.
    if _nearby_lookup_enabled():
        for candidate in _nearby_session_roots(path):
            if candidate == root:
                continue
            if _session_payload_path(candidate, session_id).exists():
                return candidate
    return root
