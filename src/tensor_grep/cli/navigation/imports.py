from __future__ import annotations

from typing import Any

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_fast_cache as _fast_cache
from tensor_grep.cli import repo_map_graph as _repo_map_graph


def _infer_project_root(file_path: _repo_map.Path) -> _repo_map.Path:
    """Walk upward from a file looking for a project-root marker; falls back to its own dir.

    `tg imports FILE` takes no ROOT argument (by design -- it is meant to be a true O(1)
    single-file operation), so bare-specifier / tsconfig-alias / Rust-workspace resolution needs
    a plausible root inferred from the file's own location rather than passed explicitly.
    """
    current = file_path if _repo_map._is_dir_with_receipt(file_path) else file_path.parent
    authenticated_root = _repo_map.authenticated_content_root()
    for candidate in [current, *current.parents]:
        try:
            if any(
                (
                    _repo_map._is_file_with_receipt(candidate / marker)
                    if authenticated_root is not None
                    else (candidate / marker).is_file()
                )
                for marker in _repo_map._PROJECT_ROOT_MARKERS
                if marker != ".git"
            ):
                return candidate
            if authenticated_root is not None and _repo_map.os.path.normcase(
                _repo_map.os.path.abspath(candidate)
            ) == _repo_map.os.path.normcase(_repo_map.os.path.abspath(authenticated_root)):
                return candidate
            if authenticated_root is None and (candidate / ".git").exists():
                return candidate
        except OSError:
            continue
    return current


