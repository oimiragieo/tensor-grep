"""Fix B: memoize Path.resolve() on the JS/TS import-resolution hot path.

Profiled on a real repo (build_symbol_callers("QueryEngine", <big TS repo>)): caller_scan spent
~18s of ~22s wall time inside Path.resolve() -- 27,669 resolve() calls / 83,114
nt._getfinalpathname syscalls -- because _js_ts_module_candidates / _js_ts_candidate_files /
_js_ts_resolve_exported_symbol / _js_ts_import_match_details / _normalized_repo_root each
re-resolve the SAME handful of path strings (the constant repo_root, the constant
definition_path, the same importer/module pairs) once per (candidate file, definition file)
pair, even though Fix A's per-(file, definition) memoization of
_file_imports_symbol_from_definition does not collapse those repeats (different definition_path
values are different cache keys there, even when the underlying path STRINGS being resolved are
identical).

Path.resolve() is a pure function of the path string for the lifetime of a resolution, so it is
memoized directly via ``_resolved_path_str`` (a plain ``@lru_cache``, registered into the same
``_MTIME_CACHE_CLEAR_REGISTRY`` / ``_clear_all_source_caches()`` sweep Fix A introduced, so a
daemon session refresh flushes it too -- a moved file or retargeted symlink mid-session must not
serve a stale resolution).

This suite proves:
  - identical results with the cache warm vs cold (both across repeat calls to
    build_symbol_callers/build_symbol_refs, and directly against Path.resolve() including a
    symlink case);
  - the cache is actually exercised (hits > 0) by both a direct call and a real
    build_symbol_callers run over a small multi-file TS repo with cross-file imports;
  - daemon safety: _clear_all_source_caches() empties _resolved_path_str's cache.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_ts_repo(root: Path) -> dict[str, Path]:
    """A small TS repo with a named export imported (and re-exported) across several files."""
    engine = _write(
        root / "src" / "engine.ts",
        "export class QueryEngine {\n  run() { return 1; }\n}\n",
    )
    # Direct relative import.
    caller_a = _write(
        root / "src" / "caller_a.ts",
        'import { QueryEngine } from "./engine";\n\n'
        "export function useA() {\n"
        "  return new QueryEngine();\n"
        "}\n",
    )
    # A second, independent importer of the same symbol -- forces the JS/TS resolution path to
    # re-derive candidates for the SAME (importer, module) pair as caller_a, and to re-resolve
    # the SAME definition_path string, on every outer iteration.
    caller_b = _write(
        root / "src" / "caller_b.ts",
        'import { QueryEngine } from "./engine";\n\n'
        "export function useB() {\n"
        "  return new QueryEngine();\n"
        "}\n",
    )
    non_caller = _write(
        root / "src" / "unrelated.ts",
        "export function formatLabel(id: string) {\n  return `item-${id}`;\n}\n",
    )
    return {
        "root": root,
        "engine": engine,
        "caller_a": caller_a,
        "caller_b": caller_b,
        "unrelated": non_caller,
    }


def _caller_files(result: dict[str, object]) -> set[str]:
    callers = result.get("callers")
    assert isinstance(callers, list)
    return {str(entry["file"]).replace("\\", "/") for entry in callers}  # type: ignore[index]


# ---------------------------------------------------------------------------
# Correctness: identical results with the cache warm vs cold.
# ---------------------------------------------------------------------------


def test_repeated_build_symbol_callers_ts_returns_identical_results(tmp_path: Path) -> None:
    paths = _build_ts_repo(tmp_path)
    root = paths["root"]

    first = repo_map.build_symbol_callers("QueryEngine", str(root))
    second = repo_map.build_symbol_callers("QueryEngine", str(root))

    assert first["callers"] == second["callers"]
    caller_files = _caller_files(first)
    assert any(name.endswith("src/caller_a.ts") for name in caller_files)
    assert any(name.endswith("src/caller_b.ts") for name in caller_files)
    assert not any(name.endswith("src/unrelated.ts") for name in caller_files)


def test_repeated_build_symbol_refs_ts_returns_identical_results(tmp_path: Path) -> None:
    paths = _build_ts_repo(tmp_path)
    root = paths["root"]

    first = repo_map.build_symbol_refs("QueryEngine", str(root))
    second = repo_map.build_symbol_refs("QueryEngine", str(root))

    assert first["references"] == second["references"]
    ref_files = {
        str(entry["file"]).replace("\\", "/")
        for entry in first["references"]  # type: ignore[index]
    }
    assert any(name.endswith("src/caller_a.ts") for name in ref_files)
    assert any(name.endswith("src/caller_b.ts") for name in ref_files)


def test_resolved_path_str_matches_path_resolve_including_symlink(tmp_path: Path) -> None:
    """The cached helper must return byte-identical results to an uncached Path.resolve() --
    including through a symlink, where a wrong cache would silently serve the WRONG canonical
    path (a real-world hazard: a moved file / retargeted symlink)."""
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    real_file = _write(real_dir / "mod.ts", "export const x = 1;\n")

    link_dir = tmp_path / "link_dir"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")

    linked_path = link_dir / "mod.ts"
    repo_map._resolved_path_str.cache_clear()

    cached = repo_map._resolved_path_str(str(linked_path))
    direct = str(linked_path.resolve())
    assert cached == direct
    assert cached == str(real_file.resolve())


# ---------------------------------------------------------------------------
# The cache is actually exercised.
# ---------------------------------------------------------------------------


def test_resolved_path_str_actually_caches(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.ts"
    _write(target, "export const y = 1;\n")
    repo_map._resolved_path_str.cache_clear()

    path_str = str(target)
    first = repo_map._resolved_path_str(path_str)
    second = repo_map._resolved_path_str(path_str)

    assert first == second
    info = repo_map._resolved_path_str.cache_info()
    assert info.hits > 0, "second call with the same path string must be served from cache"


def test_build_symbol_callers_ts_exercises_resolved_path_str_cache(tmp_path: Path) -> None:
    """caller_scan re-derives candidates for the SAME (importer, module) pair and re-resolves
    the SAME definition_path across every candidate file it inspects -- prove the shared cache
    actually absorbs those repeats on a real (small) run, not just in a synthetic direct-call
    test."""
    paths = _build_ts_repo(tmp_path)
    root = paths["root"]
    repo_map._resolved_path_str.cache_clear()

    result = repo_map.build_symbol_callers("QueryEngine", str(root))

    assert len(_caller_files(result)) == 2
    info = repo_map._resolved_path_str.cache_info()
    assert info.hits > 0, (
        "expected repeated resolve() calls across caller_a.ts/caller_b.ts to hit the cache"
    )


# ---------------------------------------------------------------------------
# Daemon safety (Guard 3): a session refresh must flush this cache too.
# ---------------------------------------------------------------------------


def test_clear_all_source_caches_empties_resolved_path_str(tmp_path: Path) -> None:
    target = tmp_path / "c.ts"
    _write(target, "export const z = 1;\n")

    repo_map._resolved_path_str(str(target))
    assert repo_map._resolved_path_str.cache_info().currsize > 0

    repo_map._clear_all_source_caches()

    assert repo_map._resolved_path_str.cache_info().currsize == 0
