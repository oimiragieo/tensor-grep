"""LARGER-style lazy lexical seeding and 1-2 hop ego-subgraph hydration for `tg prepare`."""

import hashlib
import os
import re
import subprocess
import time
from collections import deque
from functools import partial
from pathlib import Path
from typing import Any

_LARGER_LAZY_MIN_FILES = 64
_LARGER_SEED_CAP = 16
_LARGER_EGO_MAX_FILES = 48
_LARGER_SEARCH_TIMEOUT_S = 1.5
_PROJECT_CONFIG_FILES = (
    "pyproject.toml",
    "tsconfig.json",
    "Cargo.toml",
    "package.json",
    "go.mod",
)


def build_config_meta(
    context_root: Path, cache_root: Path, max_bytes: int
) -> dict[str, dict[str, Any]]:
    from tensor_grep.cli import prepare_service as _prepare_service
    from tensor_grep.io.confined import read_confined

    roots = [context_root]
    if cache_root != context_root:
        roots.append(cache_root)
    meta: dict[str, dict[str, Any]] = {}
    for base in roots:
        for cfg_name in _PROJECT_CONFIG_FILES:
            cfg_path = base / cfg_name
            stat_key = _prepare_service._lstat_regular_file(cfg_path)
            if stat_key is None:
                continue
            key = str(cfg_path)
            if stat_key[2] > max_bytes:
                meta[key] = {
                    "mtime_ns": stat_key[0],
                    "ctime_ns": stat_key[1],
                    "size": stat_key[2],
                    "oversized": True,
                }
                continue
            try:
                content = read_confined(cache_root, cfg_path, max_bytes)
                digest = hashlib.sha256(content).hexdigest()
            except OSError:
                meta[key] = {"unreadable": True}
                continue
            meta[key] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "sha256": digest,
            }
    return meta


def _larger_query_terms(query: str) -> list[str]:
    from tensor_grep.cli import repo_map as _repo_map

    raw = _repo_map._symbol_query_terms(query) or _repo_map._query_terms(query)
    terms = [t for t in raw if len(t) >= 2] or re.findall(r"[A-Za-z0-9_]+", query.lower())
    return list(dict.fromkeys(terms))[:8]