def _resolve_raw_import_entry(
    importer_path: _repo_map.Path,
    entry: dict[str, Any],
    repo_root: _repo_map.Path | str | None,
    language_id: str,
) -> dict[str, Any]:
    module = str(entry.get("module", ""))
    line = int(entry.get("line", 0) or 0)
    dynamic = bool(entry.get("dynamic", False))
    dynamic_unresolved = bool(entry.get("dynamic_unresolved", False))

    if dynamic_unresolved:
        # #93 SUB-1: the module argument isn't a static string literal (e.g.
        # `import_module(pkg_var)` / `import(path)`) -- there is no name to resolve against.
        # Fail closed: never guess a target file for an import whose identity we don't actually
        # know (over-reporting here would be a precision regression in a capability feature).
        resolved, external, provenance, confidence = None, False, [], 0.0
    elif language_id in ("javascript", "typescript"):
        candidate_info = _repo_map._js_ts_module_candidates(importer_path, module, repo_root)
        is_relative = module.startswith(".")
        has_candidates = bool(candidate_info["paths"])
        resolved = next(
            (
                str(p)
                for p in candidate_info["paths"]
                if _repo_map._is_file_with_receipt(_repo_map.Path(p))
            ),
            None,
        )
        external = (not is_relative) and not has_candidates
        provenance = list(candidate_info["provenance"]) or (["relative"] if is_relative else [])
        confidence = float(candidate_info["confidence"]) if resolved is not None else 0.0
    elif language_id == "rust":
        candidates = _repo_map._rust_module_candidates(importer_path, module, repo_root)
        resolved_candidate = next(
            (
                current
                for current in candidates
                if _repo_map._is_file_with_receipt(_repo_map.Path(str(current["path"])))
            ),
            None,
        )
        parts = [part.strip() for part in module.split("::") if part.strip()]
        first = parts[0] if parts else ""
        is_workspace_crate = first not in {"crate", "self", "super"} and (
            _repo_map._rust_workspace_entry_for_crate(first, repo_root) is not None
        )
        is_local_syntax = first in {"crate", "self", "super"} or is_workspace_crate
        resolved = str(resolved_candidate["path"]) if resolved_candidate else None
        external = resolved is None and not is_local_syntax
        provenance = list(resolved_candidate["provenance"]) if resolved_candidate else []
        confidence = float(resolved_candidate["confidence"]) if resolved_candidate else 0.0
    elif language_id == "python":
        level = int(entry.get("level", 0) or 0)
        candidate_info = _repo_map._python_module_candidates(
            importer_path, module, repo_root, level=level
        )
        resolved = next(
            (
                str(p)
                for p in candidate_info["paths"]
                if _repo_map._is_file_with_receipt(_repo_map.Path(p))
            ),
            None,
        )
        provenance = list(candidate_info["provenance"])
        if (resolved is None and (level > 0 or candidate_info["paths"])) or (
            entry.get("names") and (level > 0 or candidate_info["paths"])
        ):
            from tensor_grep.cli.incremental_symbol_cache import resolve_python_import_target

            target = resolve_python_import_target(
                importer_path,
                module,
                repo_root,
                level=level,
                imported_names=entry.get("names") or (),
            )
            if target is not None and _repo_map._is_file_with_receipt(target):
                resolved = str(target)
                provenance = ["relative"] if level > 0 else ["package-relative"]
        external = resolved is None
        if resolved is not None:
            # #152 fix: report the specific "sys-path-insert" provenance when this candidate was
            # reached only via a sys.path-hacked root, instead of the generic heuristic tag.
            tagged_provenance = candidate_info.get("path_provenance", {}).get(resolved)
            if tagged_provenance is not None:
                provenance = [tagged_provenance]
        confidence = (
            float(candidate_info["confidence"])
            if resolved is not None and candidate_info["confidence"]
            else (1.0 if level > 0 else 0.9)
            if resolved is not None
            else 0.0
        )
    elif language_id == "java":
        # Foundational tier (symbols+imports only): raw import statements are extracted and
        # reported with their line numbers (see _java_imports_with_lines), but resolving WHICH
        # file/module an import points to is cross-file resolution machinery (mirrors Rust/JS/
        # Python's own `_*_module_candidates` helpers) deferred to a follow-up PR. Report as
        # unresolved-but-not-presumed-external -- the same conservative tuple the
        # dynamic_unresolved branch above uses -- rather than guessing (never fabricate
        # resolution precision this extractor doesn't actually have).
        resolved, external, provenance, confidence = None, False, [], 0.0
    elif language_id in ("go", "php", "csharp", "c", "cpp"):
        # Foundational tier (mirrors the "java" branch above): raw import statements are
        # extracted with their line numbers (see lang_go.go_imports_with_lines /
        # lang_php.php_imports_with_lines / lang_csharp.csharp_imports_with_lines /
        # lang_c.c_imports_with_lines / lang_cpp.cpp_imports_with_lines), but resolving WHICH
        # file/module an import points to is deferred -- each of these five needs DIFFERENT
        # missing machinery: go's existing `_go_import_path_to_dir` resolves to a PACKAGE
        # DIRECTORY, not a file (no 1:1 import-to-file mapping to wire); php has no
        # PSR-4/composer.json autoload-map reader; csharp has no `.csproj`/namespace-to-file map;
        # c/cpp have no standardized manifest at all (no go.mod/composer.json/.csproj
        # equivalent) -- a bare `#include "foo.h"` needs the including file's own directory plus
        # the compiler's `-iquote`/`-I` search order, and a bare `#include <foo.h>` is ENTIRELY
        # build-system/toolchain defined, not in the source at all (see lang_c.py's and
        # lang_cpp.py's module docstrings). None of that resolver machinery exists yet for any of
        # the five. Report as unresolved-but-not-presumed-external -- the same conservative tuple
        # every other "resolution not yet built" branch in this function uses -- rather than
        # guessing (never fabricate resolution precision these extractors don't actually have).
        resolved, external, provenance, confidence = None, False, [], 0.0
    else:
        resolved, external, provenance, confidence = None, True, [], 0.0

    result: dict[str, Any] = {
        "module": module,
        "line": line,
        "resolved": resolved,
        "provenance": provenance,
        "resolution_confidence": confidence,
        "external": external,
    }
    if dynamic:
        # Payload-bloat fix (#93 SUB-1 follow-up): only stamp the dynamic markers on an entry
        # that is ACTUALLY dynamic. Stamping "dynamic": false / "dynamic_unresolved": false on
        # every static entry (the overwhelming majority) conveys nothing and tipped
        # `tg importers`' payload past the <10%-of-`tg map` token-economy guard (a capability
        # invariant -- see test_importers_payload_is_far_smaller_than_map). Presence of
        # "dynamic" now itself MEANS "this is a dynamic import" -- callers must use
        # `.get("dynamic", False)`, not a hard subscript.
        result["dynamic"] = dynamic
        result["dynamic_unresolved"] = dynamic_unresolved
    return result


