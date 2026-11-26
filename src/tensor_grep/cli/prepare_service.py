"""`tg prepare` payload/capsule composition and blast-radius floor builder.

Extracted verbatim from `cli/main.py` (Task 6 Step 0, docs/plans/2026-08-02-backlog-closeout-
implementation-plan.md): a refactoring-only move of `_build_prepare_blast_radius_floor` and
`_build_prepare_payload` so that a later task can extend `tg prepare` with edit-verification
without growing `main.py` further. No behavior changes -- the function bodies below are
byte-identical to their prior location in `main.py`. This module must never import
`tensor_grep.cli.main` back (that would reintroduce the cycle this extraction exists to avoid);
`main.py` imports these two names from here instead of defining them.

Task 8 Step 2 (docs/plans/2026-08-02-backlog-closeout-implementation-plan.md, Python-only slice,
2026-08-05): this session adds ONLY the strict typed composition API named in that step --
`PrepareSnapshotV1` / `build_prepare_snapshot` below -- as a pure, additive wrapper over the
existing `_build_prepare_payload`. It changes ZERO legacy behavior: `tg prepare`'s Typer adapter
in `main.py` still calls `_build_prepare_payload` directly and is untouched by this addition, so
its output stays byte-identical by construction (nothing on that call path changed). The full
`tg edit-ready` command (frozen argv, `EditReadyTicketV1`, the claims-only OS fence, atomic
no-clobber baseline publication, and the native/Rust half) is explicitly OUT OF SCOPE for this
slice -- see the PR description for the scope-down rationale. `build_prepare_snapshot` exists so
a *future* edit-ready composition can consume a stable typed projection of the same data this
module already produces, without that future work needing to re-parse `_build_prepare_payload`'s
untyped dict shape by hand.
"""

import hashlib
import hmac
import json
import sqlite3
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tensor_grep.cli import prepare_larger as _prepare_larger
from tensor_grep.cli import prepare_protocol as _prepare_protocol

# AGT-03 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 03): the exact-string
# validation-recipe allowlist and its selector now live in prepare_protocol.py, alongside the
# typed `ValidationAdvice` record that consumes them -- this module must never import back into
# prepare_protocol's own internals beyond `build_validation_advice`, the same one-direction rule
# `_build_prepare_payload`'s own docstring states for `cli.main`. Kept as aliases here only
# because tests may still reference the historical names on this module.
_KNOWN_VALIDATION_RECIPES = _prepare_protocol._KNOWN_VALIDATION_RECIPES
_select_validation_argv = _prepare_protocol._select_validation_argv

_PREPARE_CACHE_REL_PATH = Path(".tg_cache") / "prepare_v1" / "repo_map_cache.json"
_PREPARE_CACHE_MAX_BYTES = 32 * 1024 * 1024
_PREPARE_CACHE_SCHEMA = 1
_LARGER_LAZY_MIN_FILES = _prepare_larger._LARGER_LAZY_MIN_FILES
_LARGER_SEED_CAP = _prepare_larger._LARGER_SEED_CAP
_LARGER_EGO_MAX_FILES = _prepare_larger._LARGER_EGO_MAX_FILES
_LARGER_SEARCH_TIMEOUT_S = _prepare_larger._LARGER_SEARCH_TIMEOUT_S
_PROJECT_CONFIG_FILES = (
    "pyproject.toml",
    "tsconfig.json",
    "Cargo.toml",
    "package.json",
    "go.mod",
)


def _sign_prepare_cache(secret: bytes, payload_json: str) -> str:
    return hmac.new(
        secret,
        b"tg-prepare-v1\0" + payload_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _lstat_regular_file(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400):
        return None
    return (int(st.st_mtime_ns), int(st.st_ctime_ns), int(st.st_size))


def _prepare_cache_context(
    path: str,
) -> tuple[Path, Path, bytes] | None:
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.repo_map_fast_cache import canonical_symbol_cache_root
    from tensor_grep.cli.symbols_cache import authenticated_content_root
    from tensor_grep.cli.symbols_cache_auth import load_machine_key

    root = _repo_map._resolved_input_path(path)
    cache_root = canonical_symbol_cache_root(
        root, _repo_map._infer_project_root, authenticated_content_root()
    )
    signing_key = load_machine_key(cache_root)
    if signing_key is None:
        return None
    return root, cache_root, signing_key


_build_config_meta = _prepare_larger.build_config_meta


def _build_file_meta(
    file_paths: Iterable[str],
    *,
    cache_root: Path,
    max_bytes: int,
    deadline_monotonic: float | None = None,
) -> dict[str, dict[str, Any]] | None:
    from tensor_grep.io.confined import read_confined

    meta: dict[str, dict[str, Any]] = {}
    for f_str in file_paths:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None
        fp = Path(f_str)
        stat_key = _lstat_regular_file(fp)
        if stat_key is None:
            return None
        if stat_key[2] > max_bytes:
            meta[f_str] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "oversized": True,
            }
            continue
        try:
            content = read_confined(cache_root, fp, max_bytes)
            digest = hashlib.sha256(content).hexdigest()
        except OSError:
            return None
        meta[f_str] = {
            "mtime_ns": stat_key[0],
            "ctime_ns": stat_key[1],
            "size": stat_key[2],
            "sha256": digest,
        }
    return meta


def _capture_pre_build_stats(
    path: str,
    max_repo_files: int,
    deadline_monotonic: float | None,
) -> tuple[dict[str, tuple[int, int, int, str | None]], dict[str, dict[str, Any]]] | None:
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.io.confined import read_confined

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return None
    ctx = _prepare_cache_context(path)
    if ctx is None:
        return None
    root, cache_root, _signing_key = ctx
    if not _repo_map._path_exists_with_receipt(root):
        return None
    context_root = root if _repo_map._is_dir_with_receipt(root) else root.parent
    max_parse_bytes = _repo_map._max_parse_bytes()
    config_meta = _build_config_meta(context_root, cache_root, max_parse_bytes)
    walk_deadline = _repo_map._DeadlineBreakFlag()
    walk_unreadable = _repo_map._UnreadablePathFlag()
    all_files = _repo_map._iter_repo_files(
        root,
        max_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=walk_deadline,
        unreadable_hit=walk_unreadable,
    )
    if walk_deadline.hit or walk_unreadable.hit:
        return None
    stats: dict[str, tuple[int, int, int, str | None]] = {}
    for current in all_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None
        if not _repo_map._is_repo_context_file(current, context_root):
            continue
        stat_key = _lstat_regular_file(current)
        if stat_key is None:
            return None
        if stat_key[2] > max_parse_bytes:
            stats[str(current)] = (stat_key[0], stat_key[1], stat_key[2], None)
            continue
        try:
            content = read_confined(cache_root, current, max_parse_bytes)
            digest = hashlib.sha256(content).hexdigest()
        except OSError:
            return None
        stats[str(current)] = (stat_key[0], stat_key[1], stat_key[2], digest)
    return stats, config_meta