def _seed_larger_candidates(
    root: Path,
    query: str,
    context_files: list[Path],
    *,
    generation: Any = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[Path], bool]:
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.frontdoor_hops import probe_env
    from tensor_grep.cli.runtime_paths import resolve_native_tg_binary, resolve_ripgrep_binary

    terms = _larger_query_terms(query)
    norm_to_path = {os.path.normcase(os.path.abspath(str(p))): p for p in context_files}
    lexical_hits: set[str] = set()
    symbol_hits: set[str] = set()
    lexical_ok = not terms

    if terms:
        timeout_s = _LARGER_SEARCH_TIMEOUT_S
        if deadline_monotonic is not None:
            timeout_s = min(timeout_s, max(0.1, deadline_monotonic - time.monotonic()))
        pattern = "|".join(re.escape(t) for t in terms)
        native_tg = resolve_native_tg_binary()
        rg_bin = resolve_ripgrep_binary()
        candidates: list[tuple[list[str], dict[str, str] | None, str | None]] = []
        if native_tg is not None and native_tg.is_file():
            candidates.append((
                [str(native_tg), "search", "-i", "-l", pattern, str(root)],
                probe_env(),
                "tg",
            ))
        if rg_bin is not None and rg_bin.is_file():
            candidates.append((
                [str(rg_bin), "-i", "-l", "--no-messages", pattern, str(root)],
                None,
                "rg",
            ))
        active_kind: str | None = None
        active_bin: Path | None = None
        active_env: dict[str, str] | None = None
        for cmd, child_env, kind in candidates:
            try:
                proc = subprocess.run(
                    cmd,
                    env=child_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                )
                if proc.returncode in (0, 1):
                    lexical_ok = True
                    active_kind = kind
                    active_bin = native_tg if kind == "tg" else rg_bin
                    active_env = child_env
                    for line in proc.stdout.splitlines():
                        c = line.strip()
                        if c:
                            fp = Path(c) if Path(c).is_absolute() else root / c
                            key = os.path.normcase(os.path.abspath(str(fp)))
                            if key in norm_to_path:
                                lexical_hits.add(key)
                    break
            except (OSError, subprocess.SubprocessError):
                pass
        if not lexical_ok:
            # Pure-Python bounded fallback when native tg / rg binaries are absent,
            # cache-invalidated, or timed out under heavy CI load.
            term_re = re.compile(pattern, re.IGNORECASE)
            def_re = re.compile(
                rf"\b(?:def|class|fn|func|function|struct|enum|trait|interface|type)\s+(?:{pattern})\b",
                re.IGNORECASE,
            )
            try:
                for fp in context_files:
                    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                        break
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    if term_re.search(text):
                        key = os.path.normcase(os.path.abspath(str(fp)))
                        lexical_hits.add(key)
                        if def_re.search(text):
                            symbol_hits.add(key)
                lexical_ok = True
            except OSError:
                lexical_ok = False
        elif active_bin is not None:
            def_pat = rf"\b(?:def|class|fn|func|function|struct|enum|trait|interface|type)\s+(?:{pattern})\b"
            def_cmd = (
                [str(active_bin), "search", "-i", "-l", def_pat, str(root)]
                if active_kind == "tg"
                else [str(active_bin), "-i", "-l", "--no-messages", def_pat, str(root)]
            )
            try:
                def_proc = subprocess.run(
                    def_cmd,
                    env=active_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                )
                if def_proc.returncode in (0, 1):
                    for line in def_proc.stdout.splitlines():
                        c = line.strip()
                        if c:
                            fp = Path(c) if Path(c).is_absolute() else root / c
                            key = os.path.normcase(os.path.abspath(str(fp)))
                            if key in norm_to_path:
                                symbol_hits.add(key)
            except (OSError, subprocess.SubprocessError):
                pass

    if len(lexical_hits) > _LARGER_SEED_CAP and not symbol_hits:
        lexical_ok = False

    wants_test = "test" in terms or "tests" in terms
    pool = (
        [norm_to_path[k] for k in (lexical_hits | symbol_hits)]
        if (lexical_hits or symbol_hits)
        else context_files
    )
    scored = sorted(
        pool,
        key=lambda fp: (
            -(1 if os.path.normcase(os.path.abspath(str(fp))) in symbol_hits else 0),
            -(1 if os.path.normcase(os.path.abspath(str(fp))) in lexical_hits else 0),
            -(
                sum(1 for t in terms if t in fp.stem.lower()) * 2
                + sum(1 for t in terms if t in str(fp).lower())
            ),
            0 if (wants_test or not _repo_map._is_test_file(fp)) else 1,
            str(fp),
        ),
    )
    return scored[:_LARGER_SEED_CAP], lexical_ok


def _expand_larger_ego_files(
    *,
    cache_root: Path,
    seed_files: list[Path],
    context_files: list[Path],
    tests: list[Path],
    generation: Any,
    max_parse_bytes: int,
    deadline_monotonic: float | None = None,
    already_parsed: set[str] | None = None,
) -> tuple[
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, tuple[int, int, int, str | None]],
]:
    from tensor_grep.cli import prepare_service as _prepare_service
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.io.confined import read_confined

    stem_index: dict[str, list[Path]] = {}
    norm_to_path: dict[str, Path] = {}
    for fp in context_files:
        stem_index.setdefault(fp.stem.lower(), []).append(fp)
        norm_to_path[os.path.normcase(os.path.abspath(str(fp)))] = fp
    test_by_stem = [(t, t.stem.lower()) for t in tests]
    queue: deque[tuple[Path, int]] = deque((fp, 0) for fp in seed_files)

    seen_norm: set[str] = set()
    parsed_paths, symbols, imports = [], [], []
    parsed_meta: dict[str, dict[str, Any]] = {}
    pre_stats: dict[str, tuple[int, int, int, str | None]] = {}
    skip_parsed = already_parsed or set()

    while queue and len(seen_norm) < _LARGER_EGO_MAX_FILES:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        current, hop = queue.popleft()
        norm_key = os.path.normcase(os.path.abspath(str(current)))
        if norm_key in seen_norm:
            continue
        seen_norm.add(norm_key)
        p_str = str(current)
        if p_str in skip_parsed:
            continue
        stat_key = _prepare_service._lstat_regular_file(current)
        if stat_key is None:
            continue
        if stat_key[2] > max_parse_bytes:
            parsed_meta[p_str] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "oversized": True,
            }
            pre_stats[p_str] = (stat_key[0], stat_key[1], stat_key[2], None)
            continue
        try:
            pre_digest = hashlib.sha256(
                read_confined(cache_root, current, max_parse_bytes)
            ).hexdigest()
            cur_imports, cur_symbols = generation.product(
                current, partial(_repo_map._imports_and_symbols_for_path, current)
            )
            if (
                _prepare_service._lstat_regular_file(current) != stat_key
                or hashlib.sha256(read_confined(cache_root, current, max_parse_bytes)).hexdigest()
                != pre_digest
            ):
                continue
        except OSError:
            continue
        parsed_paths.append(p_str)
        parsed_meta[p_str] = {
            "mtime_ns": stat_key[0],
            "ctime_ns": stat_key[1],
            "size": stat_key[2],
            "sha256": pre_digest,
        }
        pre_stats[p_str] = (stat_key[0], stat_key[1], stat_key[2], pre_digest)
        if cur_imports:
            imports.append({
                "file": p_str,
                "imports": cur_imports,
                "provenance": _repo_map._symbol_navigation_provenance_for_path(p_str),
            })
        symbols.extend(cur_symbols)
        if hop < 2:
            for imp in cur_imports:
                tail = re.split(r"[./:\\]+", str(imp).strip())[-1].lower()
                for neighbor in stem_index.get(tail, [])[:4] if tail else ():
                    queue.append((neighbor, hop + 1))
            if hop == 0 and current.stem:
                for test_fp, test_stem in test_by_stem:
                    if current.stem.lower() in test_stem and test_fp != current:
                        queue.append((test_fp, 1))
                        if len(queue) >= _LARGER_EGO_MAX_FILES:
                            break
    return parsed_paths, symbols, imports, parsed_meta, pre_stats