def build_file_imports(file_path: str | _repo_map.Path) -> dict[str, Any]:
    """Return what a single FILE imports, resolved to target files where possible.

    O(1): parses exactly one file (plus lazily-primed, cached repo context for tsconfig/Cargo
    workspace lookups) -- no repo scan, no scan cap, no ``--deadline``. See ``build_file_importers``
    for the reverse (who imports this file) primitive, which does need a bounded repo scan.
    """
    resolved_file = _repo_map._resolved_input_path(file_path)
    if resolved_file.is_dir():
        raise ValueError(f"tg imports expects a FILE, not a directory: {resolved_file}")
    if not _repo_map._is_file_with_receipt(resolved_file):
        raise FileNotFoundError(f"File not found: {resolved_file}")

    payload = _repo_map._envelope(resolved_file)
    payload["routing_reason"] = "file-imports"
    payload["file"] = str(resolved_file)

    spec = _repo_map.lang_registry.spec_for_path(resolved_file)
    language_id = spec.language_id if spec is not None else None
    supported = language_id in _repo_map._SUPPORTED_FILE_DEPENDENCY_LANGUAGES

    try:
        file_size = resolved_file.stat().st_size
    except OSError:
        file_size = 0
    max_parse_bytes = _repo_map._max_parse_bytes()
    over_cap = file_size > max_parse_bytes

    imports: list[dict[str, Any]] = []
    result_incomplete = False
    incomplete_reason: str | None = None

    if over_cap:
        # Fix-A-lineage honesty rule (#74 design): the underlying per-file byte cap
        # (`_imports_and_symbols_for_path`, `_imports_with_lines_for_path`) returns an empty
        # result for an over-cap file -- that must NEVER read as "this file genuinely has zero
        # imports". Surface it as an incomplete scan instead of a clean empty list.
        result_incomplete = True
        incomplete_reason = (
            f"file exceeds the {max_parse_bytes}-byte parse cap (size={file_size}); "
            "imports were not scanned"
        )
    elif not supported:
        result_incomplete = True
        incomplete_reason = (
            "no import extractor registered for this file suffix"
            if spec is None
            else f"'{language_id}' has no import-resolution support in `tg imports` yet"
        )
    else:
        repo_root = _repo_map._infer_project_root(resolved_file)
        for raw_entry in _repo_map._imports_with_lines_for_path(resolved_file):
            imports.append(
                _repo_map._resolve_raw_import_entry(
                    resolved_file, raw_entry, repo_root, str(language_id)
                )
            )

    payload["imports"] = imports
    payload["resolved_files"] = sorted(
        dict.fromkeys(str(current["resolved"]) for current in imports if current.get("resolved"))
    )
    payload["external_modules"] = sorted(
        dict.fromkeys(str(current["module"]) for current in imports if current.get("external"))
    )
    payload["unresolved"] = sorted(
        dict.fromkeys(
            str(current["module"])
            for current in imports
            # #93 SUB-1: `current.get("module")` guards out dynamic_unresolved entries (module
            # ""), which are surfaced via their own `dynamic_unresolved` marker on the raw
            # entry, not as a fabricated blank name in this flat unresolved-module-names summary.
            if current.get("module") and not current.get("external") and not current.get("resolved")
        )
    )
    payload["result_incomplete"] = result_incomplete
    if incomplete_reason is not None:
        payload["incomplete_reason"] = incomplete_reason
    _repo_map.attach_target_gaps(
        payload, resolved_file, answer_empty=_repo_map.answer_empty(payload, "imports")
    )
    return payload


