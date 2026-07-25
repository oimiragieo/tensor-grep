"""Task #269: shared root-ignore-file discovery for the Python-only real-`rg` forwarding paths.

Both `bootstrap.py::_run_rg_passthrough` and `backends/ripgrep_backend.py::RipgrepBackend.
_build_cmd` shell out to a real `rg` binary and are reachable whenever no compiled native `tg`
binary is discoverable (the pip/uvx pure-Python install channel). Real `rg`'s own automatic
`.gitignore` discovery requires `require_git=true` by default, so a root `.gitignore` is silently
a no-op OUTSIDE a git repository -- the exact defect task #264 fixed for the COMPILED native
binary's rg-passthrough (`rust_core/src/rg_passthrough.rs::root_ignore_file_args`), but which was
deliberately left open on both Python-only paths (see that PR's outcome test docstring). This
module ports that Rust function's shape verbatim for Python so all THREE implementations
(compiled-native Rust, this module's two Python call sites) cannot silently diverge -- two
implementations that disagree would recreate the very bug class this closes.

Root-only, exactly matching the native engine's scope (`native_search.rs::build_walk_builder` /
`index.rs::collect_file_entries`'s unconditional `add_ignore` trio): no parent-directory ascent,
no nested-directory walk. NOT `--no-require-git`: that would additionally pull in nested and
global gitignores via rg's normal parent-ascending discovery, diverging from tg's deliberately
root-only scope (`docs/BACKLOG.md:154`).
"""

from __future__ import annotations

from pathlib import Path

#: Root-only ignore filenames tg treats as ignore sources, in ASCENDING precedence order (rg
#: docs: "When specifying multiple ignore files, earlier files have lower precedence than later
#: files") -- exactly mirroring `rust_core/src/rg_passthrough.rs::ROOT_IGNORE_FILENAMES`.
ROOT_IGNORE_FILENAMES: tuple[str, str, str] = (".ignore", ".gitignore", ".rgignore")


def root_ignore_file_args(
    roots: list[str] | None,
    *,
    no_ignore: bool,
    no_ignore_files: bool,
    no_ignore_vcs: bool,
    no_ignore_dot: bool,
    unrestricted: int = 0,
) -> list[str]:
    """Return `["--ignore-file", path, ...]` operands for every discovered root ignore file.

    SECURITY / correctness (verified live against the shipped rg 15.1.0 during #744, reused
    here rather than re-derived): an explicit `--ignore-file <path>` is NOT cancelled by
    `--no-ignore` or `--no-ignore-vcs` -- rg's own `--no-ignore` help text says so verbatim
    ("This does not imply --no-ignore-files, since --ignore-file is specified explicitly as a
    command line argument"). Blindly emitting `--ignore-file` would therefore silently
    RESURRECT ignore rules the user explicitly asked to disable, so every flag that can disable
    root-ignore honoring is checked BEFORE emitting the matching `--ignore-file`, matching rg's
    own documented per-flag scope:
      - ``no_ignore``       -- matches the native engine's own single-flag gate; skip all three.
      - ``no_ignore_files``  -- rg cancels any ``--ignore-file`` regardless of argv order per its
        own docs ("even ones that come after this flag, are ignored"); short-circuited here too
        so nothing is emitted at all.
      - ``no_ignore_vcs``   -- rg's docs restrict this to source-control ignore files ("only
        respect rules in .ignore or .rgignore"); skip only ``.gitignore``.
      - ``no_ignore_dot``   -- rg's docs restrict this to ``.ignore``/``.rgignore`` ("Don't
        respect filter rules from .ignore or .rgignore files ... does not impact whether filter
        rules from .gitignore files are respected"); skip only those two.
      - ``unrestricted``    -- rg's `-u`/`-uu`/`-uuu` is a documented ALIAS: `-u` expands to
        `--no-ignore` (`-uu` additionally implies `--hidden`, `-uuu` additionally implies
        `--binary`), so any count > 0 already implies `--no-ignore` and must gate exactly like
        it (independent-gate finding on task #269: this was originally missed because
        ``ripgrep_backend.py``'s ``config.no_ignore`` and clap's own `-u` expansion are two
        separate code paths -- neither call site alone observes both. Without this, `-u` would
        become STRICTER than no flag at all: the emitted `--ignore-file` survives `-u`, per the
        `no_ignore` bullet above, and silently re-applies rules `-u` asked to disable).
    ``no_ignore_exclude``/``no_ignore_global``/``no_ignore_parent`` are deliberately NOT
    parameters: none of them govern a root ``.ignore``/``.gitignore``/``.rgignore`` file (they
    gate ``.git/info/exclude``, the global git ignore config, and parent-directory ignore-file
    ascent respectively), so they have no bearing on what this function emits.

    KNOWN GAP, low priority, flagged not fixed (independent-gate non-blocking note, task #269;
    correction applied on re-gate -- the original wording here overstated it): rg's
    `--ignore`/`--ignore-vcs`/`--ignore-dot`/`--ignore-files` RE-ENABLE flags are not parameters
    of THIS function. rg is last-wins on repeated ignore flags, so e.g. `--no-ignore
    --ignore-vcs` (disable everything, then re-enable VCS-scoped honoring) should behave like a
    bare search for `.gitignore`, but since this function only receives the `no_ignore*`
    booleans, it returns `[]` for both that case and a bare `--no-ignore` with no re-enable.
    This is NOT because a caller cannot tell the difference -- `RipgrepBackend`'s own
    `SearchConfig` carries `ignore`/`ignore_dot`/`ignore_files`/`ignore_vcs` and forwards them
    to rg directly elsewhere in `_build_cmd` (each guarded by its own `if config.ignore:` /
    `if config.ignore_dot:` / `if config.ignore_files:` / `if config.ignore_vcs:` check in
    `ripgrep_backend.py` -- cited by SHAPE rather than a line number on purpose, per the NB-2
    lesson from this task's independent gate: a raw line-number citation drifted stale within
    the SAME commit that added it, when an unrelated comment inserted earlier in the file
    shifted every line below it), and `bootstrap.py`'s raw-argv caller could detect the same
    long-flag tokens via the identical `in search_args` style already used for
    `no_ignore`/`no_ignore_files`/etc. It is that neither call site currently THREADS that
    signal into this function's gating -- a scope decision, not an inherent inability. The
    failure direction is the SAFE one: under-EMITTING (falling back to whatever rg's own
    auto-discovery does, i.e. nothing outside a git repo) is a missed convenience, not a
    resurrected-ignore-rule regression, so it is not gated here.

    ``roots`` mirrors rg's own ``args.paths``: an empty/``None`` list defaults to a single ``.``
    root (an implicit search still has an implicit cwd root to check).
    """
    if no_ignore or no_ignore_files or unrestricted > 0:
        return []

    effective_roots = roots or ["."]
    operands: list[str] = []
    for root in effective_roots:
        root_path = Path(root)
        for ignore_name in ROOT_IGNORE_FILENAMES:
            if ignore_name == ".gitignore" and no_ignore_vcs:
                continue
            if ignore_name != ".gitignore" and no_ignore_dot:
                continue
            ignore_path = root_path / ignore_name
            if ignore_path.is_file():
                operands.append("--ignore-file")
                operands.append(str(ignore_path))
    return operands
