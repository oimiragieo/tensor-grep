"""Zero-dependency scan-limit constants: the single source of truth for the search-time broad-scan
guards, shared across the Python front doors AND the Rust native front door.

perf (+10% campaign #6 / F2.4): these 5 constants used to live in ``io/directory_scanner.py``,
which does ``from tensor_grep.core.config import SearchConfig`` at ITS OWN module level -- and
``SearchConfig`` transitively pulls in the stdlib ``dataclasses`` module (which itself imports
``inspect``/``ast``/``dis``/``tokenize``/``copy``/``weakref``). A caller that only wants a plain
frozenset or int (``cli/bootstrap.py``'s 3 guard helpers, ``cli/main.py``'s module-level broad-scan
literals, ``cli/scan_guardrails.py``'s ``tg scan`` guard) had no way to get one WITHOUT also paying
for the whole ``SearchConfig`` chain, because importing ANY name from a module executes that
module's entire top level first. This module holds only frozenset/dict/int literals -- no imports
at all -- so any caller that needs just the constants can import from here directly and never touch
``SearchConfig``/``dataclasses``/``inspect``.

``io/directory_scanner.py`` re-exports every public name below (``import ... as ...``, the same
redundant-alias idiom already used by ``cli/bootstrap.py`` for ``run_subprocess``) so its public
surface is unchanged for any existing caller of ``tensor_grep.io.directory_scanner.<NAME>`` -- this
module is the canonical DEFINITION site, ``directory_scanner`` is a compatibility re-export.
``_GENERATED_DIR_NAMES`` also moves here (rather than staying a directory_scanner-local literal)
because ``UNBOUNDED_VENDORED_ROOT_DIR_NAMES`` below is DERIVED from it -- keeping the derivation and
its input in the same zero-dependency module avoids a second, driftable copy of ``_GENERATED_DIR_
NAMES`` living in ``directory_scanner.py`` just to feed a value re-exported from here.
"""

# Directory names the walker already treats as generated/skip-worthy. `io/directory_scanner.py`'s
# `DirectoryScanner._should_descend_dir` imports this back (it is the walker's own skip-list, not
# just an input to the derivation below) -- see the comment on `UNBOUNDED_VENDORED_ROOT_DIR_NAMES`
# for why a name already in here can never ALSO trigger the vendored-root refusal.
_GENERATED_DIR_NAMES = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

# Heavy dirs that may legitimately sit at a project ROOT's top level and that an unscoped
# `tg search` should refuse to blindly walk into (PR #400 fix C, review finding H1). A name
# already in `_GENERATED_DIR_NAMES` above (e.g. `node_modules`) is already walker-skipped by
# `_should_descend_dir` -- it can never cause the walker to hang on its own, so it must NOT
# also appear in the refusal trigger set below (a refusal for a dir the walker never
# descends is a pure false positive: it wrongly exit-2's every ordinary Node/React repo).
# Single source of truth: both `cli/main.py`'s `_should_refuse_unbounded_vendored_root_scan`
# and `cli/bootstrap.py`'s front-door mirror `_search_paths_include_vendored_root` import
# `UNBOUNDED_VENDORED_ROOT_DIR_NAMES` from here rather than each hardcoding their own
# (subtracted) copy, so the two guards can never drift out of sync.
_ALL_HEAVY_ROOT_DIR_NAMES = frozenset({
    "node_modules",
    "vendor",
    "external_repos",
    "third_party",
})
UNBOUNDED_VENDORED_ROOT_DIR_NAMES = frozenset(_ALL_HEAVY_ROOT_DIR_NAMES - _GENERATED_DIR_NAMES)

# Project-marker names that flag a directory as an independent project root, plus the
# child-count thresholds that decide when a root "looks like" a multi-project workspace
# parent (item #154). Single source of truth: both `cli/main.py`'s
# `_should_refuse_unbounded_workspace_root_scan` and `cli/bootstrap.py`'s front-door mirror
# `_search_paths_include_workspace_root` import `BROAD_WORKSPACE_PROJECT_MARKERS` and the two
# thresholds below from here rather than each hardcoding their own copy, so the two guards can
# never drift out of sync.
BROAD_WORKSPACE_PROJECT_MARKERS = frozenset({
    ".git",
    "Cargo.toml",
    "build.gradle",
    "composer.json",
    "deno.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "settings.gradle",
})
# An UNMARKED root (no project marker of its own -- e.g. a plain folder of unrelated repos) is
# flagged as a workspace parent once it has this many independently-marked children.
BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = 3
# A MARKED root (carries its own project marker, e.g. a top-level `package.json`) is NOT
# skipped outright (item #154, reported repro: an unscoped search from a workspace parent that
# itself has a top-level `package.json` timed out instead of fast-refusing): such a root can
# *also* be a workspace parent once it has enough independently-marked children. It uses a
# HIGHER threshold than an unmarked root, since one ordinary project can legitimately carry a
# handful of marked children (a Cargo workspace member, a vendored submodule) without itself
# being a workspace parent.
BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = 8

# Item #105-parity (bootstrap raw-rg-passthrough gap, CEO dogfood v1.92.x): the ceiling above
# which an IMPLICIT-path (no explicit PATH positional) search walk must be refused rather than
# run to completion/timeout. Neither the workspace-root guard above (needs >=3/>=8
# independently-MARKED sibling dirs) nor the vendored-root guard (needs a top-level vendored dir
# NAME) catches a plain, single, large repo root -- e.g. a flat monorepo `src/` with thousands of
# files, no vendored subdir, and no marked siblings. `cli/main.py`'s `_LARGE_ROOT_SCAN_FILE_CEILING`
# (Bug #88 fix, the full-CLI-side guard) and `cli/bootstrap.py`'s front-door mirror
# `_search_paths_include_oversized_implicit_root` both import this single source of truth so the
# two guards can never drift out of sync (same pattern as `UNBOUNDED_VENDORED_ROOT_DIR_NAMES` and
# `BROAD_WORKSPACE_PROJECT_MARKERS` above). Matches the Rust native front door's own
# `IMPLICIT_SEARCH_WALK_FILE_CEILING` (`rust_core/src/rg_passthrough.rs`, audit #100/#105/#109) --
# kept at the same numeric value by convention across the language boundary (a literal constant
# cannot be shared across Python/Rust).
IMPLICIT_SEARCH_WALK_FILE_CEILING = 1500