def _load_warm_prepare_cache(
    path: str,
    max_repo_files: int,
    deadline_monotonic: float | None,
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool]:
    """Load and ``(mtime_ns, ctime_ns, size)`` stat-first reconcile the signed warm repo-map cache.

    Stat-first freshness contract:
    Each cached file stores ``(mtime_ns, ctime_ns, size, sha256)`` (or ``oversized: True`` when
    above ``_max_parse_bytes()``). Including ``ctime_ns`` ensures NTFS/POSIX content writes with
    restored ``mtime_ns`` still trigger confined hash verification. Unchanged stat tuples skip
    reading and parsing; changed stat tuples are read via ``read_confined(..., _max_parse_bytes())``
    and re-parsed only when the content hash changed, with post-parse stat+hash verification to
    reject leaf-swap TOCTOU races.

    Returns ``(repo_map, cached_floors, file_meta, cache_dirty)``.
    """
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.incompleteness import budget_remediable
    from tensor_grep.io.confined import read_confined

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return None, {}, {}, False
    ctx = _prepare_cache_context(path)
    if ctx is None:
        return None, {}, {}, False
    root, cache_root, signing_key = ctx
    cache_path = cache_root / _PREPARE_CACHE_REL_PATH
    if not cache_path.exists():
        return None, {}, {}, False
    try:
        raw_bytes = read_confined(cache_root, cache_path, _PREPARE_CACHE_MAX_BYTES)
        envelope = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(envelope, dict):
            return None, {}, {}, False
        payload_json = envelope.get("payload")
        mac = envelope.get("mac")
        if not isinstance(payload_json, str) or not isinstance(mac, str):
            return None, {}, {}, False
        if not hmac.compare_digest(_sign_prepare_cache(signing_key, payload_json), mac):
            return None, {}, {}, False
        data = json.loads(payload_json)
    except (OSError, ValueError, TypeError):
        return None, {}, {}, False

    max_parse_bytes = _repo_map._max_parse_bytes()
    if (
        not isinstance(data, dict)
        or data.get("version") != _PREPARE_CACHE_SCHEMA
        or data.get("scope") != str(root)
        or data.get("max_repo_files") != max_repo_files
        or data.get("max_parse_bytes") != max_parse_bytes
    ):
        return None, {}, {}, False

    cached_rm = data.get("repo_map")
    cached_meta = data.get("file_meta")
    cached_floors = data.get("floors")
    cached_config_meta = data.get("config_meta")
    if (
        not isinstance(cached_rm, dict)
        or not isinstance(cached_meta, dict)
        or not isinstance(cached_config_meta, dict)
    ):
        return None, {}, {}, False
    if not isinstance(cached_floors, dict):
        cached_floors = {}

    if not _repo_map._path_exists_with_receipt(root):
        return None, {}, {}, False
    context_root = root if _repo_map._is_dir_with_receipt(root) else root.parent
    if _build_config_meta(context_root, cache_root, max_parse_bytes) != cached_config_meta:
        return None, {}, {}, False

    _repo_map._load_gitignore_matcher.cache_clear()
    walk_deadline = _repo_map._DeadlineBreakFlag()
    walk_unreadable = _repo_map._UnreadablePathFlag()
    all_files = _repo_map._iter_repo_files(
        root,
        max_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=walk_deadline,
        unreadable_hit=walk_unreadable,
    )
    if walk_deadline.hit or walk_unreadable.hit:
        return None, {}, {}, False

    context_files = [
        current for current in all_files if _repo_map._is_repo_context_file(current, context_root)
    ]
    current_paths = [str(current) for current in context_files]
    current_path_set = set(current_paths)
    cached_path_set = set(cached_meta.keys())

    scan_limit = cached_rm.get("scan_limit")
    possibly_truncated = isinstance(scan_limit, dict) and bool(scan_limit.get("possibly_truncated"))
    if possibly_truncated and current_path_set != cached_path_set:
        return None, {}, {}, False

    new_meta: dict[str, dict[str, Any]] = {}
    dirty_paths: set[str] = set()
    dirty_expected: dict[str, tuple[tuple[int, int, int], str]] = {}
    stat_only_updates = False

    for current in context_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None, {}, {}, False
        p_str = str(current)
        stat_key = _lstat_regular_file(current)
        if stat_key is None:
            return None, {}, {}, False
        prev = cached_meta.get(p_str)
        if (
            isinstance(prev, dict)
            and prev.get("mtime_ns") == stat_key[0]
            and prev.get("ctime_ns") == stat_key[1]
            and prev.get("size") == stat_key[2]
            and (
                prev.get("oversized") is True
                or prev.get("unparsed") is True
                or isinstance(prev.get("sha256"), str)
            )
        ):
            new_meta[p_str] = prev
            continue
        if (
            isinstance(cached_rm.get("lazy_larger"), bool)
            and cached_rm.get("lazy_larger")
            and (prev is None or prev.get("unparsed") is True)
        ):
            new_meta[p_str] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "unparsed": True,
            }
            if isinstance(prev, dict) and prev.get("unparsed") is True:
                stat_only_updates = True
            else:
                dirty_paths.add(p_str)
            continue
        if stat_key[2] > max_parse_bytes:
            new_meta[p_str] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "oversized": True,
            }
            if isinstance(prev, dict) and prev.get("oversized") is True:
                stat_only_updates = True
            else:
                dirty_paths.add(p_str)
            continue
        try:
            content = read_confined(cache_root, current, max_parse_bytes)
            digest = hashlib.sha256(content).hexdigest()
        except OSError:
            return None, {}, {}, False
        new_meta[p_str] = {
            "mtime_ns": stat_key[0],
            "ctime_ns": stat_key[1],
            "size": stat_key[2],
            "sha256": digest,
        }
        if isinstance(prev, dict) and prev.get("sha256") == digest:
            stat_only_updates = True
        else:
            dirty_paths.add(p_str)
            dirty_expected[p_str] = (stat_key, digest)

    capped_count = len(all_files)
    _capped = capped_count >= max_repo_files
    _cause = (
        _repo_map._scan_limit_cause(all_files, context_root, capped_count, max_repo_files)
        if _capped
        else "project-files"
    )
    current_truncated = _capped and _cause == "project-files"
    expected_cause = _cause if _capped else None
    scan_meta_matches = (
        isinstance(scan_limit, dict)
        and scan_limit.get("scanned_files") == capped_count
        and scan_limit.get("truncation_cause") == expected_cause
        and bool(scan_limit.get("possibly_truncated")) == current_truncated
        and len(cached_rm.get("related_paths") or []) == len(context_files)
        and ("total_files" not in cached_rm or cached_rm.get("total_files") == len(context_files))
        and ("total_files" not in scan_limit or scan_limit.get("total_files") == capped_count)
    )

    deleted_paths = cached_path_set - current_path_set
    if not dirty_paths and not deleted_paths and scan_meta_matches:
        return cached_rm, cached_floors, new_meta, stat_only_updates

    if possibly_truncated or current_truncated:
        return None, {}, {}, False

    # Incremental update: reuse symbols/imports for unchanged files, parse only dirty/added files.
    _repo_map._clear_all_source_caches()
    _repo_map._load_gitignore_matcher.cache_clear()
    _repo_map._prime_all_language_repo_contexts(context_root)

    old_symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for sym in cached_rm.get("symbols") or []:
        if isinstance(sym, dict) and isinstance(sym.get("file"), str):
            f_key = sym["file"]
            if f_key in current_path_set and f_key not in dirty_paths:
                old_symbols_by_file.setdefault(f_key, []).append(sym)

    old_imports_by_file: dict[str, dict[str, Any]] = {}
    for imp in cached_rm.get("imports") or []:
        if isinstance(imp, dict) and isinstance(imp.get("file"), str):
            f_key = imp["file"]
            if f_key in current_path_set and f_key not in dirty_paths:
                old_imports_by_file[f_key] = imp

    tests = [str(current) for current in context_files if _repo_map._is_test_file(current)]
    non_test = [str(current) for current in context_files if not _repo_map._is_test_file(current)]
    source_files = non_test or tests

    new_symbols: list[dict[str, Any]] = []
    new_imports: list[dict[str, Any]] = []
    for current in context_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None, {}, {}, False
        p_str = str(current)
        if p_str in dirty_paths:
            if new_meta.get(p_str, {}).get("unparsed") is True:
                continue
            cur_imports: list[str]
            cur_symbols: list[dict[str, Any]]
            if new_meta.get(p_str, {}).get("oversized") is True:
                cur_imports, cur_symbols = [], []
            else:
                expected = dirty_expected.get(p_str)
                if expected is None:
                    return None, {}, {}, False
                expected_stat, expected_digest = expected
                if _lstat_regular_file(current) != expected_stat:
                    return None, {}, {}, False
                cur_imports, cur_symbols = _repo_map._imports_and_symbols_for_path(current)
                if _lstat_regular_file(current) != expected_stat:
                    return None, {}, {}, False
                try:
                    post_content = read_confined(cache_root, current, max_parse_bytes)
                except OSError:
                    return None, {}, {}, False
                if hashlib.sha256(post_content).hexdigest() != expected_digest:
                    return None, {}, {}, False
            if cur_imports:
                new_imports.append({
                    "file": p_str,
                    "imports": cur_imports,
                    "provenance": _repo_map._symbol_navigation_provenance_for_path(p_str),
                })
            new_symbols.extend(cur_symbols)
        else:
            if p_str in old_imports_by_file:
                new_imports.append(old_imports_by_file[p_str])
            new_symbols.extend(old_symbols_by_file.get(p_str, []))

    updated_rm = dict(cached_rm)
    updated_rm["files"] = source_files
    updated_rm["tests"] = tests
    updated_rm["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
    updated_rm["symbols"] = new_symbols
    updated_rm["imports"] = new_imports
    updated_rm["scan_limit"] = {
        "max_repo_files": max_repo_files,
        "scanned_files": capped_count,
        "possibly_truncated": current_truncated,
        "truncation_cause": _cause if _capped else None,
        **({"budget_remediable": budget_remediable(_cause)} if _capped else {}),
    }
    updated_rm["scan_remediation"] = (
        _repo_map._SCAN_LIMIT_TRUNCATED_REMEDIATION if current_truncated else None
    )
    return updated_rm, {}, new_meta, True


def _save_warm_prepare_cache(
    path: str,
    max_repo_files: int,
    rm: dict[str, Any],
    floors: dict[str, dict[str, Any]],
    file_meta: dict[str, dict[str, Any]] | None,
    deadline_monotonic: float | None,
    *,
    pre_build_stats: (
        tuple[dict[str, tuple[int, int, int, str | None]], dict[str, dict[str, Any]]] | None
    ) = None,
) -> None:
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.symbols_cache_io import cache_lock
    from tensor_grep.io.confined import (
        prepare_directory,
        publish_confined,
        read_confined,
    )

    if rm.get("partial") or rm.get("unreadable_paths"):
        return
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return
    ctx = _prepare_cache_context(path)
    if ctx is None:
        return
    root, cache_root, signing_key = ctx
    context_root = root if _repo_map._is_dir_with_receipt(root) else root.parent
    max_parse_bytes = _repo_map._max_parse_bytes()
    related_paths = list(rm.get("related_paths") or [])
    if file_meta is None:
        if pre_build_stats is None:
            return
        file_meta = _build_file_meta(
            related_paths,
            cache_root=cache_root,
            max_bytes=max_parse_bytes,
            deadline_monotonic=deadline_monotonic,
        )
        if file_meta is None:
            return
    config_meta = _build_config_meta(context_root, cache_root, max_parse_bytes)
    if pre_build_stats is not None:
        pre_file_stats, pre_config_meta = pre_build_stats
        if config_meta != pre_config_meta:
            return
        for p_str in related_paths:
            pre_stat = pre_file_stats.get(p_str)
            meta = file_meta.get(p_str)
            if pre_stat is None or meta is None:
                return
            meta_digest = (
                None
                if (meta.get("oversized") is True or meta.get("unparsed") is True)
                else meta.get("sha256")
            )
            if (meta.get("mtime_ns"), meta.get("ctime_ns"), meta.get("size"), meta_digest) != (
                pre_stat
            ):
                return
    payload_obj = {
        "version": _PREPARE_CACHE_SCHEMA,
        "scope": str(root),
        "max_repo_files": max_repo_files,
        "max_parse_bytes": max_parse_bytes,
        "repo_map": rm,
        "file_meta": file_meta,
        "config_meta": config_meta,
        "floors": floors,
    }
    payload_json = json.dumps(payload_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    envelope = json.dumps(
        {"payload": payload_json, "mac": _sign_prepare_cache(signing_key, payload_json)},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(envelope) > _PREPARE_CACHE_MAX_BYTES:
        return
    cache_path = cache_root / _PREPARE_CACHE_REL_PATH
    try:
        prepare_directory(cache_root, cache_path.parent)
        ignore_path = cache_path.parent / ".gitignore"
        try:
            publish_confined(cache_root, ignore_path, b"*\n", only_if_missing=True)
        except FileExistsError:
            read_confined(cache_root, ignore_path, 65536)
        with cache_lock(cache_root, cache_path, deadline_monotonic):
            if _build_config_meta(context_root, cache_root, max_parse_bytes) != config_meta:
                return
            for p_str, meta in file_meta.items():
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    return
                fp = Path(p_str)
                cur_stat = _lstat_regular_file(fp)
                if cur_stat != (
                    meta.get("mtime_ns"),
                    meta.get("ctime_ns"),
                    meta.get("size"),
                ):
                    return
                if meta.get("oversized") is True:
                    if cur_stat[2] <= max_parse_bytes:
                        return
                elif meta.get("unparsed") is True:
                    pass
                else:
                    cur_content = read_confined(cache_root, fp, max_parse_bytes)
                    if hashlib.sha256(cur_content).hexdigest() != meta.get("sha256"):
                        return
            publish_confined(cache_root, cache_path, envelope)
    except (OSError, ValueError):
        return


def _build_prepare_blast_radius_floor(
    *,
    path: str,
    rm: dict[str, Any],
    target: dict[str, Any],
    call_site_evidence: dict[str, Any],
    related_call_sites: list[dict[str, Any]],
    deadline_monotonic: float | None,
) -> tuple[dict[str, Any], bool]:
    """Blast-radius floor keyed on the capsule's SELECTED ``primary_target.symbol`` .

    ``_collect_capsule_call_site_evidence`` (agent_capsule.py:514) only collects call-site
    evidence when the query names the primary symbol AND its pre-cap confidence is >=0.75
    (agent_capsule.py:536,546) -- a natural-language task query fails that gate and leaves
    ``related_call_sites`` EMPTY even though the SELECTED primary target may have real callers.
    Reuse the capsule's own scan when it already ran (``status`` ``collected``/
    ``collected_no_call_sites``); otherwise fall to a supplementary blast-radius scan keyed on
    the symbol the capsule actually selected, not the raw query text.

    opt10 campaign ranked-queue item #2: that supplementary scan used to ALWAYS pay a second
    FS-backed ``build_repo_map`` walk+parse (via ``build_symbol_blast_radius``) even though
    ``_build_prepare_payload`` already built one moments earlier and now passes it in as ``rm``
    -- doubling prepare's dominant cost on every natural-language query (the common CUJ). Reuse
    ``rm`` via the map-reusing sibling ``build_symbol_blast_radius_from_map`` UNLESS ``rm``'s own
    truncation signal (``rm['scan_limit']['possibly_truncated']``, repo_map.py:6707-6715) says
    the capsule's ``DEFAULT_AGENT_REPO_MAP_LIMIT``-capped map may be missing files a full scan
    would find. THE LOAD-BEARING GUARD: the supplementary scan below is DELIBERATELY uncapped
    (``max_repo_files`` omitted, see that branch's own comment) for large-repo recall, while
    ``rm`` is capped at 2000 files -- blindly reusing a truncated ``rm`` would silently narrow
    blast-radius recall on a >2000-file repo, a real correctness regression, not just a missed
    speedup. When ``rm`` is complete (any repo at or under the cap, the common case), it already
    IS the same file universe an uncapped rescan would produce, so reuse is recall-identical, not
    just faster -- never stamps the rescue-less daemon ``TRAP A`` sentinel (see
    ``_collect_capsule_call_site_evidence_from_map``'s docstring) because a no_match on a
    ``possibly_truncated`` map is impossible on this branch by construction of the gate above it.

    Returns ``(floor, deadline_partial)``. ``deadline_partial`` is deliberately narrower than the
    floor's own ``possibly_incomplete`` field: it is True ONLY when a ``--deadline`` cutoff
    truncated the scan (folded into the caller's top-level ``partial``/exit-2 honesty gate,
    mirroring ``capsule.get("partial")``), never for a plain ``max_callers`` OUTPUT cap -- an
    output cap is a COMPLETE analysis capped only for display and must stay exit 0
    (``_scan_incomplete``'s own documented contract).
    """
    from tensor_grep.cli.agent_capsule import _related_call_site_record
    from tensor_grep.cli.repo_map import (
        _apply_blast_radius_output_limits,
        _copy_partial_signal,
        build_symbol_blast_radius,
        build_symbol_blast_radius_from_map,
    )

    symbol = str(target.get("symbol") or "")
    status = str(call_site_evidence.get("status") or "")

    def _floor(
        *,
        source: str,
        related: list[dict[str, Any]],
        graph_trust_summary: dict[str, Any],
        resolution_gaps: list[dict[str, Any]],
        deadline_partial: bool,
        omitted: int,
        error: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        top_callers = [
            {
                "file": record.get("file"),
                "line": record.get("line"),
                "symbol": record.get("symbol"),
                "provenance": record.get("provenance"),
            }
            for record in related
        ]
        floor: dict[str, Any] = {
            "symbol": symbol,
            "callers_count": len(top_callers),
            "top_callers": top_callers,
            "source": source,
            "graph_trust_summary": graph_trust_summary,
            "resolution_gaps": resolution_gaps,
            # A scan that RAISED did not complete, so "0 callers" is unknown, not a finding. It is
            # reported here (wider than `deadline_partial`, which stays deadline-only and so leaves
            # exit codes alone) instead of reading as "no callers, complete" with only an `error`
            # string to notice.
            "possibly_incomplete": bool(deadline_partial or omitted or error is not None),
        }
        if error is not None:
            floor["error"] = error
        return floor, bool(deadline_partial)

    def _floor_from_radius_payload(radius_payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        # Shared tail for BOTH the from-map reuse branch and the FS-backed fallback branch below
        # -- one definition so the two paths cannot silently drift apart on how a radius_payload
        # becomes a floor.
        if radius_payload.get("no_match"):
            return _floor(
                source="supplementary_blast_radius",
                related=[],
                graph_trust_summary=dict(radius_payload.get("graph_trust_summary") or {}),
                resolution_gaps=list(radius_payload.get("resolution_gaps") or []),
                deadline_partial=bool(radius_payload.get("partial")),
                omitted=0,
            )
        related = [
            record
            for record in (
                _related_call_site_record(caller, target_symbol=symbol)
                for caller in (radius_payload.get("callers") or [])
                if isinstance(caller, dict)
            )
            if record is not None
        ]
        output_limit = radius_payload.get("output_limit") or {}
        return _floor(
            source="supplementary_blast_radius",
            related=related,
            graph_trust_summary=dict(radius_payload.get("graph_trust_summary") or {}),
            resolution_gaps=list(radius_payload.get("resolution_gaps") or []),
            deadline_partial=bool(radius_payload.get("partial")),
            omitted=int(output_limit.get("omitted_callers") or 0),
        )

    if status in ("collected", "collected_no_call_sites"):
        return _floor(
            source="capsule_call_site_evidence",
            related=related_call_sites,
            graph_trust_summary=dict(call_site_evidence.get("graph_trust_summary") or {}),
            resolution_gaps=list(call_site_evidence.get("resolution_gaps") or []),
            deadline_partial=bool(call_site_evidence.get("partial")),
            omitted=int(call_site_evidence.get("omitted_call_sites") or 0),
        )

    if not symbol:
        # Degenerate capsule (no primary symbol resolved at all) -- nothing to scan.
        return _floor(
            source="no_primary_symbol",
            related=[],
            graph_trust_summary={},
            resolution_gaps=[],
            deadline_partial=False,
            omitted=0,
        )

    if status == "skipped" and call_site_evidence.get("reason") == (
        "primary symbol definition was not found by blast-radius"
    ):
        # The capsule's own rescue-equipped scan (agent_capsule.py's cold path always passes
        # `_rescue_call_site_evidence=True`) already ran and confirmed no_match for this exact
        # symbol -- a second identical FS scan would just repeat it. Report honestly without
        # paying the cost twice.
        return _floor(
            source="capsule_call_site_evidence",
            related=[],
            graph_trust_summary=dict(call_site_evidence.get("graph_trust_summary") or {}),
            resolution_gaps=list(call_site_evidence.get("resolution_gaps") or []),
            deadline_partial=bool(call_site_evidence.get("partial")),
            omitted=0,
        )

    scan_limit = rm.get("scan_limit")
    map_possibly_truncated = isinstance(scan_limit, dict) and bool(
        scan_limit.get("possibly_truncated")
    )

    if not map_possibly_truncated:
        # opt10 #2: `rm` is a COMPLETE map (the file-count cap never bit), so it already IS the
        # file universe an uncapped rescan would produce below -- reuse it via the map-reusing
        # sibling instead of paying a second build_repo_map walk+parse. Mirrors the established
        # from_map reuse idiom this codebase already uses elsewhere (agent_capsule._collect_
        # capsule_call_site_evidence_from_map; main.py's own blast-radius daemon gate): a
        # build_symbol_blast_radius_from_map call, then the SAME _apply_blast_radius_output_
        # limits + _copy_partial_signal tail build_symbol_blast_radius itself applies internally.
        try:
            radius_payload = build_symbol_blast_radius_from_map(
                rm,
                symbol,
                max_depth=1,
                deadline_monotonic=deadline_monotonic,
            )
            radius_payload = _apply_blast_radius_output_limits(
                radius_payload, max_callers=8, max_files=8
            )
            _copy_partial_signal(radius_payload, rm)
        except Exception as exc:  # pragma: no cover - defensive evidence side path
            return _floor(
                source="supplementary_blast_radius",
                related=[],
                graph_trust_summary={},
                resolution_gaps=[],
                deadline_partial=False,
                omitted=0,
                error=str(exc),
            )
        return _floor_from_radius_payload(radius_payload)

    # `rm` is possibly_truncated (a repo bigger than DEFAULT_AGENT_REPO_MAP_LIMIT): PRESERVE the
    # exact pre-#2 uncapped FS rescan below rather than reusing a map that may be missing the
    # very caller this floor exists to find (the #2 load-bearing guard).
    deadline_seconds_remaining: float | None = None
    if deadline_monotonic is not None:
        deadline_seconds_remaining = max(0.1, deadline_monotonic - time.monotonic())
    try:
        # `max_repo_files` is deliberately OMITTED (max recall within the shared deadline, not a
        # 2nd cap): this scan's only truncation source must stay the deadline, whose signal we
        # already read back below (`radius_payload.get("partial")` -> `deadline_partial` ->
        # top-level `partial`/exit-2). A file-count cap would truncate via `scan_limit` instead,
        # which this floor never inspects -- that truncation would be silently invisible in
        # `tg prepare`'s output. Do not "fix" this by adding a cap here.
        radius_payload = build_symbol_blast_radius(
            symbol,
            path,
            max_depth=1,
            max_callers=8,
            max_files=8,
            deadline_seconds=deadline_seconds_remaining,
        )
    except Exception as exc:  # pragma: no cover - defensive evidence side path
        return _floor(
            source="supplementary_blast_radius",
            related=[],
            graph_trust_summary={},
            resolution_gaps=[],
            deadline_partial=False,
            omitted=0,
            error=str(exc),
        )
    return _floor_from_radius_payload(radius_payload)


_larger_query_terms = _prepare_larger._larger_query_terms
_seed_larger_candidates = _prepare_larger._seed_larger_candidates
_expand_larger_ego_files = _prepare_larger._expand_larger_ego_files
_open_larger_generation = _prepare_larger._open_larger_generation
_build_larger_lazy_repo_map = _prepare_larger.build_larger_lazy_repo_map
_hydrate_warm_larger_repo_map = _prepare_larger.hydrate_warm_larger_repo_map
_scope_lazy_rm = _prepare_larger.scope_lazy_rm


def _build_prepare_payload(
    *,
    path: str,
    query: str,
    claim: bool,
    deadline_monotonic: float | None = None,
    include_next_action: bool = False,
    repo_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Thin composition : ONE repo-map build supplies primary target, confidence,
    ask-user, and validation verbatim; the only NEW scan is the blast-radius floor (see
    ``_build_prepare_blast_radius_floor``). No new ranking/scan logic lives here.

    opt10 campaign ranked-queue item #2: builds ``rm`` directly instead of calling
    ``build_agent_capsule`` -- byte-identical to that function's own 2-line body
    (agent_capsule.py:2724-2731: same ``DEFAULT_AGENT_REPO_MAP_LIMIT`` cap, since this command
    never overrides ``max_repo_files``; same ``deadline_monotonic``, already resolved by the
    caller) followed by ``build_agent_capsule_from_map(..., _rescue_call_site_evidence=True)`` --
    the exact cold-path value ``build_agent_capsule`` itself always passes internally. The
    returned ``capsule`` is therefore byte-identical to calling ``build_agent_capsule(query,
    path, deadline_monotonic=...)`` directly; this function additionally keeps its own ``rm``
    reference to hand to the blast-radius floor below, so the floor can reuse it instead of
    paying a second ``build_repo_map`` walk+parse for the common natural-language-query CUJ.
    ``build_agent_capsule``'s own public contract/return is completely untouched by this --
    `tg agent` / MCP / its existing tests keep calling it directly, exactly as before."""
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.agent_capsule import _command_ref, build_agent_capsule_from_map
    from tensor_grep.cli.symbols_cache import content_receipts_active

    max_repo_files = _repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    cached_floors: dict[str, dict[str, Any]] = {}
    cached_file_meta: dict[str, dict[str, Any]] | None = None
    cache_dirty = False
    can_use_warm_cache = repo_map is None and not content_receipts_active()
    warm_rm: dict[str, Any] | None = None
    pre_build_stats: (
        tuple[dict[str, tuple[int, int, int, str | None]], dict[str, dict[str, Any]]] | None
    ) = None

    if repo_map is not None:
        # P9 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 06): a caller that
        # already holds a freshness-checked map (session_prepare, via session_root's
        # `_snapshot_generation` identity) skips this function's own `build_repo_map` walk
        # entirely. `repo_map` is trusted as-is -- freshness/identity is the CALLER's
        # responsibility, never re-derived here.
        rm = repo_map
    else:
        if can_use_warm_cache:
            warm_rm, cached_floors, cached_file_meta, cache_dirty = _load_warm_prepare_cache(
                path, max_repo_files, deadline_monotonic
            )
        if warm_rm is not None:
            hydrated = _hydrate_warm_larger_repo_map(
                path, warm_rm, cached_file_meta, query, deadline_monotonic
            )
            if hydrated is None:
                warm_rm = None
            else:
                rm, cached_file_meta, hydrated_dirty = hydrated
                if hydrated_dirty:
                    cache_dirty = True
        if warm_rm is None:
            lazy_result = (
                _build_larger_lazy_repo_map(path, max_repo_files, query, deadline_monotonic)
                if can_use_warm_cache
                else None
            )
            if lazy_result is not None:
                rm, cached_file_meta, pre_build_stats = lazy_result
                cache_dirty = True
            else:
                if can_use_warm_cache:
                    pre_build_stats = _capture_pre_build_stats(
                        path, max_repo_files, deadline_monotonic
                    )
                rm = _repo_map.build_repo_map(
                    path,
                    max_repo_files=max_repo_files,
                    deadline_monotonic=deadline_monotonic,
                )
                cached_file_meta = None
                cache_dirty = True
        if (
            can_use_warm_cache
            and cache_dirty
            and not rm.get("partial")
            and not rm.get("unreadable_paths")
        ):
            _save_warm_prepare_cache(
                path,
                max_repo_files,
                rm,
                cached_floors,
                cached_file_meta,
                deadline_monotonic,
                pre_build_stats=pre_build_stats,
            )
            cache_dirty = False
    scoped_rm = _scope_lazy_rm(rm)
    capsule = build_agent_capsule_from_map(
        scoped_rm,
        query,
        deadline_monotonic=deadline_monotonic,
        _rescue_call_site_evidence=not bool(rm.get("lazy_larger") or warm_rm is not None),
    )

    target = dict(capsule.get("primary_target") or {})
    call_site_evidence = dict(capsule.get("call_site_evidence") or {})
    related_call_sites = list(capsule.get("related_call_sites") or [])
    resolved_path = str(capsule.get("path") or path)
    resolved_query = str(capsule.get("query") or query)

    ego_subgraph: dict[str, Any] | None = None
    manifest_fresh = False
    resolved_root_path = Path(resolved_path)
    if target.get("file") and resolved_root_path.is_dir():
        try:
            from tensor_grep.cli.incremental_graph_sync import extract_ego_subgraph
            from tensor_grep.cli.symbol_graph_index import (
                sync_incremental_ego_subgraph_from_paths,
            )
            from tensor_grep.cli.unified_cache import (
                build_source_address_contract,
                check_view_freshness,
                compute_view_artifact_fingerprint,
                load_unified_manifest,
                update_view_states,
            )

            seed_files: list[str] = [str(target["file"])]
            for alt in capsule.get("alternative_targets") or []:
                if isinstance(alt, dict) and alt.get("file"):
                    alt_file = str(alt["file"])
                    if alt_file not in seed_files:
                        seed_files.append(alt_file)
                        if len(seed_files) >= 3:
                            break

            edges_by_file: dict[str, list[tuple[str, str]]] = {}
            for entry in rm.get("imports") or []:
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("file") or "")
                raw_imps = entry.get("imports") or []
                if src and isinstance(raw_imps, list):
                    edges_by_file[src] = [(str(dst), "import") for dst in raw_imps if dst]
            for cs in related_call_sites:
                if isinstance(cs, dict) and cs.get("file"):
                    cs_file = str(cs["file"])
                    if cs_file != seed_files[0]:
                        edges_by_file.setdefault(cs_file, []).append((seed_files[0], "call"))
            for tf in rm.get("tests") or []:
                tf_str = str(tf)
                tf_stem = Path(tf_str).stem
                for sf in seed_files:
                    sf_stem = Path(sf).stem
                    if sf_stem and sf_stem in tf_stem and tf_str != sf:
                        edges_by_file.setdefault(sf, []).append((tf_str, "test"))
                        break

            repo_files = list(rm.get("related_paths") or rm.get("files") or seed_files)
            merkle_root, source_address = build_source_address_contract(
                resolved_root_path, repo_files
            )
            manifest = (
                load_unified_manifest(resolved_root_path, deadline_monotonic=deadline_monotonic)
                if can_use_warm_cache
                else None
            )
            view_fresh = check_view_freshness(manifest, merkle_root, ("symbols_v1", "graph_v1"))
            manifest_fresh = all(view_fresh.values())
            graph_view_ready = bool(
                manifest_fresh
                and isinstance(manifest, dict)
                and isinstance(manifest.get("views"), dict)
                and isinstance(manifest["views"].get("graph_v1"), dict)
                and manifest["views"]["graph_v1"].get("ready") is True
            )

            rm_context_digest = hashlib.blake2b(
                json.dumps(
                    {
                        "files": rm.get("files") or [],
                        "imports": rm.get("imports") or [],
                        "exports": rm.get("exports") or [],
                        "tests": rm.get("tests") or [],
                        "query": resolved_query,
                        "seed_files": seed_files,
                        "related_call_sites": related_call_sites,
                        "edges_by_file": {
                            src: sorted((dst, kind) for dst, kind in edges)
                            for src, edges in edges_by_file.items()
                        },
                        "merkle_root": merkle_root,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8"),
                digest_size=32,
            ).hexdigest()
            ego_file_universe = (
                list(rm.get("parsed_files") or seed_files)
                if rm.get("lazy_larger")
                else (rm.get("files") or seed_files)
            )
            ego_subgraph = sync_incremental_ego_subgraph_from_paths(
                resolved_root_path,
                ego_file_universe,
                lambda p, _c: edges_by_file.get(p, []),
                seed_files[0],
                max_hops=2,
                max_nodes=24,
                deadline_monotonic=deadline_monotonic,
                force_refresh=not graph_view_ready,
                context_digest=rm_context_digest,
            )
            if len(seed_files) > 1 and ego_subgraph is not None:
                merged_nodes = list(ego_subgraph.get("nodes") or [])
                seen_nodes = set(merged_nodes)
                merged_edges = list(ego_subgraph.get("edges") or [])
                seen_edges = {
                    (e.get("src"), e.get("dst"), e.get("kind"))
                    for e in merged_edges
                    if isinstance(e, dict)
                }
                for extra_seed in seed_files[1:]:
                    if len(merged_nodes) >= 24:
                        break
                    extra = extract_ego_subgraph(
                        resolved_root_path, extra_seed, max_hops=2, max_nodes=24
                    )
                    for node in extra.get("nodes") or []:
                        if node not in seen_nodes and len(merged_nodes) < 24:
                            seen_nodes.add(node)
                            merged_nodes.append(node)
                    for edge in extra.get("edges") or []:
                        if isinstance(edge, dict):
                            ekey = (edge.get("src"), edge.get("dst"), edge.get("kind"))
                            if ekey not in seen_edges:
                                seen_edges.add(ekey)
                                merged_edges.append(edge)
                ego_subgraph["nodes"] = merged_nodes
                ego_subgraph["edges"] = merged_edges
                ego_subgraph["seed_files"] = seed_files
            rm_truncated_for_manifest = bool(
                (rm.get("scan_limit") or {}).get("possibly_truncated")
            ) or bool(rm.get("possibly_truncated"))
            symbols_ready = (
                not bool(rm.get("partial"))
                and not bool(rm.get("lazy_larger"))
                and not bool(rm.get("unreadable_paths"))
                and not rm_truncated_for_manifest
            )
            graph_ready = (
                ego_subgraph is not None
                and not bool(rm.get("lazy_larger"))
                and int((ego_subgraph.get("sync") or {}).get("complete", 0)) == 1
            )
            if can_use_warm_cache:
                view_updates: dict[str, tuple[bool, int]] = {
                    "symbols_v1": (symbols_ready, 1),
                    "graph_v1": (graph_ready, 1),
                }
                other_view_fresh = check_view_freshness(
                    manifest, merkle_root, ("hybrid_v1", "index_v1")
                )
                loaded_views = (
                    manifest.get("views")
                    if isinstance(manifest, dict)
                    and manifest.get("merkle_root") == merkle_root
                    and isinstance(manifest.get("views"), dict)
                    else None
                )
                artifact_fingerprints: dict[str, str | None] = {}
                for vname in ("hybrid_v1", "index_v1"):
                    fp = compute_view_artifact_fingerprint(resolved_root_path, vname)
                    artifact_fingerprints[vname] = fp
                    if fp is None:
                        view_updates[vname] = (False, 1)
                    else:
                        prev_entry = loaded_views.get(vname) if loaded_views is not None else None
                        prev_fp = (
                            prev_entry.get("artifact_fingerprint")
                            if isinstance(prev_entry, dict)
                            else None
                        )
                        view_updates[vname] = (
                            bool(other_view_fresh.get(vname)) and prev_fp == fp,
                            1,
                        )

                views_changed = loaded_views is None or any(
                    not (
                        isinstance(loaded_views.get(vname), dict)
                        and loaded_views[vname].get("merkle_root") == merkle_root
                        and loaded_views[vname].get("ready") is vready
                        and loaded_views[vname].get("schema_version") == vschema
                        and (
                            vname not in artifact_fingerprints
                            or loaded_views[vname].get("artifact_fingerprint")
                            == artifact_fingerprints[vname]
                        )
                    )
                    for vname, (vready, vschema) in view_updates.items()
                )
                if not graph_view_ready or not graph_ready or not symbols_ready or views_changed:
                    update_view_states(
                        resolved_root_path,
                        merkle_root,
                        view_updates,
                        source_address=source_address,
                        deadline_monotonic=deadline_monotonic,
                        artifact_fingerprints=artifact_fingerprints,
                    )
        except (OSError, ValueError, sqlite3.Error):
            ego_subgraph = None

    symbol = str(target.get("symbol") or "")
    floor_cache_key = (
        f"{symbol}\0{target.get('file') or ''}\0{call_site_evidence.get('status') or ''}"
    )
    ego_sync = (ego_subgraph or {}).get("sync") or {}
    graph_unchanged = (
        ego_subgraph is not None
        and int(ego_sync.get("complete", 0)) == 1
        and int(ego_sync.get("updated", 1)) == 0
        and int(ego_sync.get("deleted", 1)) == 0
    )
    rm_truncated = bool((rm.get("scan_limit") or {}).get("possibly_truncated")) or bool(
        rm.get("possibly_truncated")
    )
    cached_floor_entry = (
        cached_floors.get(floor_cache_key)
        if can_use_warm_cache and not cache_dirty and not rm_truncated and graph_unchanged
        else None
    )
    if isinstance(cached_floor_entry, dict):
        floor = dict(cached_floor_entry)
        floor_deadline_partial = False
    else:
        floor, floor_deadline_partial = _build_prepare_blast_radius_floor(
            path=resolved_path,
            rm=scoped_rm,
            target=target,
            call_site_evidence=call_site_evidence,
            related_call_sites=related_call_sites,
            deadline_monotonic=deadline_monotonic,
        )
        if scoped_rm.get("lazy_larger_scoped"):
            floor["lazy_larger_scope"] = True
            floor["possibly_incomplete"] = True
        if (
            can_use_warm_cache
            and not floor_deadline_partial
            and not floor.get("possibly_incomplete")
        ):
            cached_floors[floor_cache_key] = dict(floor)
            cache_dirty = True

    if ego_subgraph is not None:
        floor["ego_nodes"] = list(ego_subgraph.get("nodes") or [])
        floor["ego_edges"] = list(ego_subgraph.get("edges") or [])

    if can_use_warm_cache and cache_dirty:
        _save_warm_prepare_cache(
            path,
            max_repo_files,
            rm,
            cached_floors,
            cached_file_meta,
            deadline_monotonic,
            pre_build_stats=pre_build_stats,
        )
    if (
        Path(resolved_path).is_dir()
        and not rm_truncated
        and not rm.get("lazy_larger")
        and not content_receipts_active()
    ):
        try:
            from tensor_grep.cli.tdad_kg_ast import ensure_tdad_dependency_map

            ensure_tdad_dependency_map(
                Path(resolved_path),
                repo_map_payload=scoped_rm,
                persist=True,
            )
        except (OSError, ValueError, sqlite3.Error):
            pass
    claim_argv: list[object] = ["tg", "ledger", "claim", resolved_path]
    if symbol:
        claim_argv += ["--symbol", symbol]
    claim_argv += ["--intent", "edit", "--json"]
    claim_ref = _command_ref(claim_argv)
    claim_hook: dict[str, Any] = {
        "command": claim_ref["command"],
        "argv": claim_ref["argv"],
        "submitted": False,
        "advisory": True,
    }

    # Task #306 W2: surface a LIVE FOREIGN CLAIM on the DEFAULT path.
    #
    # Before this, `prepare` learned about overlaps only when `--claim` was passed -- i.e. you
    # discovered another agent had claimed your target only by claiming it yourself. An agent
    # doing the ordinary read-only `tg prepare` to get edit-ready got no signal at all, which is
    # the coordination gap in miniature: the ledger held the answer and nothing asked it.
    #
    # REPORTS, NEVER REFUSES. The #306 verdict is STAY ADVISORY, and `docs/CONTRACTS.md:225` says
    # the ledger has "no enforcement mechanism of any kind". Turning this into an `ask_user` gate
    # would be enforcement without any of the safety machinery real locking needs (fencing tokens,
    # lease expiry handling) -- the plan's §5 "what NOT to build" names exactly that.
    #
    # `list_claims` is a pure read: no write lock, no writes, expired entries pruned for display
    # only (ledger_store.py:782). Failure is swallowed for the same reason the `--claim` path
    # swallows its own: an advisory hook must never fail prepare's primary read.
    try:
        from tensor_grep.cli import ledger_store as _ledger_store

        _self_agent = _ledger_store.resolve_agent_id(None)
        _live = _ledger_store.list_claims(resolved_path)
        _foreign = [
            {
                "agent_id": entry.get("agent_id"),
                "scope": entry.get("scope"),
                "symbols": entry.get("symbols") or [],
                "intent": entry.get("intent"),
                "expires_at": entry.get("expires_at"),
            }
            for entry in (_live.get("claims") or [])
            if entry.get("agent_id") and entry.get("agent_id") != _self_agent
        ]
    except Exception:
        # Additive-conditional: on failure the key is simply absent, exactly as it is when no
        # foreign claim exists. A reader must not be able to distinguish "nothing claimed" from
        # "the probe broke" by the presence of an empty list -- so neither emits one.
        _foreign = []
    if _foreign:
        claim_hook["foreign_claims"] = _foreign
        claim_hook["foreign_claim_count"] = len(_foreign)

    if claim:
        if not symbol:
            claim_hook["error"] = "no primary symbol resolved; nothing to claim"
        else:
            from tensor_grep.cli import ledger_store

            try:
                submitted = ledger_store.submit_claim(
                    resolved_path, symbols=[symbol], intent="edit"
                )
            except Exception as exc:
                # Advisory coordination hook: a claim failure must never fail prepare's primary
                # read (mirrors ledger_store.submit_claim's own "NEVER blocks" contract one level
                # up -- see ledger_store.py:591-597, submit_claim's docstring).
                claim_hook["error"] = str(exc)
            else:
                claim_hook["submitted"] = True
                claim_hook["result"] = {
                    "claim": submitted["claim"],
                    "overlaps": submitted["overlaps"],
                }
                # v1.92.1 dogfood item 6: `ledger_store.resolve_agent_id` falls back to the
                # literal "anonymous" (CONTRACTS.md section 9 "agent_id resolution"; also
                # ledger_store._DEFAULT_AGENT_ID, not imported here to stay decoupled from that
                # module's private surface) whenever neither TG_LEDGER_AGENT_ID nor
                # TG_EVIDENCE_AGENT_ID is set -- `prepare` has no --agent-id flag of its own, so
                # that env-var pair is the only way a caller sets identity through this path.
                # Read the ACTUAL recorded value back off the submitted claim (never re-derive
                # the resolution rule here) so this hint can never drift from what was really
                # written to the ledger. Additive-only field, present only in the anonymous
                # case -- mirrors the conditional install_hint/autostart precedent elsewhere in
                # this module.
                if str(submitted["claim"].get("agent_id") or "") == "anonymous":
                    # Strengthened from "set TG_LEDGER_AGENT_ID for a stable identity", which four
                    # consecutive dogfoods reported as present-but-easy-to-miss. It now says what
                    # the caller LOSES, not just what to set: attribution. Machine-branchable
                    # sibling field so a harness can gate on it without string-matching prose.
                    claim_hook["agent_id_hint"] = (
                        "this claim is filed as 'anonymous' and is NOT attributable to you. Set "
                        "TG_LEDGER_AGENT_ID (or TG_EVIDENCE_AGENT_ID) to a stable per-agent value "
                        "so other agents can see WHO holds it."
                    )
                    claim_hook["agent_id_is_anonymous"] = True

    evidence_ref = _command_ref([
        "tg",
        "evidence",
        "emit",
        resolved_path,
        "--capsule",
        "<capsule.json>",
        "--query",
        resolved_query,
        "--json",
    ])
    evidence_hook = {
        "command": evidence_ref["command"],
        "argv": evidence_ref["argv"],
        "note": (
            "tg prepare does not persist a capsule file. Save this payload's JSON to disk (e.g. "
            "capsule.json), then run the command above with --capsule pointing at it to attach "
            "blast-radius/confidence evidence to a signed receipt."
        ),
    }

    result: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "prepare",
        "prepare_version": 1,
        "path": resolved_path,
        "query": resolved_query,
        "primary_target": target,
        "alternative_targets": capsule.get("alternative_targets", []),
        "confidence": capsule.get("confidence", {}),
        "ask_user_before_editing": capsule.get(
            "ask_user_before_editing", {"required": False, "reasons": []}
        ),
        "validation_plan": capsule.get("validation_plan", []),
        "validation_commands": capsule.get("validation_commands", []),
        "context_consistency": capsule.get("context_consistency", {}),
        "rollback": capsule.get("rollback", {}),
        "blast_radius_floor": floor,
        "coordination": {"claim": claim_hook, "evidence": evidence_hook},
    }
    _repo_map._copy_scan_limit(result, capsule)

    if include_next_action:
        target_path = str(target.get("file") or path)
        # AGT-03 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 03): this used to
        # hardcode `uv run pytest -q` for EVERY repository regardless of detected language, so a
        # Rust-only repo got Python test advice. Only emit a runnable `on_success` when
        # `validation_commands` (detected by the capsule builder from the actual repo, never from
        # unreviewed shell text) contains one of the allowlisted exact recipes; otherwise advise
        # inspection instead of inventing a successful plan. Built via the typed
        # `ValidationAdvice` record (prepare_protocol.py) so detected-recipe IDENTITY and
        # execution AUTHORITY stay decoupled internally; projected back to the exact same
        # `on_success` dict shape this function has always returned.
        advice = _prepare_protocol.build_validation_advice(result["validation_commands"])
        if advice.status == "available":
            on_success: dict[str, Any] = {
                "type": "run",
                "argv": list(advice.argv),
                "deadline_seconds": 300,
                "max_output_bytes": 1000000,
                "allow_network": False,
                "fail_closed_on_timeout": True,
            }
        else:
            on_success = {
                "type": "unavailable",
                "reason": advice.reason,
            }
        result["next_action"] = {
            "action": {
                "type": "edit_file",
                "path": target_path,
                "span": {
                    "start_line": target.get("line"),
                    "end_line": target.get("end_line", target.get("line")),
                },
                "instruction": f"Edit {target.get('symbol') or target_path} to address: {query}",
                "stop_if": ["ask_user_before_editing.required", "result_incomplete"],
            },
            "on_success": on_success,
            "on_failure": {
                "type": "narrow_scope",
                "suggested_path": str(Path(target_path).parent).replace("\\", "/"),
            },
        }

    capsule_partial = bool(capsule.get("partial"))
    if capsule_partial or floor_deadline_partial:
        result["partial"] = True
        result["partial_reason"] = "deadline"
        result["deadline_limit"] = {
            "deadline_exceeded": True,
            "capsule": capsule_partial,
            "blast_radius_floor": floor_deadline_partial,
        }
    return result


@dataclass(frozen=True)
class PrepareSnapshotV1:
    """Strict typed projection of a `_build_prepare_payload` result (Task 8 Step 2).

    Additive-only: every field here is read from the existing untyped payload dict via
    `.get(...)`, never computed independently, so this type can never disagree with what
    `tg prepare` itself returns for the same call. `raw` retains the complete untyped payload
    for any caller that needs a field not yet promoted to a named attribute -- promoting a new
    field later is a pure addition to this dataclass, not a behavior change to `raw` or to
    `_build_prepare_payload`.

    This type has no CLI/MCP consumer yet (Task 8's `tg edit-ready` command is out of scope for
    this slice) -- it exists so that future composition work has a typed contract to build
    against instead of re-deriving field names from the dict shape by hand.
    """

    version: int
    path: str
    query: str
    primary_target: dict[str, Any]
    confidence: dict[str, Any]
    blast_radius_floor: dict[str, Any]
    coordination: dict[str, Any]
    partial: bool
    partial_reason: str | None
    raw: dict[str, Any] = field(repr=False)


def build_prepare_snapshot(
    *,
    path: str,
    query: str,
    claim: bool = False,
    deadline_monotonic: float | None = None,
    include_next_action: bool = False,
    repo_map: dict[str, Any] | None = None,
    _input_validator: Callable[[], None] | None = None,
) -> PrepareSnapshotV1:
    """Build a `PrepareSnapshotV1` by calling the existing `_build_prepare_payload` and
    projecting its result onto typed fields. Delegates every computation to that function --
    this wrapper performs no scanning or ranking of its own, so it carries none of
    `_build_prepare_payload`'s side effects beyond what that function already does (including,
    when `claim=True`, the same advisory ledger claim submission `_build_prepare_payload`
    already performs)."""
    payload = _build_prepare_payload(
        path=path,
        query=query,
        claim=claim,
        deadline_monotonic=deadline_monotonic,
        include_next_action=include_next_action,
        repo_map=repo_map,
    )
    if _input_validator is not None:
        _input_validator()
    return PrepareSnapshotV1(
        version=int(payload.get("version") or 1),
        path=str(payload.get("path") or path),
        query=str(payload.get("query") or query),
        primary_target=dict(payload.get("primary_target") or {}),
        confidence=dict(payload.get("confidence") or {}),
        blast_radius_floor=dict(payload.get("blast_radius_floor") or {}),
        coordination=dict(payload.get("coordination") or {}),
        partial=bool(payload.get("partial", False)),
        partial_reason=payload.get("partial_reason"),
        raw=payload,
    )
