"""One-call codebase orientation capsule (`tg orient`).

Reuses repo_map's import graph (in-degree centrality) + AST symbol-source chunkers to produce a
bounded, AI-readable "explain this repo" capsule: the most central files, entry points, a symbol
map, and AST-boundary snippets within a token budget. Pure-CPU, no API key, no GPU.
"""

from __future__ import annotations

import fnmatch
import json
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map as _repo_map

_CHARS_PER_TOKEN = 3.5

# Documentation suffixes excluded from the code-centrality ranking: a doc file is never a useful
# "central CODE file", and in doc-heavy repos (many cross-linked CLAUDE.md / README) it would
# otherwise dominate the graph and bury the real architecture.
_CENTRAL_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})
# Config/data suffixes also excluded (round-8 audit): a package.json / *.yaml / *.toml / *.lock has
# no import edges and no symbols, so in a config- or doc-heavy "harness" repo it would surface as a
# spurious "central" file over the real code (the recurring dogfood complaint that orient ranks
# non-code as central). build_repo_map's fallback-source set includes these, so they reach here.
_CENTRAL_CONFIG_DATA_SUFFIXES = frozenset({
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".csv",
    ".lock",
    ".env",
})
# The full non-code exclusion applied to the centrality ranking (docs + config/data).
_CENTRAL_NON_CODE_SUFFIXES = _CENTRAL_DOC_SUFFIXES | _CENTRAL_CONFIG_DATA_SUFFIXES

# Composite-centrality tuning (see _central_files_from_map): cap in-degree so a widely-imported data
# sink can't dominate, and bound symbol density so one giant file can't either.
_CENTRAL_FAN_IN_CAP = 12
_CENTRAL_SYMBOL_DENSITY_CAP = 25

# Auto de-weight (never hard-exclude) bundled vendor/skill/generated CODE subtrees, AND known
# AI-coding-harness config directories, so `tg orient`/`tg agent` surface real product code without
# a manual `--ignore` (#55 PR6; harness dirs added #164; unambiguous-vendor-name STRONG-0 promotion
# + skill-tree shape heuristic added M1). A subtree fires on STRONG-0 (a closed-vocabulary directory
# NAME) ALONE, on STRONG-3 (a `skills`-named directory whose children have the SHAPE of independent
# leaf skills) ALONE, or on STRONG-1 (nested package manifest) AND (STRONG-2 (import island) OR WEAK
# (name prior)):
#   STRONG-0 -- an exact, closed-vocabulary directory name is sufficient BY ITSELF, no manifest and
#     no import-island required. Two closed vocabularies, both matched on the EXACT basename (not
#     "any path component", the way `_VENDOR_NAME_PRIOR` is):
#       * `_TOOL_CONFIG_DIR_NAMES` (e.g. `.claude`) -- AI-coding-harness config. Dogfood bug (#164):
#         `.claude/hooks|lib|tools` (.cjs harness files) ranked in `tg orient`'s top-10 central_files
#         on every Claude-Code-harness repo, because such a directory never carries its own package
#         manifest (no package.json/pyproject.toml -- it's config, not a buildable nested project),
#         so gating it behind STRONG-1 like the WEAK prior below meant it could NEVER fire, no
#         matter what name list it was added to.
#       * `_STRONG0_VENDOR_DIR_NAMES` (`third_party`, `_vendored`) -- M1 dogfood gap: a bundled
#         dependency tree with NO manifest inside the SCANNED subtree (or a manifest filename outside
#         `_BROAD_WORKSPACE_PROJECT_MARKERS`) previously required STRONG-1 to even be CONSIDERED a
#         de-weight candidate at all, so the whole tree ranked as "central" right alongside real
#         product code. These names are effectively NEVER a repo's own product code, unlike `skills`
#         (see STRONG-3 below). NOTE the set is deliberately just these two: `node_modules`, `vendor`,
#         and `external_repos` are ALSO in `repo_map._SKIP_DIR_NAMES`, so the repo-map WALKER never
#         descends into them at all (a stronger, upstream protection) -- a STRONG-0 entry for them
#         would be dead code at real-scan time, so they are left out to keep this set honest.
#   STRONG-1 -- a directory below the repo root contains its own manifest, reusing the same marker
#     set `_path_has_project_marker` (main.py) uses for broad-scan workspace-project detection.
#   STRONG-2 -- an import island: no file OUTSIDE the subtree resolves an import INTO it (computed
#     from the same resolved-import graph the centrality ranking builds).
#   STRONG-3 (M1) -- a `skills`-named directory (AMBIGUOUS: unlike the unambiguous STRONG-0 vendor
#     names, a repo's OWN feature/plugin package could plausibly be named `skills/`) that clears a
#     SHAPE gauntlet designed so a genuine Python package can never pass it (see `_is_skill_leaf_tree`).
#     Fires only when ALL of:
#       (a) POSITIVE skill-manifest evidence -- at least `_SKILL_LEAF_FRACTION_THRESHOLD` of its
#           immediate children each carry their own `SKILL.md`/`skill.md` (`_child_has_skill_manifest`).
#           This is the LOAD-BEARING guard: an earlier draft counted a child as a leaf if it merely
#           had "no imports crossing out", but the stem-only import graph
#           (`_code_files_and_import_graph`) cannot resolve a `from skills.auth import Auth` symbol/
#           subpackage edge, so a real `__init__.py` subpackage had empty resolved-imports and
#           passed VACUOUSLY -- mislabeling a genuine product `skills/` package as a bundle (Opus-gate
#           FP). A folder-per-skill bundle always carries a manifest; a Python package never does.
#       (b) NO `__init__.py` in the tree root OR any immediate child -- an unambiguous "this is a real
#           Python (sub)package, not a folder-per-skill bundle" marker. Belt-and-suspenders with (a).
#       (c) the best-effort external-import guard: nothing OUTSIDE the tree resolves an import INTO it
#           (the same `externally_isolated` evidence STRONG-2 uses). Kept as a strictly-safer extra
#           signal, but it is NOT relied on alone -- the stem graph misses symbol/subpackage imports,
#           which is exactly why (a)+(b) exist.
#   WEAK -- a name prior (vendor/, third_party/, skills/, external_repos/, _vendored/, node_modules/):
#     a TIE-BREAKER for a STRONG-1 candidate that is neither an island nor already covered by
#     STRONG-0/STRONG-3 above (kept for any directory that reaches this point via a nested manifest
#     alone -- e.g. a manifest-bearing `node_modules/` that is not an import island, see the tests).
# A monorepo subproject that HAS a manifest but IS imported across the repo is protected by STRONG-2
# (not an island) -- de-weight, never exclude, is what keeps a false positive from hiding real product
# code (the file can still surface if it is genuinely central even after the multiplier).
_DEWEIGHT_FACTOR = 0.25
_VENDOR_NAME_PRIOR = frozenset({
    "vendor",
    "third_party",
    "skills",
    "external_repos",
    "_vendored",
    "node_modules",
})
# STRONG-0 tool/harness config directory names (see the comment above `_DEWEIGHT_FACTOR`): matched
# on the EXACT directory basename (not "any path component", the way `_VENDOR_NAME_PRIOR` is), so
# only the `.claude` root itself is flagged, not every subdirectory beneath it. Deliberately
# CONSERVATIVE -- only a name validated by a real-corpus before/after (agent-studio, #164) is
# listed. `.github`/`.vscode` are plausible future candidates but are NOT included without their
# own validation pass (over-deweighting a real source directory is worse than missing one).
_TOOL_CONFIG_DIR_NAMES = frozenset({
    ".claude",
})
# STRONG-0 unambiguous vendor/dependency directory names (M1): unlike `_VENDOR_NAME_PRIOR`'s WEAK
# tie-breaker role above (fires only alongside a STRONG-1 nested manifest), these names are
# effectively NEVER a repo's own product code -- matched on the EXACT directory basename, same
# mechanism as `_TOOL_CONFIG_DIR_NAMES`, so a `third_party/`/`_vendored/` subtree de-weights its
# WHOLE contents without needing a nested manifest at all. Deliberately just these two: `node_modules`,
# `vendor`, and `external_repos` are ALSO in `repo_map._SKIP_DIR_NAMES` (the repo-map walker skips
# them entirely at scan time), so a STRONG-0 entry for those would be dead code -- they stay OUT to
# keep this set honest (they still fire as the WEAK `_VENDOR_NAME_PRIOR` tie-breaker if a nested
# manifest ever surfaces one through the walker). `skills` is EXCLUDED for a different reason -- a
# repo's OWN feature/plugin package could plausibly be named `skills/`, so it gets a SHAPE gauntlet
# instead of a bare name-alone promotion; see `_is_skill_leaf_tree` and the STRONG-3 tier above.
_STRONG0_VENDOR_DIR_NAMES = frozenset({
    "third_party",
    "_vendored",
})
# STRONG-3 skill-leaf manifest filenames (Opus-gate FIX -- now the SOLE positive leaf signal, see
# `_child_has_skill_manifest`): a `skills`-named directory's immediate child counts toward the
# leaf-fraction ONLY if it carries one of these manifest filenames (case-insensitive, checked
# directly inside the child -- not recursively, mirroring STRONG-1's own manifest check). The
# earlier "no imports crossing out / no code files" fallback was REMOVED: it satisfied the fraction
# vacuously for an `__init__.py` subpackage whose symbol/subpackage imports the stem-only graph
# could not resolve, a false positive on the common `from skills.<subpkg> import <Symbol>` idiom.
_SKILL_LEAF_MANIFEST_NAMES = frozenset({"skill.md"})
# A `skills/` directory de-weights its whole subtree only when AT LEAST this fraction of its
# immediate children look like independent leaf skills -- a strict majority, not "any single one",
# so a handful of leaf-shaped helper folders inside an otherwise-real `skills/` PACKAGE (e.g. a
# plugin registry with a couple of self-contained example plugins) does not tip the whole package
# into being de-weighted.
_SKILL_LEAF_FRACTION_THRESHOLD = 0.6