def _open_larger_generation(root: Path, deadline_monotonic: float | None) -> tuple[Path, int, Any]:
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.repo_map_fast_cache import canonical_symbol_cache_root
    from tensor_grep.cli.symbols_cache import SymbolGeneration, authenticated_content_root

    cache_root = canonical_symbol_cache_root(
        root, _repo_map._infer_project_root, authenticated_content_root()
    )
    max_parse_bytes = _repo_map._max_parse_bytes()
    generation = SymbolGeneration(
        cache_root,
        scope=root,
        max_bytes=max_parse_bytes,
        ignore_policy=sorted(_repo_map._SKIP_DIR_NAMES),
        deadline=deadline_monotonic,
    )
    return cache_root, max_parse_bytes, generation


def build_larger_lazy_repo_map(
    path: str, max_repo_files: int, query: str, deadline_monotonic: float | None
) -> (
    tuple[
        dict[str, Any],
        dict[str, dict[str, Any]],
        tuple[dict[str, tuple[int, int, int, str | None]], dict[str, dict[str, Any]]],
    ]
    | None
):
    from tensor_grep.cli import prepare_service as _prepare_service
    from tensor_grep.cli import repo_map as _repo_map
    from tensor_grep.cli.incompleteness import budget_remediable

    root = _repo_map._resolved_input_path(path)
    if not _repo_map._path_exists_with_receipt(root) or not _repo_map._is_dir_with_receipt(root):
        return None
    _repo_map._clear_all_source_caches()
    _repo_map._load_gitignore_matcher.cache_clear()
    _repo_map._prime_all_language_repo_contexts(root)
    walk_deadline, walk_unreadable = _repo_map._DeadlineBreakFlag(), _repo_map._UnreadablePathFlag()
    all_files = _repo_map._iter_repo_files(
        root,
        max_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=walk_deadline,
        unreadable_hit=walk_unreadable,
    )
    if walk_deadline.hit or walk_unreadable.hit:
        return None
    context_files = [c for c in all_files if _repo_map._is_repo_context_file(c, root)]
    if len(context_files) <= _LARGER_LAZY_MIN_FILES:
        return None

    cache_root, max_parse_bytes, generation = _open_larger_generation(root, deadline_monotonic)
    config_meta = build_config_meta(root, cache_root, max_parse_bytes)
    test_paths = [c for c in context_files if _repo_map._is_test_file(c)]
    tests = [str(c) for c in test_paths]
    source_files = [str(c) for c in context_files if not _repo_map._is_test_file(c)] or tests
    seed_files, lexical_ok = _seed_larger_candidates(
        root, query, context_files, generation=generation, deadline_monotonic=deadline_monotonic
    )
    if not lexical_ok:
        if generation.connection is not None:
            generation.connection.close()
        return None
    parsed_paths, symbols, imports, parsed_meta, pre_stats = _expand_larger_ego_files(
        cache_root=cache_root,
        seed_files=seed_files,
        context_files=context_files,
        tests=test_paths,
        generation=generation,
        max_parse_bytes=max_parse_bytes,
        deadline_monotonic=deadline_monotonic,
    )
    file_meta = dict(parsed_meta)
    for current in context_files:
        p_str = str(current)
        if p_str not in file_meta:
            stat_key = _prepare_service._lstat_regular_file(current)
            if stat_key is None:
                return None
            file_meta[p_str] = {
                "mtime_ns": stat_key[0],
                "ctime_ns": stat_key[1],
                "size": stat_key[2],
                "unparsed": True,
            }
            pre_stats[p_str] = (stat_key[0], stat_key[1], stat_key[2], None)

    capped_count = len(all_files)
    _capped = capped_count >= max_repo_files
    _cause = (
        _repo_map._scan_limit_cause(all_files, root, capped_count, max_repo_files)
        if _capped
        else "project-files"
    )
    _truncated = _capped and _cause == "project-files"
    rm = _repo_map._envelope(root)
    rm.update({
        "files": source_files,
        "symbols": symbols,
        "imports": imports,
        "tests": tests,
        "related_paths": sorted(dict.fromkeys([*source_files, *tests])),
        "lazy_larger": True,
        "parsed_files": sorted(dict.fromkeys(parsed_paths)),
        "scan_limit": {
            "max_repo_files": max_repo_files,
            "scanned_files": capped_count,
            "possibly_truncated": _truncated,
            "truncation_cause": _cause if _capped else None,
            **({"budget_remediable": budget_remediable(_cause)} if _capped else {}),
        },
        "scan_remediation": _repo_map._SCAN_LIMIT_TRUNCATED_REMEDIATION if _truncated else None,
    })
    generation.finish(rm, complete=False)
    return rm, file_meta, (pre_stats, config_meta)