def _confirm_import_edges(
    candidate_importer: _repo_map.Path,
    target_file: str,
    repo_root: _repo_map.Path | str | None,
    *,
    raw_entries: list[dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    return _repo_map_graph.confirm_edges(
        _repo_map,
        candidate_importer,
        target_file,
        repo_root,
        raw_entries=raw_entries,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )


def _tier_reverse_importer_candidates(
    candidates: set[str],
    target_file: str,
) -> list[str]:
    """Proximity-tiered reverse-import candidate ordering (dogfood flap: v1.81.15 PASS ->
    v1.81.17 INCOMPLETE "0 importers @ 330/1035 files scanned" on a 50k-file WSL multi-repo
    workspace). Orders the reverse-import PREFILTER candidates by proximity to TARGET before
    ``build_file_importers_from_map`` applies
    its ``CALLER_SCAN_FILE_CEILING``/``--deadline`` slice, instead of the plain lexicographic
    path sort this replaces.

    ``_reverse_importers``'s alias prefilter is DELIBERATELY broad -- ``_module_aliases_for_path``
    keys purely on a file's basename/near-basename (e.g. every ``utils.py`` in a 40-repo
    workspace collides on the "utils" alias), relying on the per-candidate
    ``_confirm_import_edges`` CONFIRM step to separate real edges from same-named-but-unrelated
    noise. On a huge multi-repo ROOT this prefilter can legitimately balloon to 1000+ candidates
    for one target file, the overwhelming majority from OTHER repos. A plain
    ``sorted(reverse_map.get(target_file, set()))`` then buckets that list by absolute-path
    string, i.e. effectively by REPO NAME alphabetically -- nothing about it favors TARGET's own
    repo. When the ceiling/deadline slice below cuts the list short (a large ROOT + a
    --deadline, or the default CLI --deadline-less path racing wall-clock variance on a slow
    WSL /mnt/c mount), a target file whose own repo happens to sort late is entirely capable of
    having ALL of its real (same-repo) importers stranded past the cut line, reporting "0
    importers" while the scan is still honestly stamped incomplete -- exactly the reported flap.

    A real importer of FILE is overwhelmingly within FILE's own repo/package, so reordering the
    SAME candidate set (never dropping or adding members) into four proximity tiers, closest
    first, makes a partial scan cover the highest-yield subset first:

      0. same directory as FILE, or one of FILE's ancestor directories up to (and including) its
         inferred project root (``_infer_project_root`` -- the nearest ``.git``/``pyproject.toml``/
         ``package.json``/``Cargo.toml``/``tsconfig.json`` marker) -- the files most likely to hold
         a real same-package edge (a parent ``__init__.py``, a sibling module).
      1. elsewhere inside the same project root.
      2. outside the project root, but the same language (file suffix) as FILE.
      3. everything else.

    Within a tier, candidates sort by path string for full determinism, independent of the
    upstream set/dict iteration order. This is a pure REORDERING of the same candidate
    membership -- an unbounded/complete scan (no ceiling or deadline hit) still confirms every
    candidate exactly as before and returns the identical found-set;
    ``build_file_importers_from_map`` re-sorts ``edges`` by ``(file, line)`` before returning, so
    the tiering here never changes output order, only which candidates survive a partial cut.
    """
    target_path = _repo_map.Path(target_file)
    target_dir = target_path.parent
    project_root = _repo_map._infer_project_root(target_path)
    ancestor_dirs: set[_repo_map.Path] = set()
    current = target_dir
    while True:
        try:
            ancestor_dirs.add(_repo_map._resolved_input_path(current))
        except OSError:
            ancestor_dirs.add(current)
        if current == project_root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    target_suffix = target_path.suffix.lower()
    # review nit (PR #670, same class as the #639 precedent at :1149): _tier runs once per
    # candidate via sorted() below, and for every non-same-ancestry candidate (the majority on a
    # large multi-repo ROOT) used to pay ~3 filesystem syscalls -- its own
    # candidate_path.resolve(), a SECOND resolve of that identical path inside
    # _path_is_relative_to, and a resolve of project_root inside _path_is_relative_to repeated on
    # EVERY call despite being invariant across the whole sort. This runs over the FULL
    # prefiltered set (not ceiling-bounded) BEFORE the deadline-gated confirm loop, so on a slow
    # filesystem a short --deadline could be consumed by tiering itself. Resolve project_root
    # exactly ONCE here; _tier below reuses the already-resolved resolved_candidate directly via
    # relative_to instead of calling _path_is_relative_to (which would re-resolve both sides
    # again) -- net one resolve per candidate, zero per-candidate project-root resolves.
    try:
        project_root_resolved = _repo_map._resolved_input_path(project_root)
    except OSError:
        project_root_resolved = project_root

    def _tier(candidate: str) -> int:
        candidate_path = _repo_map.Path(candidate)
        try:
            resolved_candidate = _repo_map._resolved_input_path(candidate_path)
        except OSError:
            resolved_candidate = candidate_path
        if resolved_candidate.parent in ancestor_dirs:
            return _repo_map._REVERSE_IMPORTER_TIER_SAME_ANCESTRY
        try:
            resolved_candidate.relative_to(project_root_resolved)
            return _repo_map._REVERSE_IMPORTER_TIER_SAME_PROJECT
        except ValueError:
            pass
        if candidate_path.suffix.lower() == target_suffix:
            return _repo_map._REVERSE_IMPORTER_TIER_SAME_LANGUAGE
        return _repo_map._REVERSE_IMPORTER_TIER_OTHER

    return sorted(candidates, key=lambda candidate: (_tier(candidate), candidate))


def build_file_importers_from_map(
    repo_map: dict[str, Any],
    file_path: str | _repo_map.Path,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    # A relative `file_path` here is resolved against `repo_map`'s own root, NOT process cwd --
    # deliberate for this function's OTHER callers: `session_file_importers` and the raw
    # `file_importers` daemon-socket command (session_store.py) pass a `file` straight from a
    # remote/persistent-daemon request, where the daemon process's own cwd is meaningless and
    # "relative to the session root" is the only sane interpretation (round-7 security audit
    # #81 deliberately anchors the MCP confinement check the same way; see
    # `tg_session_file_importers` in mcp_server.py). `build_file_importers` (the CLI/cold-tier
    # entry point, dogfood #104) instead pre-resolves FILE to an absolute cwd-anchored path
    # BEFORE calling here, so this join is a no-op for that caller -- do not "fix" this function
    # to resolve against cwd instead, that would silently break the session/daemon contract.
    repo_root = _repo_map._resolved_input_path(str(repo_map["path"]))
    resolved_file = _repo_map.Path(file_path).expanduser()
    if not resolved_file.is_absolute():
        resolved_file = repo_root / resolved_file
    resolved_file = _repo_map._resolved_input_path(resolved_file)
    if not _repo_map._is_file_with_receipt(resolved_file):
        raise FileNotFoundError(f"File not found: {resolved_file}")
    target_file = str(resolved_file)
    # Honesty fix (dogfood, published v1.69.2 wheel): a purely lexical containment check --
    # both paths are already `.resolve()`d above, so this touches no filesystem state beyond
    # what the code above already did. A relative FILE (the daemon/session convention, joined
    # onto repo_root by the `if not resolved_file.is_absolute()` branch above) stays a
    # descendant of repo_root for the normal in-repo convention, so it does not trip this; a
    # net-escaping `..` relative path (e.g. `../other/mod.py`) correctly DOES. The usual
    # outside-root case is an already-absolute FILE (the CLI cold-tier caller, pre-resolved
    # against cwd by `build_file_importers` before this function ever sees it). `relative_to` raises ValueError both for a genuinely
    # unrelated path and for a different Windows drive -- both mean "outside root" here. Without
    # this signal, an outside-root FILE silently reported `importer_count: 0`, indistinguishable
    # from a genuine "unimported inside ROOT" answer, when the real problem was ROOT defaulting
    # to the wrong scan boundary (e.g. CWD instead of the repo that actually contains FILE).
    try:
        resolved_file.relative_to(repo_root)
        file_outside_root = False
    except ValueError:
        file_outside_root = True

    payload = _repo_map._envelope(repo_root)
    payload["routing_reason"] = "file-importers"
    payload["file"] = target_file
    payload["file_outside_root"] = file_outside_root

    all_files = _fast_cache.importers_all_files(repo_map)
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(str(name) for name in current.get("imports", []) if name)
        )
        for current in repo_map.get("imports", [])
    }
    # `include_directory_index_aliases=True`: `tg importers` is the ONE reverse consumer that
    # runs the per-candidate CONFIRM step below (`_confirm_import_edges`), so it is the only one
    # that may safely widen the prefilter with directory-index parent-dir aliases (a bare
    # `require('./router')` importer of `router/index.js`). The blast-radius / context callers of
    # `_reverse_importers` leave this OFF -- they feed its output into PageRank scoring with no
    # confirm step, and widening it there reorders pinned output (see the note on that function).
    # #691 gate NIT-2 (#222 residual symmetry): `_reverse_importers` is ~linear -- not the
    # super-linear culprit `_reverse_import_distances` was -- so this is lower severity than
    # NIT-1, but this function's OWN confirm-edges loop just below already honors `deadline_
    # monotonic`; leaving this call un-threaded left one un-gated whole-repo pass in an otherwise
    # deadline-aware pipeline. Fold its own trip into the SAME `deadline_hit` local the
    # confirm-edges loop already sets, so `tg importers --deadline` reports one honest signal
    # regardless of which stage actually consumed the budget.
    reverse_importers_deadline_hit = _repo_map._DeadlineBreakFlag()
    reverse_map = _repo_map._reverse_importers(
        all_files,
        imports_by_file,
        include_directory_index_aliases=True,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_importers_deadline_hit,
    )
    # Proximity-tiered, not a plain lexicographic path sort (dogfood flap fix) -- see
    # _tier_reverse_importer_candidates for why a bare alphabetical order strands a target
    # repo's real importers on a large multi-repo ROOT once the ceiling/deadline slice below
    # cuts the candidate list short.
    prefiltered = _repo_map._tier_reverse_importer_candidates(
        reverse_map.get(target_file, set()), target_file
    )

    ceiling_hit = len(prefiltered) > _repo_map.CALLER_SCAN_FILE_CEILING
    bounded_candidates = prefiltered[: _repo_map.CALLER_SCAN_FILE_CEILING]

    edges: list[dict[str, Any]] = []
    deadline_hit = reverse_importers_deadline_hit.hit
    scanned_count = 0
    for candidate in bounded_candidates:
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            deadline_hit = True
            break
        scanned_count += 1
        edges.extend(
            _repo_map._confirm_import_edges(_repo_map.Path(candidate), target_file, repo_root)
        )
    edges.sort(key=lambda item: (str(item["file"]), int(item.get("line", 0) or 0)))

    _fast_cache.finalize_importers_payload(payload, edges, _repo_map._is_test_file)
    # backlog #57 companion fix: copy the (unrelated) repo-map-level scan_limit/scan_remediation
    # BEFORE stamping the caller-scan ceiling's own remediation below -- build_symbol_callers_from_map
    # orders it the same way (_copy_scan_limit, then its ceiling-hit _mark_result_incomplete).
    # Reversing this order was a real bug caught while adding coverage for this branch: a COMPLETE
    # repo-map scan always stamps `scan_remediation: None` on its own payload (see
    # _mark_result_incomplete's docstring), and _copy_scan_limit unconditionally copies that key
    # over when `source["scan_limit"]` is a dict -- clobbering the ceiling-hit remediation this
    # function sets below if _copy_scan_limit ran AFTER it instead of before.
    _repo_map._copy_scan_limit(payload, repo_map)
    if ceiling_hit:
        # task #61 lesson: bound this sibling loop with the SAME ceiling the caller-scan main
        # loop uses, and mark it via the shared `caller_scan_limit` shape so the CLI's existing
        # `_scan_truncation_warning`/exit-2 contract picks it up with no bespoke wiring.
        # backlog #57 companion fix: route through _mark_result_incomplete (as
        # build_symbol_callers_from_map/build_symbol_refs_from_map already do at their own
        # ceiling-hit sites) instead of setting `caller_scan_limit` alone -- the CLI's exit-2 gate
        # already read `caller_scan_limit` directly so behavior there was unaffected, but a
        # non-CLI consumer (MCP tools, a direct build_file_importers*/session_file_importers call)
        # used to see the ceiling-truncation fact without the `result_incomplete`/`scan_remediation`
        # honesty signal its callers/refs siblings always set.
        _repo_map._mark_result_incomplete(
            payload,
            remediation=_repo_map._CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": _repo_map.CALLER_SCAN_FILE_CEILING,
                "files_total": len(prefiltered),
            },
        )
    if deadline_hit:
        payload["partial"] = True
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "importer_candidates_scanned": scanned_count,
            "importer_candidates_total": len(bounded_candidates),
        }
    # Stamped LAST (after _copy_scan_limit and the ceiling/deadline blocks above), only when no
    # more specific remediation already claimed the slot: _copy_scan_limit unconditionally copies
    # the repo-map's own `scan_remediation` (often `None` on a complete ROOT scan) whenever the
    # repo-map carried a `scan_limit` dict, and the ceiling-hit branch above stamps its own
    # narrower message first via `_mark_result_incomplete` -- either would silently clobber this
    # one if it ran earlier (backlog #57 taught the same lesson for the ceiling-hit remediation
    # above). `file_outside_root` is additive-only: it never flips `result_incomplete`/`partial`
    # or changes exit code, since a truly outside-root FILE always finds zero prefiltered
    # candidates (never ceiling- or deadline-bound).
    if file_outside_root and not payload.get("scan_remediation"):
        payload["scan_remediation"] = (
            f"FILE {resolved_file} is outside the scanned ROOT {repo_root}; the importers scan "
            "only covers ROOT, so 0 importers here does NOT mean the file is unused. Pass the "
            "repo containing FILE as ROOT (tg importers FILE <its-repo>) or run from inside it."
        )
    _repo_map.attach_importer_coverage(
        payload,
        repo_map,
        all_files,
        repo_root,
        resolved_file,
        answer_empty=_repo_map.answer_empty(payload, "importers"),
    )
    return payload