_ENTRY_NAMES = {
    "main.py",
    "__main__.py",
    "cli.py",
    "app.py",
    "server.py",
    "main.ts",
    "index.ts",
    "index.js",
    "main.js",
    "app.ts",
    # .tsx entrypoints (React/Ink CLIs): a real CLI entry is often main.tsx/app.tsx, not just the
    # index.ts barrel (dogfood 2026-07-03 — orient listed index.ts barrels, missed main.tsx).
    "main.tsx",
    "app.tsx",
    "cli.tsx",
    "index.tsx",
    "main.rs",
    "lib.rs",
}


def _code_files_and_import_graph(
    rm: dict[str, Any],
) -> tuple[list[str], dict[str, list[str]], dict[str, set[str]]]:
    """Shared code-only import graph (docs/config/data suffixes excluded): returns
    ``(code_files, resolved_imports, reverse_importers)``. Used by both the centrality ranking and
    the vendored-subtree import-island detection so the two heuristics see the identical graph."""
    all_files = [str(f) for f in rm.get("files", [])]
    if not all_files:
        return [], {}, {}
    # "Central files" surface CODE architecture. Documentation files (heavily cross-referenced in
    # doc-heavy repos — e.g. 36 CLAUDE.md files) must not rank as central, and must not absorb a code
    # import via a stem collision (config.md shadowing config.py in by_stem). Exclude docs from the
    # graph entirely; fall back to all files only if the repo is pure docs so we still return context.
    code_files = [
        f for f in all_files if Path(f).suffix.lower() not in _CENTRAL_NON_CODE_SUFFIXES
    ] or all_files
    code_file_set = set(code_files)
    imports_by_file: dict[str, list[str]] = {
        str(entry["file"]): [str(i) for i in entry.get("imports", [])]
        for entry in rm.get("imports", [])
        if str(entry["file"]) in code_file_set
    }
    # build_repo_map records imports as module names ("hub"), not file paths ("hub.py"); resolve them
    # to files by stem so the import graph has real edges. Docs are excluded so they cannot shadow a
    # code module.
    by_stem: dict[str, str] = {}
    for source in code_files:
        by_stem.setdefault(Path(source).stem, source)
    resolved_imports: dict[str, list[str]] = {}
    for source, modules in imports_by_file.items():
        targets: list[str] = []
        for module in modules:
            candidate = by_stem.get(module) or by_stem.get(module.split(".")[-1])
            if candidate and candidate != source:
                targets.append(candidate)
        resolved_imports[source] = targets
    reverse_importers = _repo_map._reverse_importers(code_files, resolved_imports)
    return code_files, resolved_imports, reverse_importers


def _dir_contains_init_py(directory: Path) -> bool:
    """True if ``directory`` directly contains an ``__init__.py`` -- an unambiguous "this is a real
    Python (sub)package" marker used to REFUSE a STRONG-3 skill-leaf match (see
    ``_is_skill_leaf_tree``). Fail-safe: any OSError reads as "not a package" (False)."""
    try:
        return (directory / "__init__.py").is_file()
    except OSError:
        return False


def _child_has_skill_manifest(child_abs_dir: Path) -> bool:
    """One immediate child of a STRONG-3 ``skills/`` candidate: does it carry its own skill manifest
    (``SKILL.md``/``skill.md``, matched case-insensitively directly inside the child -- NOT
    recursively, mirroring how STRONG-1's own manifest check works)?

    This is the SOLE positive leaf signal (Opus-gate FIX). An earlier draft ALSO counted a child
    with "no imports crossing out / no code files" as a leaf, but the stem-only import graph
    (``_code_files_and_import_graph``) cannot resolve a ``from skills.auth import Auth`` symbol/
    subpackage edge, so a real ``__init__.py`` subpackage had empty resolved-imports and satisfied
    that test VACUOUSLY -- a false positive on a genuine product ``skills/`` package consumed via
    the common ``from skills.<subpkg> import <Symbol>`` idiom. A folder-per-skill bundle always
    carries a manifest; a Python subpackage never does, so requiring one is unambiguous.
    """
    try:
        for entry in child_abs_dir.iterdir():
            if entry.is_file() and entry.name.lower() in _SKILL_LEAF_MANIFEST_NAMES:
                return True
    except OSError:
        pass
    return False