def hydrate_warm_larger_repo_map(
    path: str,
    rm: dict[str, Any],
    file_meta: dict[str, dict[str, Any]] | None,
    query: str,
    deadline_monotonic: float | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]] | None, bool] | None:
    from tensor_grep.cli import repo_map as _repo_map

    if not rm.get("lazy_larger"):
        return rm, file_meta, False
    root = _repo_map._resolved_input_path(path)
    if not _repo_map._is_dir_with_receipt(root):
        return rm, file_meta, False
    already_parsed = {str(p) for p in (rm.get("parsed_files") or [])}
    context_files = [Path(p) for p in (rm.get("related_paths") or [])]
    test_paths = [Path(p) for p in (rm.get("tests") or [])]
    cache_root, max_parse_bytes, generation = _open_larger_generation(root, deadline_monotonic)
    seed_files, lexical_ok = _seed_larger_candidates(
        root, query, context_files, generation=generation, deadline_monotonic=deadline_monotonic
    )
    if not lexical_ok:
        if generation.connection is not None:
            generation.connection.close()
        return None
    if not any(str(sf) not in already_parsed for sf in seed_files):
        if generation.connection is not None:
            generation.connection.close()
        return rm, file_meta, False
    parsed_paths, new_symbols, new_imports, parsed_meta, _ = _expand_larger_ego_files(
        cache_root=cache_root,
        seed_files=seed_files,
        context_files=context_files,
        tests=test_paths,
        generation=generation,
        max_parse_bytes=max_parse_bytes,
        deadline_monotonic=deadline_monotonic,
        already_parsed=already_parsed,
    )
    if not parsed_paths:
        if generation.connection is not None:
            generation.connection.close()
        return rm, file_meta, False
    updated_rm = {
        **rm,
        "symbols": [*(rm.get("symbols") or []), *new_symbols],
        "imports": [*(rm.get("imports") or []), *new_imports],
        "parsed_files": sorted(already_parsed | set(parsed_paths)),
    }
    updated_meta = {**(file_meta or {}), **parsed_meta}
    generation.finish(updated_rm, complete=False)
    return updated_rm, updated_meta, True


def scope_lazy_rm(rm: dict[str, Any]) -> dict[str, Any]:
    if not rm.get("lazy_larger"):
        return rm
    parsed_set = set(rm.get("parsed_files") or [])
    if not parsed_set:
        return rm
    return {
        **rm,
        "lazy_larger_scoped": True,
        "files": [p for p in (rm.get("files") or []) if p in parsed_set] or sorted(parsed_set),
        "tests": [p for p in (rm.get("tests") or []) if p in parsed_set],
        "related_paths": sorted(parsed_set),
    }