def build_file_importers(
    file_path: str | _repo_map.Path,
    root: str | _repo_map.Path = ".",
    *,
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> dict[str, Any]:
    """Return which files import a single FILE (the reverse #74 file-dependency primitive).

    Cold tier: builds (or reuses a cached) repo map over ROOT, then confirms candidate importer
    edges precisely. See ``session_file_importers`` in session_store.py for the zero-reparse
    session tier, which calls ``build_file_importers_from_map`` directly on a cached map.

    Dogfood #104 fix: FILE is resolved to an absolute path independently, against cwd -- the
    SAME rule ``build_file_imports`` (``tg imports``, which takes no ROOT arg at all) already
    uses -- before ``build_file_importers_from_map`` ever sees it. ROOT is only the scan
    boundary. Without this, a cwd-relative FILE arg (the normal shell convention; from a parent
    directory it is naturally prefixed with ROOT's own directory name, e.g.
    ``myrepo/src/util.py`` when ROOT is ``myrepo``) got silently re-joined onto ROOT a second
    time inside ``build_file_importers_from_map`` (``repo_root / resolved_file``), doubling the
    path (``myrepo/myrepo/src/util.py``) and raising a spurious "not found". An already-absolute
    FILE is unaffected (``.resolve()`` on an absolute path just normalizes it), so this is a
    no-op for every caller that already passes an absolute FILE (both MCP tools always do, via
    ``_confine_read_path``) -- only the previously-buggy cwd-relative case changes.
    """
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)
    resolved_file_path = _repo_map._resolved_input_path(file_path)
    repo_map = _repo_map.build_repo_map(
        root,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = _repo_map.build_file_importers_from_map(
        repo_map,
        resolved_file_path,
        deadline_monotonic=deadline_monotonic,
    )
    _repo_map._copy_partial_signal(result, repo_map)
    return result