def _is_skill_leaf_tree(
    abs_dir: Path,
    tree_files: set[str],
    *,
    reverse_importers: dict[str, set[str]],
) -> bool:
    """STRONG-3 SHAPE gauntlet for an AMBIGUOUS ``skills``-named directory (see the module comment
    above ``_DEWEIGHT_FACTOR``): unlike an unambiguous ``_STRONG0_VENDOR_DIR_NAMES`` entry, a repo's
    own feature could plausibly be named ``skills/``, so bare-name-alone promotion is unsafe. Fires
    only when ALL of:

      (c) the best-effort external-import guard: nothing OUTSIDE the tree resolves an import INTO it
          (the same ``externally_isolated`` evidence STRONG-2 import-island detection uses). Kept as
          a strictly-safer EXTRA signal, but NOT relied on alone -- the stem-only import graph misses
          a ``from skills.<subpkg> import <Symbol>`` edge, which is why (a)+(b) below carry the load.
      (b) NO ``__init__.py`` in the tree root OR any immediate child -- a real Python (sub)package
          marker a folder-per-skill bundle never has (Opus-gate FIX, belt-and-suspenders with (a)).
      (a) POSITIVE skill-manifest evidence: at least ``_SKILL_LEAF_FRACTION_THRESHOLD`` of the
          immediate child directories each carry their own ``SKILL.md``/``skill.md``
          (``_child_has_skill_manifest``) -- the load-bearing guard (Opus-gate FIX).

    A ``skills/`` directory with NO subdirectories at all (flat ``.py``/``.ts`` files directly
    inside -- the shape of a real Python/TS package, not a folder-per-skill bundle) has no children
    to score and never matches.
    """
    externally_isolated = all(reverse_importers.get(f, set()) <= tree_files for f in tree_files)
    if not externally_isolated:
        return False  # imported from outside the tree -- looks like real, shared product code
    if _dir_contains_init_py(abs_dir):
        return False  # `skills/__init__.py` -- this is a real Python package, not a skill bundle
    try:
        child_dirs = [entry for entry in abs_dir.iterdir() if entry.is_dir()]
    except OSError:
        return False
    if not child_dirs:
        return False
    if any(_dir_contains_init_py(child) for child in child_dirs):
        return False  # a child is a real Python subpackage -- not a folder-per-skill bundle
    leaf_count = sum(1 for child in child_dirs if _child_has_skill_manifest(child))
    return (leaf_count / len(child_dirs)) >= _SKILL_LEAF_FRACTION_THRESHOLD


def _detect_vendored_subtrees(
    rm: dict[str, Any],
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> dict[str, dict[str, Any]]:
    """Auto-detect bundled vendor/skill/generated CODE subtrees, and known AI-tool/harness config
    directories, to DE-WEIGHT (never hard-exclude).

    Returns ``{tree_path: {"reasons": [...], "ignore_glob": "<repo-relative>/**"}}``. Fires on
    STRONG-0 (a closed-vocabulary directory name -- tool-config OR unambiguous vendor/dependency)
    ALONE, on STRONG-3 (a `skills`-named directory whose children have the SHAPE of independent leaf
    skills) ALONE, or on STRONG-1 (nested package manifest) AND (STRONG-2 (import island) OR WEAK
    (name prior)) -- see the module-level comment above ``_DEWEIGHT_FACTOR`` for the full rule.
    Requires ``rm["path"]`` to point at a real, existing directory (a synthetic/relative-path test
    fixture with no "path" key returns ``{}`` rather than guessing against the process CWD).

    ``deadline_monotonic`` (agent cold-path assembly-tail SLA fix): this is the single most
    expensive post-map assembly stage on a large repo -- a candidate-directory manifest-marker
    probe (STRONG-1, O(candidate_dirs x markers) `Path.exists()` calls), a reverse-import-graph
    re-derivation + STRONG-3 skill-leaf validation (`_is_skill_leaf_tree`, `iterdir()`-heavy per
    `skills`-named candidate -- measured on a real ~50k-file multi-project workspace: 241
    skills-named directories / 2,933 child directories, ~0.69s warm / low-seconds cold), and an
    outermost-nested-chain dedup (O(candidate_roots^2) `Path.resolve()`-backed comparisons) --
    profiled at ~1.2-3.6s PER CALL depending on how many manifest-bearing directories the repo has,
    and it is called from multiple sites (this module's own callers plus `repo_map._build_context_
    pack_from_map`'s independent `auto_deweight` pass) so the cost compounds. An optional
    PRE-ANCHORED absolute ``time.monotonic()`` budget, exactly like every other post-map deadline
    seam in this codebase (`_collect_outbound_dependencies`, `_build_context_pack_from_map`'s
    symbol-scoring loop), checked at FOUR points:

      1. Function entry: when the shared budget is ALREADY exhausted by the time this stage would
         start, skip the expensive detection entirely and return ``{}`` immediately.
      2. UNLIKE most sibling deadline seams (whose per-item cost is small enough that an
         entry-only check is sufficient), this function's OWN internal sections are each
         independently expensive enough to blow the ENTIRE remaining budget in a single
         uninterrupted call even when the deadline had NOT yet been exceeded at entry (measured:
         one call took ~3.6s against a repo whose collection stage left ~2.3s of budget) -- so the
         STRONG-1 manifest probe LOOP also checks the deadline per-iteration (mirroring
         `_build_context_pack_from_map`'s own per-symbol check) and breaks early, keeping whatever
         partial `manifest_dirs` was already found rather than discarding it.
      3. Immediately after that loop, one more check gates the reverse-import-graph re-derivation
         AND the STRONG-3 skill-leaf validation loop TOGETHER (they are not independently
         expensive enough on their own to warrant per-iteration checks the way the manifest probe
         and the dedup loop are, but their COMBINED cost on a skills-directory-heavy repo is real
         and was measured un-gated by points 2 and 4 alone) -- an early ``{}`` return here, same
         shape as every other bail-out in this function, never a partially-built dict missing
         fields the rest of the function assumes.
      4. The outermost-chain dedup loop ALSO checks the deadline per-iteration and breaks early,
         keeping whatever partial `subtree_rel_roots` was already deduped.

    All four cases return/keep the SAME shapes this function already produces for "no vendor/skill
    signal found" or "partial evidence" -- no new return type, no exception. This is purely
    ADDITIVE de-weighting evidence (never hard-exclude, per the docstring above), so cutting it
    short once a --deadline has already been blown never changes correctness, only whether ranking
    gets the (full or partial) vendor/skill de-weight boost -- and the capsule is already stamped
    partial/deadline_exceeded by the caller at that point. ``deadline_hit`` (optional, mirrors the
    ``_DeadlineBreakFlag`` sibling seams in this module and in ``repo_map.py``) is set to ``True``
    on ANY of the four checks firing, so a caller folding this into its own wider N-way
    partial/deadline_limit union can observe "did this stage actually cut anything short" even on
    a call that still returns a non-empty (but now partial) result. Default ``None`` for both new
    parameters (every pre-existing call site) is a byte-identical no-op."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if deadline_hit is not None:
            deadline_hit.hit = True
        return {}
    path_value = rm.get("path")
    if not path_value:
        return {}
    try:
        root = Path(str(path_value)).resolve()
    except OSError:
        return {}
    if not root.is_dir():
        return {}

    all_files = [str(f) for f in rm.get("files", [])]
    if not all_files:
        return {}

    from tensor_grep.cli.main import _BROAD_WORKSPACE_PROJECT_MARKERS

    # Candidate directories: every ancestor (strictly below root) of every scanned file. Bounded by
    # the already-scanned file set -- never an independent filesystem walk.
    candidate_dirs: set[Path] = set()
    for file_str in all_files:
        try:
            rel = Path(file_str).relative_to(root)
        except ValueError:
            continue
        parts = rel.parts[:-1]
        for i in range(1, len(parts) + 1):
            candidate_dirs.add(Path(*parts[:i]))
    if not candidate_dirs:
        return {}

    # STRONG-1: directory contains its own manifest.
    # #220: O(candidate_dirs x markers) `Path.exists()` calls -- on a repo with many candidate
    # directories this loop ALONE can consume the entire remaining --deadline budget in one
    # uninterrupted call (measured). Per-iteration check (mirrors `_build_context_pack_from_map`'s
    # own per-symbol loop): break and keep whatever partial `manifest_dirs` was already found --
    # additive de-weight evidence, so a partial pass under-detects (never mis-detects).
    manifest_dirs: dict[Path, str] = {}
    for rel_dir in candidate_dirs:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        abs_dir = root / rel_dir
        for marker in sorted(_BROAD_WORKSPACE_PROJECT_MARKERS):
            try:
                if (abs_dir / marker).exists():
                    manifest_dirs[rel_dir] = marker
                    break
            except OSError:
                continue

    # STRONG-0: a closed-vocabulary directory name is sufficient evidence entirely on its own -- see
    # the module comment above `_DEWEIGHT_FACTOR`. Matched on the directory's EXACT basename so only
    # the root itself is selected here, not every descendant of it (`.claude/hooks`,
    # `node_modules/react`, ...) -- those get covered by the outermost-nested-chain dedup below via
    # the base entry, exactly like a STRONG-1 manifest root covers its own descendants.
    tool_config_dirs: set[Path] = {
        rel_dir
        for rel_dir in candidate_dirs
        if rel_dir.parts and rel_dir.parts[-1].lower() in _TOOL_CONFIG_DIR_NAMES
    }
    strong0_vendor_dirs: set[Path] = {
        rel_dir
        for rel_dir in candidate_dirs
        if rel_dir.parts and rel_dir.parts[-1].lower() in _STRONG0_VENDOR_DIR_NAMES
    }
    # STRONG-3 candidates: any directory literally named `skills` (a plain top-level `skills/`, a
    # nested `core/skills/`, or one inside a tool-config dir like `.claude/skills` alike -- a
    # redundant nested candidate is deduped by the outermost-chain pass below same as any other
    # kind). Membership in `skill_leaf_dirs` (the SHAPE-validated subset) is decided just below,
    # once the import graph is available; an un-validated candidate here does not by itself cause a
    # de-weight.
    skill_candidate_dirs = [
        rel_dir
        for rel_dir in candidate_dirs
        if rel_dir.parts and rel_dir.parts[-1].lower() == "skills"
    ]

    if not (manifest_dirs or tool_config_dirs or strong0_vendor_dirs or skill_candidate_dirs):
        return {}

    # #220 Opus-gate follow-up: the two most expensive REMAINING sections -- the reverse-import
    # graph re-derivation just below AND the STRONG-3 skill-leaf validation loop that consumes it
    # (`_is_skill_leaf_tree`, `iterdir()`-heavy per `skill_candidate_dirs` entry) -- ran
    # unconditionally even after the manifest-probe loop above already broke on a tripped deadline:
    # that loop's own per-iteration check only bounds ITSELF, and the outermost-dedup loop's check
    # further below only bounds what comes AFTER it, leaving exactly this middle section open.
    # Measured on the real workspace shape that motivated this fix: 241 skills-named directories /
    # 2,933 child directories -> ~0.69s warm, low-seconds cold, entirely unbounded by either
    # existing check. One check here, in the SAME shape as every other bail-out in this function
    # (return the truncated-but-valid `{}` shape, never a partially-built dict missing the
    # STRONG-2/WEAK fields the rest of this function assumes), bounds both in one shot.
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if deadline_hit is not None:
            deadline_hit.hit = True
        return {}

    # The reverse-import graph is needed both for STRONG-3's external-import guard below and for the
    # main STRONG-2/WEAK evaluation loop further down -- compute it exactly ONCE and reuse (this reads
    # only `rm`'s already-in-hand "files"/"imports" lists, no new filesystem I/O). `resolved_imports`
    # (the forward edges) is not needed here since the STRONG-3 leaf test became manifest-based.
    _code_files, _resolved_imports, reverse_importers = _code_files_and_import_graph(rm)
    code_file_set = set(_code_files)

    # Precompute each code file's path-parts relative to root ONCE, on the SAME lexical basis the
    # candidate_dirs scan above used (`Path(f).relative_to(root)`; root is already resolved). Subtree
    # membership is then a cheap tuple-prefix test -- NO per-(subtree, file) filesystem resolve. The
    # old `_path_is_relative_to(Path(f), abs_dir)` did two `.resolve()` (realpath) syscalls per pair,
    # i.e. O(subtrees x files) realpath calls: ~15% of `tg agent` wall on an 872-file repo and
    # pathological on WSL 9p mounts (profile 2026-07-11). A file not lexically under root never
    # contributed a candidate dir, so it cannot belong to any detected subtree -- skipping it here is
    # consistent with how the subtrees were found.
    code_rel_parts: list[tuple[str, tuple[str, ...]]] = []
    for f in code_file_set:
        try:
            code_rel_parts.append((f, Path(f).relative_to(root).parts))
        except ValueError:
            continue

    skill_leaf_dirs: set[Path] = set()
    for rel_dir in skill_candidate_dirs:
        depth = len(rel_dir.parts)
        tree_files_for_skill_check = {
            f for f, parts in code_rel_parts if parts[:depth] == rel_dir.parts
        }
        if _is_skill_leaf_tree(
            root / rel_dir,
            tree_files_for_skill_check,
            reverse_importers=reverse_importers,
        ):
            skill_leaf_dirs.add(rel_dir)

    if not (manifest_dirs or tool_config_dirs or strong0_vendor_dirs or skill_leaf_dirs):
        return {}

    # Keep only the OUTERMOST directory (any matched kind) in any nested chain -- a deeper match
    # inside an already-flagged subtree does not start a second, overlapping subtree (M1 task 3:
    # e.g. a `core/skills/` STRONG-3 match must swallow a nested manifest island like
    # `core/skills/x/` rather than emitting both globs).
    all_candidate_roots = (
        manifest_dirs.keys() | tool_config_dirs | strong0_vendor_dirs | skill_leaf_dirs
    )
    # #220: O(candidate_roots^2)-shaped worst case (each candidate compared against every
    # already-accepted outer root) -- the single largest measured contributor to this function's
    # cost (~1.2s of a ~1.2s total call on a 40-manifest-dir tree; scales further with more
    # manifest-bearing directories, e.g. a nested monorepo `packages/*/package.json` shape).
    # Per-iteration check, same shape as the manifest-probe loop above: break and keep whatever
    # partial `subtree_rel_roots` was already deduped -- a partial dedup can at worst leave a
    # redundant NESTED entry alongside its already-accepted outer parent (never a correctness bug:
    # the ranking consumer below stops at the FIRST matching tree_root per file, so an overlapping
    # entry is never double-applied).
    #
    # #222 (real-workspace-scale residual of #220/#669): the per-iteration check above bounds the
    # ITERATION COUNT, but each iteration's own cost used to be `_path_is_relative_to(root /
    # rel_dir, root / existing)` -- TWO real `Path.resolve()` filesystem syscalls (Windows
    # `nt._getfinalpathname`, independently measured expensive -- see
    # `_precomputed_validation_files_for_root`'s docstring) PER (candidate, already-accepted)
    # pair, so a SINGLE outer iteration's cost grows with `len(subtree_rel_roots)` -- on a
    # candidate-cardinality synthetic (~40 sibling repos with manifest-heavy dependency trees) a
    # single call's unbounded cost scaled super-linearly (~quadratic: 7.7s at 120 candidates,
    # 20.7s at 200, 40.9s at 304; 88-92% of wall-clock inside this one genexpr, ~61% inside
    # `nt._getfinalpathname` alone), so on a real workspace with thousands of manifest-bearing
    # directories a handful of late, expensive iterations can blow tens of seconds past
    # --deadline between one checkpoint and the next.
    #
    # Fix: `rel_dir`/`existing` are BOTH already lexically relative to the SAME resolved `root`
    # (built from `candidate_dirs` above via a plain `.relative_to(root)` -- no resolve() of its
    # own), so "is root/rel_dir nested under root/existing" is exactly a `.parts` PREFIX test --
    # no filesystem I/O needed. Same fix shape this codebase already uses twice: this function's
    # own STRONG-3 code-file-membership test above (`code_rel_parts`, replaced an identical
    # two-resolve `_path_is_relative_to` call for the same reason) and
    # `_reverse_importer_proximity_tier`'s `_tier` helper (repo_map.py, PR #670): "resolve once /
    # reuse / avoid re-resolving via `_path_is_relative_to`" in a hot per-candidate loop. This
    # function is documented as purely ADDITIVE de-weighting evidence (never hard-exclude, see
    # the module comment above `_DEWEIGHT_FACTOR`), so trading real-symlink-aware nesting for
    # lexical nesting is the same accepted tradeoff the STRONG-3 fix already made -- worst case a
    # symlinked subtree dedupes against its lexical parent instead of its resolved one, never a
    # crash or a violation of "never exclude."
    subtree_rel_roots: list[Path] = []
    for rel_dir in sorted(all_candidate_roots, key=lambda p: len(p.parts)):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        rel_dir_parts = rel_dir.parts
        if any(
            existing.parts == rel_dir_parts[: len(existing.parts)] for existing in subtree_rel_roots
        ):
            continue
        subtree_rel_roots.append(rel_dir)

    result: dict[str, dict[str, Any]] = {}
    for rel_dir in subtree_rel_roots:
        abs_dir = root / rel_dir
        prefix = rel_dir.parts
        depth = len(prefix)
        tree_files = {f for f, parts in code_rel_parts if parts[:depth] == prefix}
        # A STRONG-3 skill-leaf match was already validated by `_is_skill_leaf_tree` against the
        # REAL filesystem child-directory shape, independent of whether the tree happens to
        # contain any CODE files at all -- a leaf-skill folder is commonly pure Markdown (its own
        # `SKILL.md` and nothing else). Skipping it here on an empty `tree_files` (a guard whose
        # purpose is "nothing to de-weight in the code-centrality ranking, so this candidate is a
        # no-op") would silently drop the exact all-Markdown shape M1 exists to catch: an agent's
        # `--ignore`-glob hint (and `tg agent`'s own text-ranking de-weight, which scores ALL
        # files, not just code) still benefits from the tree being reported even with zero code
        # files inside it. Every OTHER mechanism (manifest/tool-config/vendor-name/name-prior/
        # import-island) keeps requiring a non-empty `tree_files`, matching pre-M1 behavior.
        if not tree_files and rel_dir not in skill_leaf_dirs:
            continue
        # An import-island needs POSITIVE evidence of an internally-cohesive-but-externally-isolated
        # cluster: some file in the subtree is imported by ANOTHER file in it, AND no file outside it
        # imports in. A subtree with ZERO import edges -- a non-Python crate like `rust_core/`, invisible
        # to this Python-centric stem graph -- trivially satisfies "externally isolated" but is NOT an
        # island, just graph-invisible; it must NOT be de-weighted on STRONG-2 alone (else a legitimate
        # Rust/Go subproject with its own manifest gets buried). It can still fire on a name prior.
        externally_isolated = all(reverse_importers.get(f, set()) <= tree_files for f in tree_files)
        has_internal_edge = any(reverse_importers.get(f, set()) & tree_files for f in tree_files)
        is_island = externally_isolated and has_internal_edge
        name_hits = sorted({part.lower() for part in rel_dir.parts} & _VENDOR_NAME_PRIOR)
        is_tool_config = rel_dir in tool_config_dirs
        is_strong0_vendor = rel_dir in strong0_vendor_dirs
        is_skill_leaf = rel_dir in skill_leaf_dirs

        if not (is_island or name_hits or is_tool_config or is_strong0_vendor or is_skill_leaf):
            continue

        reasons: list[str] = []
        if rel_dir in manifest_dirs:
            reasons.append(f"nested-manifest:{manifest_dirs[rel_dir]}")
        if is_island:
            reasons.append("import-island")
        if name_hits:
            reasons.append(f"name-prior:{name_hits[0]}")
        if is_tool_config:
            reasons.append(f"tool-config-name:{rel_dir.parts[-1].lower()}")
        if is_strong0_vendor:
            reasons.append(f"vendor-name-strong0:{rel_dir.parts[-1].lower()}")
        if is_skill_leaf:
            reasons.append("skill-tree-shape")

        result[str(abs_dir)] = {"reasons": reasons, "ignore_glob": f"{rel_dir.as_posix()}/**"}

    return result


def _suggested_ignore_from_deweighted_trees(
    deweighted_trees: dict[str, dict[str, Any]],
) -> list[str] | None:
    """Ready-to-paste ``--ignore`` globs for each detected vendor/skill/tool-config subtree root
    (see ``_detect_vendored_subtrees``), e.g. ``.claude/**``. Shared by `tg orient`
    (``build_orient_capsule_from_map``) and `tg agent` (``agent_capsule.build_agent_capsule_from_map``,
    M2) so both surface the identical hint off the identical detection, rather than two
    independently hand-rolled copies of the same list comprehension. ``None`` -- never an empty
    list -- when nothing was deweighted, mirroring `suggested_scope`'s never-guess-empty convention
    (an agent can branch on `is None`)."""
    return [info["ignore_glob"] for _tree_path, info in sorted(deweighted_trees.items())] or None


def _file_centrality_scores(rm: dict[str, Any]) -> tuple[list[str], dict[str, float]]:
    """Composite per-file centrality (capped fan-in + fan-out + symbol density) over the non-doc,
    non-config code files in `rm`. Shared by `_central_files_from_map` (top-N central-file ranking)
    and `_suggested_scope_from_map` (directory rollup, audit #93 SUB-2) so both features read off
    the exact same score -- never a second, driftable scoring system."""
    code_files, resolved_imports, reverse_importers = _code_files_and_import_graph(rm)
    if not code_files:
        return [], {}
    code_file_set = set(code_files)
    # Composite centrality (dogfood 2026-07-03, v1.19.9): pure import in-degree surfaced LEAF data
    # files (constants.ts / figures.ts / barrel index.ts imported by many) at the top and buried the
    # real hubs (QueryEngine.ts, state.ts). A real architectural hub both RECEIVES and SENDS import
    # edges AND has substance (many symbols); a data sink only receives. So: cap the in-degree
    # contribution (a file imported by 50 is not proportionally more central than one imported by 12
    # -- past that it is a common utility/constant, not a hub), and ADD fan-out (imports others) +
    # symbol density. This demotes pure sinks without a fragile name/leaf heuristic.
    symbol_counts: dict[str, int] = {}
    for symbol in rm.get("symbols", []):
        symbol_file = str(symbol.get("file"))
        if symbol_file in code_file_set:
            symbol_counts[symbol_file] = symbol_counts.get(symbol_file, 0) + 1
    centrality: dict[str, float] = {}
    for source in code_files:
        fan_in = min(len(reverse_importers.get(source, ())), _CENTRAL_FAN_IN_CAP)
        fan_out = len(resolved_imports.get(source, []))
        density = min(symbol_counts.get(source, 0), _CENTRAL_SYMBOL_DENSITY_CAP)
        centrality[source] = float(fan_in + fan_out + density)
    return code_files, centrality


def _central_files_from_map(
    rm: dict[str, Any],
    *,
    max_central_files: int,
    auto_deweight: bool = True,
    deweighted_trees: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rank source files by import in-degree (foundational = imported-by-many); top-N with symbols.

    Files inside a detected vendored/skill subtree (see ``_detect_vendored_subtrees``) have their
    composite score multiplied by ``_DEWEIGHT_FACTOR`` -- DE-WEIGHTED, never removed, so a genuinely
    central file inside such a tree can still surface. The de-weight is applied HERE (not in
    `_file_centrality_scores`) so `_suggested_scope_from_map` keeps reading the raw, un-de-weighted
    score -- matching the WIP's original scope (central_files only)."""
    code_files, centrality = _file_centrality_scores(rm)
    if not code_files:
        return []
    if deweighted_trees is None:
        deweighted_trees = _detect_vendored_subtrees(rm) if auto_deweight else {}
    tree_roots = list(deweighted_trees.keys())
    for source in list(centrality):
        candidate = Path(source)
        for tree_root in tree_roots:
            try:
                candidate.relative_to(tree_root)
            except ValueError:
                continue
            centrality[source] *= _DEWEIGHT_FACTOR
            break
    ranked = sorted(code_files, key=lambda source: (-centrality[source], source))
    result: list[dict[str, Any]] = []
    for file_path in ranked[:max_central_files]:
        file_symbols = [
            {"name": str(s["name"]), "kind": str(s["kind"])}
            for s in rm.get("symbols", [])
            if str(s.get("file")) == file_path
        ][:6]
        rounded_score = round(centrality[file_path], 6)
        result.append({
            "file": file_path,
            # `graph_score` is the composite centrality; `score` is a stable alias so agents that
            # threshold on a generic `score` key find it populated (dogfood v1.20.0: "central_files
            # JSON still has score: null — surface the score so agents can threshold").
            "graph_score": rounded_score,
            "score": rounded_score,
            "symbols": file_symbols,
        })
    return result


# suggested_scope (audit #93 SUB-2): a truncated scan gives an agent an incomplete map with no
# guidance on how to narrow it. When the top-level-directory rollup of `_file_centrality_scores`
# shows a clear winner, suggest re-scoping to it; a tie or near-tie degrades to None rather than
# guess (ranking-safety-floor discipline, memory: tensor-grep-idf-ranking-fragility-2026-06-29 --
# this inherits the same flat, no-IDF-style composite score as central_files, so a wrong scope
# guess would actively misdirect an agent, which is worse than no hint at all). The margin is a
# ratio, not a fixed delta, so it scales with repos of very different absolute centrality sizes.
_SUGGESTED_SCOPE_MIN_MARGIN_RATIO = 1.5


def _top_level_dir(file_path: str, root: Path) -> str | None:
    """First path component of `file_path` relative to `root`, or None for a file that lives
    directly at the repo root (no subdirectory exists there to re-scope into)."""
    try:
        relative = Path(file_path).relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2:
        return None
    return parts[0]


def _file_in_any_tree(file_path: str, tree_roots: list[str]) -> bool:
    """True if `file_path` lives inside any of `tree_roots` (absolute directory paths -- the
    ``_detect_vendored_subtrees`` dict keys). Standalone helper (#168) rather than reusing
    `_central_files_from_map`'s inline tree-membership loop, so that function's existing,
    already-tested de-weight logic is left untouched."""
    candidate = Path(file_path)
    for tree_root in tree_roots:
        try:
            candidate.relative_to(tree_root)
        except ValueError:
            continue
        return True
    return False


def _suggested_scope_from_map(
    rm: dict[str, Any],
    *,
    deweighted_trees: dict[str, dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any] | None:
    """Centrality-weighted directory rollup: sum each code file's composite centrality
    (`_file_centrality_scores`) up to its top-level directory, rank directories, and suggest the
    top one only when it clearly outranks the runner-up. Returns None (never a guess) when there
    are no candidate subdirectories, the signal is entirely flat (all zero), or the top two
    directories are tied/near-tied. Callers gate the call itself on the repo map's
    ``scan_limit.possibly_truncated`` -- a complete scan has nothing left to narrow.

    ``deweighted_trees`` (#168): the same auto-detected vendor/skill/tool-config subtree set
    ``_detect_vendored_subtrees`` produces for ``suggested_ignore`` (e.g. ``.claude/**`` on a
    Claude-Code-harness repo whose scan truncates). A file inside any of these trees is EXCLUDED
    from the directory rollup entirely -- not merely de-weighted -- so `suggested_scope` can never
    point an agent at the exact tree `suggested_ignore` already says to ignore; the two fields must
    never contradict each other. This only shrinks the candidate set: whatever directory remains is
    still ranked on the raw, un-de-weighted score (preserving the SUB-2 design -- see
    `_central_files_from_map`'s docstring). ``None``/omitted -- the default -- means "nothing to
    exclude" and reproduces the pre-#168 behavior exactly; the `agent_capsule.py` and `repo_map.py`
    call sites do not thread a deweight set through yet and are unaffected by this parameter.

    ``deadline_monotonic`` (agent cold-path assembly-tail SLA fix): this rollup's own
    `_file_centrality_scores` call re-derives the whole-repo import graph (`_code_files_and_
    import_graph`), a second such derivation on top of `_detect_vendored_subtrees`'s own -- real
    but smaller cost than that function on the profiled synthetic tree. An optional PRE-ANCHORED
    absolute ``time.monotonic()`` budget: when the shared assembly budget is already exhausted,
    skip the rollup and return ``None`` -- the SAME "no suggestion" shape this function already
    returns for a flat/tied/signal-free repo, so callers need no new branch. Default ``None``
    (every pre-existing call site) is a byte-identical no-op."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return None
    code_files, centrality = _file_centrality_scores(rm)
    if not code_files:
        return None
    root = Path(str(rm.get("path", ".")))
    tree_roots = list(deweighted_trees.keys()) if deweighted_trees else []
    dir_scores: dict[str, float] = {}
    for file_path in code_files:
        if tree_roots and _file_in_any_tree(file_path, tree_roots):
            continue  # #168: never roll an ignored tree's files into a scope candidate
        top_dir = _top_level_dir(file_path, root)
        if top_dir is None:
            continue
        dir_scores[top_dir] = dir_scores.get(top_dir, 0.0) + centrality.get(file_path, 0.0)
    if not dir_scores:
        return None
    ranked_dirs = sorted(dir_scores, key=lambda d: (-dir_scores[d], d))
    top_score = dir_scores[ranked_dirs[0]]
    if top_score <= 0:
        return None  # no signal at all -- nothing to distinguish a "highest-value" directory
    if len(ranked_dirs) > 1:
        runner_up_score = dir_scores[ranked_dirs[1]]
        if runner_up_score > 0 and top_score < runner_up_score * _SUGGESTED_SCOPE_MIN_MARGIN_RATIO:
            return None  # no clear winner -- degrade to null rather than risk a misleading guess
    return {
        "dirs": [str(root / ranked_dirs[0])],
        "confidence": "heuristic",
    }


def _detect_entry_points(rm: dict[str, Any]) -> list[dict[str, Any]]:
    """Heuristic: files named main.py / cli.py / index.ts / lib.rs etc."""
    result: list[dict[str, Any]] = []
    for file_path in rm.get("files", []):
        if Path(str(file_path)).name.lower() in _ENTRY_NAMES:
            result.append({"file": str(file_path), "reason": "entry-name-heuristic"})
    return result


def _ast_chunked_snippet(path_str: str, symbols: list[dict[str, Any]]) -> str | None:
    """Return the source of the first resolvable symbol via the AST/regex symbol-source chunkers."""
    path = Path(path_str)
    for sym in symbols:
        name = str(sym.get("name", ""))
        if not name:
            continue
        sources = _repo_map._python_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._js_ts_parser_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._rust_parser_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._regex_symbol_sources(path, name)
        if sources:
            return str(sources[0].get("source", ""))
    return None


def _apply_ignore_globs(rm: dict[str, Any], ignore: tuple[str, ...]) -> dict[str, Any]:
    """Drop files matching any --ignore glob (basename OR repo-relative posix path) from the map
    before ranking (1.35 dogfood): `tg orient . --ignore 'seo/**' 'core/skills/**'` excludes vendor /
    skill trees that would otherwise rank as 'central' on a doc- or harness-heavy repo, even though
    they are .py CODE (so the doc/config suffix exclusions don't catch them)."""
    if not ignore:
        return rm
    root = Path(str(rm.get("path", ".")))

    def _excluded(file_str: str) -> bool:
        candidate = Path(file_str)
        try:
            rel = candidate.relative_to(root).as_posix()
        except ValueError:
            rel = candidate.as_posix()
        return any(
            fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(candidate.name, glob) for glob in ignore
        )

    filtered = dict(rm)
    filtered["files"] = [f for f in rm.get("files", []) if not _excluded(str(f))]
    filtered["symbols"] = [
        s for s in rm.get("symbols", []) if not _excluded(str(s.get("file", "")))
    ]
    filtered["imports"] = [
        i for i in rm.get("imports", []) if not _excluded(str(i.get("file", "")))
    ]
    return filtered


def build_orient_capsule(
    path: str | Path = ".",
    *,
    max_central_files: int = 10,
    max_snippet_files: int = 5,
    max_tokens: int = 3000,
    max_repo_files: int | None = None,
    render_profile: str = "compact",
    ignore: tuple[str, ...] = (),
    auto_deweight: bool = True,
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Build a bounded codebase orientation capsule (no API key, no GPU).

    ``auto_deweight`` (default on) DE-WEIGHTS -- never hard-excludes -- auto-detected bundled
    vendor/skill/generated CODE subtrees in the centrality ranking (see
    ``_detect_vendored_subtrees``); pass ``auto_deweight=False`` (CLI: ``--no-auto-deweight``) to
    disable. This is independent of ``--ignore``, which still hard-excludes explicit globs.

    Thin cold-path wrapper: build the repo map (the only expensive step, task #108) and delegate
    everything else to ``build_orient_capsule_from_map`` so the warm session-daemon fast path
    (which reuses an already-cached map) shares one code path with the cold path -- parity by
    construction rather than a second, driftable implementation.

    ``deadline_seconds`` (CLI consistency fix, CEO v1.71.3 dogfood): `--deadline` used to be
    undefined on `tg orient` (Click "No such option" exit-2). Bounds the underlying
    ``build_repo_map`` walk/parse the same way the symbol commands do; `tg orient` has NO exit-2
    contract (docs/CONTRACTS.md), so a truncated scan still surfaces `partial`/`deadline_limit` as
    INFORMATIONAL fields only (see `build_orient_capsule_from_map`), never a retry signal.

    ``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline, used AS-IS instead of being recomputed from
    ``deadline_seconds`` when supplied. The CLI cold path (``main.orient``) anchors it at command
    entry, before the lazy import and the daemon gate, so front-door time is budgeted the same way
    scan time already is. Existing ``deadline_seconds``-only callers are unaffected: the fallback
    computation below is byte-identical to the prior behavior.
    """
    from tensor_grep.cli.repo_map import (
        DEFAULT_AGENT_REPO_MAP_LIMIT,
        _deadline_monotonic_from_seconds,
    )

    effective_max_repo_files = (
        max_repo_files if max_repo_files is not None else DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    if deadline_monotonic is None:
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    rm = _repo_map.build_repo_map(
        path, max_repo_files=effective_max_repo_files, deadline_monotonic=deadline_monotonic
    )
    return build_orient_capsule_from_map(
        rm,
        max_central_files=max_central_files,
        max_snippet_files=max_snippet_files,
        max_tokens=max_tokens,
        render_profile=render_profile,
        ignore=ignore,
        auto_deweight=auto_deweight,
    )


def build_orient_capsule_from_map(
    rm: dict[str, Any],
    *,
    max_central_files: int = 10,
    max_snippet_files: int = 5,
    max_tokens: int = 3000,
    render_profile: str = "compact",
    ignore: tuple[str, ...] = (),
    auto_deweight: bool = True,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Task #108 (Tier-2 daemon moat): the map-based core of ``build_orient_capsule``, taking an
    already-built ``rm`` (e.g. the warm session daemon's cached ``repo_map``) instead of scanning
    the filesystem itself. ``build_orient_capsule`` is a thin wrapper around this function, so
    cold and warm output are identical by construction for the same map.

    ``max_repo_files`` is deliberately NOT a parameter here: the only place the cold wrapper uses
    it is to build ``rm`` (already done by the caller) and to echo it back in the ``scan_limit``
    result field -- reconstructed below from ``rm["scan_limit"]["max_repo_files"]`` (populated by
    ``build_repo_map`` whenever a cap was applied), so there is exactly one source of truth for
    what cap actually produced ``rm`` instead of a second, independently-supplied value that could
    drift from it.

    ``deadline_monotonic`` (#200): an ABSOLUTE ``time.monotonic()`` budget for THIS function's own
    post-map work (the snippet-building loop below reads real files via ``_ast_chunked_snippet``,
    and the centrality/entry-point passes above it are O(cached-map-size)). Unlike its siblings
    (``build_agent_capsule_from_map``, ``build_context_render_from_map``,
    ``build_context_edit_plan_from_map``), this function did NOT already accept one -- added here
    so the warm session daemon (``session_store._serve_session_request_from_payload``) can bound
    it the same way. Deliberately NOT threaded from ``build_orient_capsule`` (the cold CLI
    wrapper): ``tg orient`` intentionally stays unbounded by default on the cold path (see this
    module's ``build_orient_capsule`` docstring and its ``--no-deadline`` CLI help) -- only the
    warm daemon dispatch path supplies a value, so ``deadline_monotonic=None`` (every existing
    call site) remains a byte-identical no-op."""
    rm = _apply_ignore_globs(rm, ignore)

    deweighted_trees = _detect_vendored_subtrees(rm) if auto_deweight else {}
    central_files = _central_files_from_map(
        rm, max_central_files=max_central_files, deweighted_trees=deweighted_trees
    )
    entry_points = _detect_entry_points(rm)

    # suggested_scope (audit #93 SUB-2): gate on the underlying repo map's OWN scan_limit dict
    # (`rm["scan_limit"]["possibly_truncated"]`, set by `repo_map.build_repo_map` -- NOT this
    # capsule's own simplified `scan_limit` int returned below, and NOT the snippet/token-budget
    # `truncated` flag computed further down). A complete scan has no incomplete map to narrow.
    scan_limit_info = rm.get("scan_limit")
    scan_possibly_truncated = bool(
        isinstance(scan_limit_info, dict) and scan_limit_info.get("possibly_truncated")
    )
    # `deweighted_trees` (#168) is threaded through so suggested_scope can never point an agent at
    # the same tree suggested_ignore (below) already says to ignore -- the two fields must agree.
    suggested_scope = (
        _suggested_scope_from_map(rm, deweighted_trees=deweighted_trees)
        if scan_possibly_truncated
        else None
    )
    # The capsule's own simplified `scan_limit` int (see the docstring above): the cap that
    # produced `rm`, read back off the map itself rather than threaded through as a second,
    # independently-suppliable parameter.
    effective_max_repo_files = (
        scan_limit_info.get("max_repo_files") if isinstance(scan_limit_info, dict) else None
    )

    symbol_map: dict[str, list[dict[str, Any]]] = {}
    for cf in central_files:
        file_path = cf["file"]
        syms = [
            {
                "name": str(s["name"]),
                "kind": str(s["kind"]),
                "line": int(s.get("line", s.get("start_line", 0)) or 0),
            }
            for s in rm.get("symbols", [])
            if str(s.get("file")) == file_path
        ][:8]
        if syms:
            symbol_map[file_path] = syms

    snippets: list[dict[str, Any]] = []
    token_budget_used = 0
    budget_truncated = False
    # #200: the only real-file I/O left in this function (_ast_chunked_snippet reads+parses each
    # central file from disk) -- cheap monotonic check per iteration, mirrors the checkpoint
    # style build_context_pack_from_map's own per-symbol loop already uses.
    snippet_loop_deadline_hit = False
    for cf in central_files[:max_snippet_files]:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            snippet_loop_deadline_hit = True
            break
        file_path = cf["file"]
        snippet_text = _ast_chunked_snippet(file_path, symbol_map.get(file_path, []))
        if not snippet_text:
            continue
        snippet_tokens = _repo_map._estimate_tokens(snippet_text)
        if token_budget_used + snippet_tokens > max_tokens:
            # Budget can't fit this snippet whole -> content is being cut or dropped either way
            # (a partial snippet below, or an outright break). This is the accurate truncation
            # signal; the old `token_budget_used >= max_tokens` proxy false-flagged a snippet that
            # landed EXACTLY on the budget with nothing left to drop.
            budget_truncated = True
            remaining_chars = int((max_tokens - token_budget_used) * _CHARS_PER_TOKEN)
            if remaining_chars < 80:
                break
            snippets.append({
                "file": file_path,
                "source": snippet_text[:remaining_chars],
                "truncated": True,
            })
            token_budget_used = max_tokens
            break
        snippets.append({"file": file_path, "source": snippet_text, "truncated": False})
        token_budget_used += snippet_tokens

    deweighted_trees_list = [
        {"path": tree_path, "reasons": list(info["reasons"])}
        for tree_path, info in sorted(deweighted_trees.items())
    ]
    # suggested_ignore (#164; M1 extraction into `_suggested_ignore_from_deweighted_trees`): when
    # auto-deweight found something, surface the deweighted tree roots as ready-to-paste `--ignore`
    # globs (e.g. `.claude/**`) so an agent that wants a HARD exclude (not just a lowered score)
    # doesn't have to hand-derive the glob syntax.
    suggested_ignore = _suggested_ignore_from_deweighted_trees(deweighted_trees)

    lines: list[str] = [f"# Codebase orientation: {rm['path']}"]
    lines.append("\n## Central files (by import-graph centrality)")
    for cf in central_files:
        lines.append(f"- {cf['file']}  graph_score={cf['graph_score']}")
    if deweighted_trees_list:
        lines.append("\n## De-weighted vendor/skill subtrees (auto-detected, NOT excluded)")
        for tree in deweighted_trees_list:
            lines.append(f"- {tree['path']}  ({', '.join(tree['reasons'])})")
    if entry_points:
        lines.append("\n## Entry points (heuristic name detection)")
        for ep in entry_points:
            lines.append(f"- {ep['file']}  ({ep['reason']})")
    lines.append("\n## Symbol map (top symbols per central file)")
    for file_path, syms in symbol_map.items():
        sym_list = ", ".join(f"{s['kind']} {s['name']}" for s in syms)
        lines.append(f"- {file_path}: {sym_list}")
    if snippets:
        lines.append("\n## Key snippets (AST-boundary chunks)")
        for snip in snippets:
            lines.append(f"\n### {snip['file']}")
            lines.append(f"```\n{snip['source'].rstrip()}\n```")

    total_token_estimate = _repo_map._estimate_tokens("\n".join(lines))
    truncated = any(s.get("truncated") for s in snippets) or budget_truncated

    result = {
        "path": rm["path"],
        "central_files": central_files,
        "entry_points": entry_points,
        "symbol_map": symbol_map,
        "snippets": snippets,
        "token_estimate": total_token_estimate,
        "token_budget_label": (
            f"~{total_token_estimate} tokens (heuristic len/3.5); snippet budget {max_tokens}"
        ),
        "truncated": truncated,
        "scan_limit": effective_max_repo_files,
        "suggested_scope": suggested_scope,
        "suggested_ignore": suggested_ignore,
        "routing_reason": "orient",
        "deweighted_trees": deweighted_trees_list,
        "auto_deweight": auto_deweight,
    }
    # CLI consistency fix (CEO v1.71.3 dogfood): carry a --deadline truncation forward from `rm`
    # (mirrors repo_map._copy_partial_signal's shape) so a deadline-bounded scan is never silently
    # dropped. INFORMATIONAL only -- `tg orient` has NO exit-2 contract (docs/CONTRACTS.md:110), so
    # this does not change orient's documented always-exit-0 behavior; it only makes a truncated
    # scan visible in the payload, the same way `scan_limit`/`truncated` already are.
    _repo_map._copy_partial_signal(result, rm)
    # #200: final wall-clock catch-all, mirrors build_agent_capsule_from_map's own
    # deadline_exceeded_at_return check (agent_capsule.py). `_copy_partial_signal` above only
    # forwards a SCAN-level partial signal already present on `rm` (build_repo_map's own
    # --deadline, which the warm daemon path never re-applies since `rm` is an already-cached
    # map) -- this is the independent POST-map bound: even when the snippet loop above didn't
    # need to break early, the in-memory centrality/entry-point work before it is still
    # O(cached-map-size) and could itself have consumed the whole warm-daemon budget on an
    # unusually large cached map. Re-check the shared absolute deadline one final time before
    # returning, regardless of which part of this function actually consumed the time. No-op
    # when deadline_monotonic is None (every cold-path call), so cold output stays byte-identical.
    if deadline_monotonic is not None and (
        snippet_loop_deadline_hit or time.monotonic() >= deadline_monotonic
    ):
        result["partial"] = True
        result["partial_reason"] = "deadline"
        existing_deadline_limit = result.get("deadline_limit")
        result["deadline_limit"] = (
            dict(existing_deadline_limit)
            if isinstance(existing_deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
    return result


def build_orient_capsule_json(path: str | Path = ".", **kwargs: Any) -> str:
    """JSON form of :func:`build_orient_capsule`."""
    return json.dumps(build_orient_capsule(path, **kwargs), indent=2)
