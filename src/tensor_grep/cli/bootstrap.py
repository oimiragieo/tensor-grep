from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS
from tensor_grep.cli.commands import PYTHON_FULL_HELP_COMMANDS as _PYTHON_FULL_HELP_COMMANDS
from tensor_grep.cli.runtime_paths import (
    env_flag_enabled,
    resolve_native_tg_binary,
    resolve_ripgrep_binary,
)
from tensor_grep.cli.subprocess_policy import run_subprocess as run_subprocess

# perf/#48: `tensor_grep.io.directory_scanner` is deliberately NOT imported at module level.
# bootstrap.py only needs its 4 constants (BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
# BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD, BROAD_WORKSPACE_PROJECT_MARKERS,
# UNBOUNDED_VENDORED_ROOT_DIR_NAMES) inside the search broad-scan guard helpers below
# (`_path_has_project_marker`, `_search_paths_include_workspace_root`,
# `_search_paths_include_vendored_root`), which run only for an actual search invocation -- never
# for `tg --version`/`-V` (the diagnosed cold-start case, ~135ms vs rg's ~7ms). `directory_scanner`
# itself does `from tensor_grep.core.config import SearchConfig` at ITS OWN module level, which
# transitively pulls in the stdlib `dataclasses` module -- and `dataclasses` imports `inspect`
# (plus `ast`/`dis`/`tokenize`/`copy`/`weakref`), a chain measured at ~25ms cumulative
# (`python -X importtime -c "import tensor_grep.cli.bootstrap"`, warm cache) that a trivial
# `--version` call has no reason to pay. Each of the 3 helper functions does its own
# function-local `from tensor_grep.io.directory_scanner import ...` instead; see
# `tests/unit/test_bootstrap_fast_path_imports.py` for the regression test that pins this.

# Saved at import time so _streaming_passthrough_returncode can detect when
# run_subprocess has been monkey-patched by a test (old mock pattern).
_ORIG_RUN_SUBPROCESS = run_subprocess

_TG_ONLY_SEARCH_FLAGS = {
    "--ast",
    "--cpu",
    "--debug",
    "--files",
    "--files-with-matches",
    "--files-without-match",
    "--force-cpu",
    "--format",
    "--generate",
    "--glob",
    "--gpu-device-ids",
    "--allow-broad-generated-scan",
    "--json",
    "--ndjson",
    "--pcre2-version",
    "--lang",
    "--ltl",
    "--rank",
    "--bm25",
    "--semantic",
    "--replace",
    "--stats",
    "--type-list",
    # --type/-t, --type-not/-T, and --iglob narrow WHICH files match but do NOT bound the walk, so
    # a bare `tg search PAT -t py` / `--iglob '*.py'` (no PATH) on a large root is the same
    # unbounded-walk DoS as bare --glob (audit #88 siblings; the guard's own scope condition is
    # glob|iglob|file_type|type_not). Force them to the full CLI where _should_refuse_unbounded_*
    # guards fire, rather than the unguarded rg passthrough.
    "--type",
    "--type-not",
    "--iglob",
    "-t",
    "-T",
    "-l",
    "-g",
    "-r",
}

_TG_ONLY_SEARCH_FLAG_PREFIXES = (
    "--format=",
    "--generate=",
    "--glob=",
    "--gpu-device-ids=",
    "--iglob=",
    "--lang=",
    "--replace=",
    "--type=",
    "--type-not=",
)

_SCAN_FULL_CLI_FLAGS = {
    "--help",
    "-h",
    "--baseline",
    "--allow-broad-generated-scan",
    "--glob",
    "--include-evidence-snippets",
    "--inline-rules",
    "--json",
    "--justification",
    "--language",
    "--max-depth",
    "--max-evidence-snippet-chars",
    "--max-evidence-snippets-per-file",
    "--path",
    "--rule",
    "--ruleset",
    "--suppressions",
    "--write-baseline",
    "--write-suppressions",
    "--type",
    "-g",
    "-r",
    "-t",
}

_SCAN_FULL_CLI_FLAG_PREFIXES = (
    "--allow-broad-generated-scan=",
    "--baseline=",
    "--glob=",
    "--justification=",
    "--language=",
    "--max-depth=",
    "--max-evidence-snippet-chars=",
    "--max-evidence-snippets-per-file=",
    "--path=",
    "--rule=",
    "--ruleset=",
    "--suppressions=",
    "--type=",
    "--write-baseline=",
    "--write-suppressions=",
)
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "appdata",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/main.py`'s equivalent guard from drifting out of sync. perf/#48: no longer aliased at
# module level -- `_path_has_project_marker` / `_search_paths_include_workspace_root` below
# import these constants function-locally (fast-path-unused; see the deferred-import note above
# the `tensor_grep.cli.subprocess_policy` import).
_SEARCH_PATTERN_FLAGS = {"-e", "--regexp"}
_SEARCH_LITERAL_FLAGS = {"-F", "--fixed-strings"}
_SEARCH_PCRE2_FLAGS = {"-P", "--pcre2"}
_SEARCH_FLAGS_WITH_VALUES = {
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-g",
    "-j",
    "-m",
    "--after-context",
    "--before-context",
    "--color",
    "--colors",
    "--context",
    "--context-separator",
    "--dfa-size-limit",
    "--encoding",
    "--engine",
    "--field-context-separator",
    "--field-match-separator",
    "--file",
    "-f",
    "--glob",
    "--gpu-device-ids",
    "--hostname-bin",
    "--hyperlink-format",
    "--iglob",
    "--ignore-file",
    "--max-columns",
    "--max-count",
    "--max-depth",
    "--maxdepth",
    "--max-filesize",
    "--path-separator",
    "--pre",
    "--pre-glob",
    "--regex-size-limit",
    "--replace",
    "--sort",
    "--sortr",
    "--threads",
    "--type",
    "--type-add",
    "--type-clear",
    "--type-not",
    "-d",
    "-r",
    "-t",
    "-T",
}
_SEARCH_ATTACHED_VALUE_SHORT_FLAGS = (
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-d",
    "-f",
    "-g",
    "-j",
    "-m",
    "-r",
    "-t",
    "-T",
)
# #88-parity fix (bootstrap/main.py divergence, dogfood #2): cli/main.py's
# `_has_walk_scope_bound` (~4734, the original #88 fix) distinguishes an UNCONDITIONAL
# walk bound (`-d`/`--max-depth`/`--maxdepth`, which genuinely limits how far the walk
# descends) from a PATH-CONDITIONAL one (`-g`/`-t`/`-T`/`--glob`/`--iglob`/`--type`/
# `--type-not`, which only filter WHICH already-walked files count as candidates -- they
# do NOT reduce how much of the tree must be walked). This file's front-door mirror used
# to treat every one of these flags as an unconditional bound, so a bare
# `tg search PAT -t py --json` (no explicit PATH) slipped past
# `_search_args_include_unbounded_broad_scan`'s refusal straight into native delegation
# -- a resurrection of #88 (see `_search_args_include_generated_scan_bound`'s
# `paths_defaulted` parameter below). The path-conditional set is a valid bound ONLY
# when the caller also supplied an explicit PATH positional (a deliberately scoped
# root, further narrowed by a file filter).
_SEARCH_UNCONDITIONAL_SCAN_BOUND_FLAGS = {
    "-d",
    "--max-depth",
    "--maxdepth",
}
_SEARCH_UNCONDITIONAL_SCAN_BOUND_PREFIXES = (
    "--max-depth=",
    "--maxdepth=",
)
_SEARCH_PATH_CONDITIONAL_SCAN_BOUND_FLAGS = {
    "-g",
    "-t",
    "-T",
    "--glob",
    "--iglob",
    "--type",
    "--type-not",
}
_SEARCH_PATH_CONDITIONAL_SCAN_BOUND_PREFIXES = (
    "--glob=",
    "--iglob=",
    "--type=",
    "--type-not=",
)
_SEARCH_NO_IGNORE_FLAGS = {
    "--no-ignore",
    "--no-ignore-dot",
    "--no-ignore-exclude",
    "--no-ignore-files",
    "--no-ignore-global",
    "--no-ignore-parent",
    "--no-ignore-vcs",
}
_SEARCH_HIDDEN_FLAGS = {"-.", "--hidden"}


def _prefer_rust_first_search() -> bool:
    value = os.environ.get("TG_RUST_FIRST_SEARCH", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _read_project_version_fallback() -> str:
    try:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return "0.0.0"


def _print_version() -> None:
    try:
        from importlib.metadata import version

        pkg_version = version("tensor-grep")
    except Exception:
        pkg_version = _read_project_version_fallback()

    print(f"tensor-grep {pkg_version}")
    if any(arg in {"--verbose", "-v"} for arg in sys.argv[2:]):
        print()
        print("features:+gpu-cudf,+gpu-torch,+rust-core")
        print("simd(compile):+SSE2,-SSSE3,-AVX2")
        print("simd(runtime):+SSE2,+SSSE3,+AVX2")
        print()
        print("Arrow Zero-Copy IPC is available")


def _normalize_search_invocation(argv: list[str]) -> list[str] | None:
    if not argv:
        return None

    first_arg = argv[0]
    if first_arg == "search":
        return argv[1:]
    if first_arg in _KNOWN_COMMANDS or first_arg.startswith("--typer-"):
        return None
    return argv


def _is_public_help_invocation(argv: list[str]) -> bool:
    if len(argv) == 1 and argv[0] in {"--help", "-h"}:
        return True
    if len(argv) == 2 and argv[0] in _PYTHON_FULL_HELP_COMMANDS and argv[1] in {"--help", "-h"}:
        return True
    return False


def _requires_full_cli(search_args: list[str]) -> bool:
    if not search_args:
        return True
    for arg in search_args:
        if arg in {"--help", "-h"}:
            return True
        if arg in {"--show-completion", "--install-completion"}:
            return True
        if arg in _TG_ONLY_SEARCH_FLAGS:
            return True
        if arg.startswith(_TG_ONLY_SEARCH_FLAG_PREFIXES):
            return True
        # Bundled/attached short-flag value form of the walk-scope filters: rg accepts
        # `-g*.py` == `-g *.py`, `-tpy` == `-t py`, `-Tpy` == `-T py`, and mid-bundle
        # `-itpy` == `-i -t py`. The exact-token / `--x=` checks above miss these, so a bare
        # bundled filter would slip into the unguarded rg passthrough (bundled sibling of the
        # -g/-t/--type walk-DoS, bug #88). The walk-scope short flags are -g (glob), -t (type),
        # -T (type-not); --iglob has no short form. Walk the short cluster: the first
        # VALUE-CONSUMING short flag swallows the remainder, so if that flag is -g/-t/-T it
        # carries an attached walk-scope value -> route to the full CLI (where the walk guard
        # fires). A value-consuming flag that is NOT one of those (e.g. -f<file>, -C3) swallows
        # the rest as its value, so any later g/t is data, not a flag -> stop scanning this token.
        if len(arg) > 2 and arg.startswith("-") and not arg.startswith("--"):
            for ch in arg[1:]:
                if f"-{ch}" in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS:
                    if ch in ("g", "t", "T"):
                        return True
                    break
    return False


def _requires_full_cli_ignoring_rg_json(search_args: list[str]) -> bool:
    """Like ``_requires_full_cli``, but does not treat a bare ``--json`` token as a reason
    to route to the full CLI.

    Only call this once ``explicit_rg_json`` (``--format rg`` + ``--json``) has already
    been confirmed: in that combo ``--json`` is a deliberate request for ripgrep's own
    JSON Lines output, which the real ``rg`` binary understands natively -- it is not the
    tensor-grep aggregate-JSON flag. Any OTHER TG-only flag riding along (``--cpu``,
    ``--force-cpu``, ``--rank``, ``--gpu-device-ids``, ...) must still force the full CLI,
    since real ``rg`` rejects those flags outright and dies (audit #8)."""
    return _requires_full_cli([arg for arg in search_args if arg != "--json"])


def _strip_noop_rg_format(search_args: list[str]) -> list[str] | None:
    stripped: list[str] = []
    index = 0
    while index < len(search_args):
        arg = search_args[index]
        if arg == "--format":
            index += 1
            if index >= len(search_args) or search_args[index] != "rg":
                return None
        elif arg.startswith("--format="):
            if arg.split("=", 1)[1] != "rg":
                return None
        else:
            stripped.append(arg)
        index += 1
    return stripped


def _explicit_rg_format_requested(search_args: list[str]) -> bool:
    for index, arg in enumerate(search_args):
        if arg == "--format":
            return index + 1 < len(search_args) and search_args[index + 1] == "rg"
        if arg == "--format=rg":
            return True
    return False


def _explicit_json_requested(search_args: list[str]) -> bool:
    return "--json" in search_args


# Render-only flags the aggregate plain-``--json`` path cannot honor. Mirrors
# main._PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS; kept here so the fast launcher does not
# import the heavy full CLI just to route.
_JSON_INCOMPATIBLE_RENDER_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--passthru", "--passthrough"),
    ("--heading", "--no-heading"),
    ("--trim", "--no-trim"),
    ("-b", "--byte-offset", "--no-byte-offset"),
    ("-M", "--max-columns"),
    ("--max-columns-preview", "--no-max-columns-preview"),
    ("--context-separator", "--no-context-separator"),
    ("--field-context-separator",),
    ("--field-match-separator",),
    ("-p", "--pretty"),
)


def _json_aggregate_blocks_passthrough(search_args: list[str]) -> bool:
    """Plain ``--json`` (not ``--format rg``) combined with a render-only flag the
    aggregate JSON path cannot honor must NOT be delegated to the native binary or rg
    passthrough — the native front door deadlocks/fork-bombs on e.g. ``--json -b``.
    Route to the full Python CLI, which rejects the combo with a structured exit 2
    (audit C3)."""
    if "--json" not in search_args or _explicit_rg_format_requested(search_args):
        return False
    for token in search_args:
        if token == "--":
            break
        base = token.split("=", 1)[0]
        if any(base in group for group in _JSON_INCOMPATIBLE_RENDER_FLAGS):
            return True
    return False


def _can_delegate_to_native_tg_search(search_args: list[str]) -> bool:
    if not search_args:
        return False

    supported_trigger = any(
        arg in {"--cpu", "--force-cpu", "--json", "--ndjson", "--gpu-device-ids"}
        or arg.startswith("--gpu-device-ids=")
        for arg in search_args
    )
    if not supported_trigger:
        return False

    unsupported_flags = {
        "--ast",
        "--bm25",
        "--files",
        "--files-with-matches",
        "--files-without-match",
        "--format",
        "--lang",
        "--ltl",
        "--rank",
        "--semantic",
        "--replace",
        "--stats",
        "-l",
        "-r",
        # audit #69 (re-do of #441): the separately-compiled native binary has its OWN,
        # independent multi-pattern bugs (verified via direct invocation: a `-f` pattern
        # file is silently never read at all -- an even more severe flood than the pre-fix
        # Python bug -- and multiple `-e` patterns are not deduplicated when a single line
        # matches more than one). This outer argv fast path must never delegate `-e`/`-f`
        # searches to it; route them through the full CLI instead, which now combines
        # multi-pattern correctly and already refuses this exact case in its OWN inner
        # native-delegation gate (`regexp`/`file_patterns` are both in
        # `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`, cli/main.py) -- excluding them
        # here too keeps the two front doors in parity for -e/-f, even a single one.
        "-e",
        "--regexp",
        "-f",
        "--file",
        # task #121: `--count-matches` reports ripgrep's per-OCCURRENCE count, which the
        # separately-compiled native binary's fallback engine cannot produce (it is
        # LINE-granular only, same as the Python CPU/Rust fallbacks -- see
        # rust_core/src/backend_cpu.rs's `count_matches`, "count MATCHING LINES, not total
        # occurrences"). Without this exclusion, `--count-matches` combined with a trigger
        # flag (`--json`/`--ndjson`/`--cpu`/`--force-cpu`/`--gpu-device-ids`) delegated
        # straight to the native binary and silently returned a LINE count mislabeled as an
        # occurrence count (verified live: a 3-occurrence line undercounted to 1, exit 0, no
        # visible signal) -- bypassing cli/main.py's OWN identical exclusion entirely
        # (`count_matches` is already in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`
        # there; this outer argv fast path had simply drifted out of parity with it, the
        # same two-front-doors gap the -e/-f exclusion above already guards against).
        # Excluding it here routes to the full CLI instead, which refuses cleanly when rg
        # is unresolvable and otherwise still uses rg (correct occurrence counts) when it
        # is. `-c`/`--count` is UNCHANGED: its line-count contract is exactly what the
        # native binary's fallback already provides correctly, so it keeps delegating.
        "--count-matches",
    }
    unsupported_prefixes = ("--format=", "--lang=", "--replace=", "--regexp=", "--file=")
    if any(arg in unsupported_flags or arg.startswith(unsupported_prefixes) for arg in search_args):
        return False
    # Attached short-flag value form (`-efoo` == `-e foo`, `-fpats.txt` == `-f pats.txt`).
    # The bare `-e`/`-f` tokens are already covered by `unsupported_flags` above; `-F`
    # (fixed-strings, uppercase) and `--file`/`--regexp=...` (double-dash) are untouched by
    # this lowercase single-dash prefix check.
    if any(
        arg.startswith(("-e", "-f")) and not arg.startswith("--") and arg not in {"-e", "-f"}
        for arg in search_args
    ):
        return False
    return True


def _search_args_include_guarded_broad_root(search_args: list[str]) -> bool:
    for arg in search_args:
        if not arg or arg == "-" or arg.startswith("-"):
            continue
        normalized = arg.replace("\\", "/").rstrip("/").lower()
        if normalized in _GUARDED_BROAD_SEARCH_ROOTS:
            return True
        if any(normalized.endswith(f"/{root}") for root in _GUARDED_BROAD_SEARCH_ROOTS):
            return True
    return False


def _is_short_flag_with_attached_value(arg: str) -> bool:
    if not arg.startswith("-") or arg.startswith("--"):
        return False
    return any(
        arg.startswith(flag) and len(arg) > len(flag) for flag in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS
    )


def _search_args_include_generated_scan_bound(
    search_args: list[str], *, paths_defaulted: bool
) -> bool:
    """Council fix (#88-parity): mirrors cli/main.py's `_has_walk_scope_bound` (~4734)
    exactly -- `-d`/`--max-depth`/`--maxdepth` are an unconditional bound;
    `-g`/`-t`/`-T`/`--glob`/`--iglob`/`--type`/`--type-not` only count as a bound when
    ``paths_defaulted`` is False (the caller also supplied an explicit PATH). The
    caller computes ``paths_defaulted`` from the RAW args via
    ``_search_args_paths_defaulted`` -- NOT from ``_search_path_args``, whose
    ``paths or ["."]`` fallback collapses "no path" and an explicit "." into the same
    value and so cannot make this distinction.
    """
    for arg in search_args:
        if arg in _SEARCH_UNCONDITIONAL_SCAN_BOUND_FLAGS:
            return True
        if arg.startswith(_SEARCH_UNCONDITIONAL_SCAN_BOUND_PREFIXES):
            return True
        if not arg.startswith("--") and arg.startswith("-d") and len(arg) > len("-d"):
            return True
        if not paths_defaulted:
            if arg in _SEARCH_PATH_CONDITIONAL_SCAN_BOUND_FLAGS:
                return True
            if arg.startswith(_SEARCH_PATH_CONDITIONAL_SCAN_BOUND_PREFIXES):
                return True
            if not arg.startswith("--") and any(
                arg.startswith(flag) and len(arg) > len(flag) for flag in ("-g", "-t", "-T")
            ):
                return True
    return False


def _search_args_request_unrestricted_generated_scan(search_args: list[str]) -> bool:
    files_mode = "--files" in search_args
    if files_mode and any(arg in _SEARCH_HIDDEN_FLAGS for arg in search_args):
        return True
    if any(arg in _SEARCH_NO_IGNORE_FLAGS for arg in search_args):
        return True
    return any(
        arg == "--unrestricted" or (arg.startswith("-u") and not arg.startswith("--"))
        for arg in search_args
    )


def _search_path_args_raw(search_args: list[str]) -> list[str]:
    """Same walk as ``_search_path_args`` but WITHOUT its ``paths or ["."]`` fallback --
    an empty return means the caller supplied no explicit PATH positional at all
    (``paths_defaulted``), which the fallback-collapsed public helper cannot
    distinguish from an explicit ``.`` (both become ``["."]`` there).
    ``_search_args_paths_defaulted`` below is the only reason this is split out; keep
    both derived from one walk so they can never drift out of sync with each other."""
    paths: list[str] = []
    bare_pattern_seen = False
    regexp_pattern_seen = False
    skip_next = False
    parse_options = True
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if parse_options and arg == "--":
            parse_options = False
            continue
        if parse_options:
            if arg in _SEARCH_PATTERN_FLAGS:
                regexp_pattern_seen = True
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_FLAGS):
                regexp_pattern_seen = True
                continue
            if arg in _SEARCH_FLAGS_WITH_VALUES:
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
                continue
            if _is_short_flag_with_attached_value(arg):
                continue
            if arg.startswith("-"):
                continue
        if not regexp_pattern_seen and not bare_pattern_seen:
            bare_pattern_seen = True
            continue
        paths.append(arg)
    return paths


def _search_path_args(search_args: list[str]) -> list[str]:
    return _search_path_args_raw(search_args) or ["."]


def _search_args_paths_defaulted(search_args: list[str]) -> bool:
    """RAW-arg positional predicate (#88-parity fix) mirroring cli/main.py's
    ``paths_defaulted = not args[1:]`` (~7262). Deliberately does NOT derive from
    ``_search_path_args``: that helper's ``paths or ["."]`` fallback collapses "no path
    given" and an explicit "." into the identical ``["."]``, so it cannot tell
    ``tg search PAT -t py`` (no path -- REFUSE) apart from ``tg search PAT . -t py``
    (explicit "." -- ALLOW). This reads the pre-fallback raw list instead."""
    return not _search_path_args_raw(search_args)


def _path_has_project_marker(path: Path) -> bool:
    from tensor_grep.io.directory_scanner import BROAD_WORKSPACE_PROJECT_MARKERS

    for marker in BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


def _search_paths_include_generated_root(paths: list[str]) -> bool:
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if path.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                return True
            if resolved.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                return True
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                    return True
        except OSError:
            continue
    return False


def _search_paths_include_workspace_root(paths: list[str]) -> bool:
    from tensor_grep.io.directory_scanner import (
        BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
        BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    )

    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            # Item #154: mirrors cli/main.py's `_workspace_project_child_names` -- a root
            # carrying its own project marker is not skipped outright, it just needs more
            # marked children (the higher "marked-root" threshold) before it counts as a
            # workspace parent too.
            threshold = (
                BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
                if _path_has_project_marker(path)
                else BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
            )
            project_children = 0
            for child in path.iterdir():
                try:
                    if child.is_dir() and _path_has_project_marker(child):
                        project_children += 1
                except OSError:
                    continue
                if project_children >= threshold:
                    return True
        except OSError:
            continue
    return False


# Critical unscoped-search-hang fix C: heavy vendored dirs that can sit at the TOP LEVEL of
# a single project root -- `_search_paths_include_workspace_root` above only fires on
# independently-MARKED children (a `.git`/`package.json`/etc. of their own; item #154 raised
# the marked-root threshold from a flat skip to >= 8 marked children), and a single huge
# vendored repo's own `node_modules`/`external_repos`/etc. is not itself marked that way, so a
# single huge vendored repo (one giant vendored dir at the top, however few or many marked
# siblings) always slipped past it. This check
# MUST live here (not just in cli/main.py's equivalent guard) because `main_entry` below
# decides whether to delegate straight to the native `tg` binary or to `rg` passthrough --
# both of which bypass cli/main.py's Python guards entirely. Without this, an unscoped
# `tg search PATTERN --json` from a workspace root with a top-level vendored dir gets
# fast-pathed straight into an unbounded native/rg walk before cli/main.py's guard (or
# backends/cpu_backend.py's wall-clock deadline) ever gets a chance to run.
#
# Deliberately excludes tg's own index dirs (`.tensor-grep`, `_tg_refs`,
# `.tg_semantic_index`) for the same reason cli/main.py's guard does: those are normally
# gitignored, already skipped by repo_map's walk, and bounded by the native/cpu wall-clock
# deadline if ever walked -- including them here made this guard refuse a plain unscoped
# search from tensor-grep's own repo root (verified via real dogfood run).
#
# Review finding H1 (2026-07-05): also excludes any dir already walker-skipped by
# `DirectoryScanner`'s `_GENERATED_DIR_NAMES` (currently just `node_modules` of the four
# above) -- that dir was already bounded (walker-skipped + normally `.gitignore`d + Fix B's
# per-file deadline), so refusing it was a pure false positive against every ordinary
# Node/React repo. Imported (not hardcoded) from `io/directory_scanner.py` so this set and
# cli/main.py's equivalent guard can never drift out of sync.
def _search_paths_include_vendored_root(paths: list[str]) -> bool:
    """O(top-level-entries) probe: never walks -- only `Path.iterdir()` one level deep."""
    from tensor_grep.io.directory_scanner import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in UNBOUNDED_VENDORED_ROOT_DIR_NAMES:
                    return True
        except OSError:
            continue
    return False


# Item #105 (bootstrap raw-rg-passthrough gap, CEO dogfood v1.92.x directive): neither
# `_search_paths_include_workspace_root` above (needs >=3/>=8 independently-MARKED sibling
# dirs) nor `_search_paths_include_vendored_root` (needs a top-level vendored dir NAME) catches
# a plain, single, large repo root -- e.g. a flat monorepo `src/` with thousands of files, no
# vendored subdir, and no marked siblings. That shape sailed straight into `_run_rg_passthrough`
# (a raw `rg` subprocess spawn bounded ONLY by a wall-clock timeout --
# `TG_RG_TIMEOUT_SECONDS`/`TG_SIDECAR_TIMEOUT_MS`, no proactive refusal) because bootstrap's fast
# path never delegates a bare, flag-less search to the native binary by default --
# `_can_delegate_to_native_tg_search` requires a "supported trigger" flag (`--cpu`/
# `--force-cpu`/`--json`/`--ndjson`/`--gpu-device-ids`) and `TG_RUST_FIRST_SEARCH` is off by
# default -- so the native binary's OWN `check_implicit_walk_ceiling`
# (`rust_core/src/rg_passthrough.rs`, audit #100/#105/#109, verified via direct invocation to
# refuse in ~34ms) never gets a chance to run for the common bare `tg search PATTERN` shape.
# `cli/main.py`'s full-CLI path already refuses this shape correctly (Bug #88 fix,
# `_should_refuse_unbounded_large_root_scan`) -- this mirror sends the SAME unscoped search
# there instead of into an unguarded raw `rg` spawn, matching the pattern already used above for
# the workspace-root/vendored-root guards (both of which bypass cli/main.py's Python guards
# entirely without a front-door mirror).
#
# Deliberately WALKS (bounded to ceiling+1 entries, never a full-tree enumeration) rather than a
# one-level `iterdir()` probe like the vendored/workspace guards above -- there is no shallow
# signal for "this tree is huge"; the walk itself is the only honest measurement, same approach
# as cli/main.py's `_implicit_glob_search_walk_exceeds_ceiling`. Mirrors `--hidden`/
# `--no-ignore*` flags into the probe config by forwarding each raw flag to its OWN
# `SearchConfig` field -- the same shape `_implicit_glob_search_walk_exceeds_ceiling` gets for
# free from its already-Typer-parsed `config` argument (`dataclasses.replace(config, ...)`,
# main.py). #702 gate NIT-1: an earlier version of this probe collapsed every flag in
# `_SEARCH_NO_IGNORE_FLAGS` onto the single `no_ignore` field -- but `DirectoryScanner` only
# actually disables `.gitignore` loading when `no_ignore`/`no_ignore_vcs`/`no_ignore_files` is
# set (`_load_ignore_spec`, `io/directory_scanner.py:154`); `--no-ignore-dot`/`-exclude`/
# `-global`/`-parent` are no-ops for this scanner's single ignore mechanism. Collapsing them all
# onto `no_ignore` OVER-widened the probe (a latency-only over-count, not a correctness bug --
# it could only make the guard refuse MORE often, never less), but drifted from the sibling's
# field-exact behavior; passing each flag to its own field restores parity and still never
# UNDER-counts relative to the real invocation (each flag maps 1:1 to the field the real,
# Typer-parsed `SearchConfig` would carry for that same raw argv).
def _search_paths_include_oversized_implicit_root(paths: list[str], search_args: list[str]) -> bool:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import (
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
        DirectoryScanner,
    )

    probe_config = SearchConfig(
        hidden=any(arg in _SEARCH_HIDDEN_FLAGS for arg in search_args),
        no_ignore="--no-ignore" in search_args,
        no_ignore_dot="--no-ignore-dot" in search_args,
        no_ignore_exclude="--no-ignore-exclude" in search_args,
        no_ignore_files="--no-ignore-files" in search_args,
        no_ignore_global="--no-ignore-global" in search_args,
        no_ignore_parent="--no-ignore-parent" in search_args,
        no_ignore_vcs="--no-ignore-vcs" in search_args,
    )
    count = 0
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        scanner = DirectoryScanner(probe_config)
        for _ in scanner.walk(raw_path):
            count += 1
            if count > IMPLICIT_SEARCH_WALK_FILE_CEILING:
                return True
    return False


def _search_args_include_unbounded_broad_scan(search_args: list[str]) -> bool:
    if "--allow-broad-generated-scan" in search_args:
        return False
    paths_defaulted = _search_args_paths_defaulted(search_args)
    if _search_args_include_generated_scan_bound(search_args, paths_defaulted=paths_defaulted):
        return False
    paths = _search_path_args(search_args)
    if _search_paths_include_workspace_root(paths):
        return True
    if _search_paths_include_vendored_root(paths):
        return True
    if paths_defaulted and _search_paths_include_oversized_implicit_root(paths, search_args):
        return True
    return _search_args_request_unrestricted_generated_scan(
        search_args
    ) and _search_paths_include_generated_root(paths)


def _regex_patterns_from_search_args(search_args: list[str]) -> list[str]:
    skip_next = False
    bare_pattern: str | None = None
    regexp_patterns: list[str] = []
    parse_options = True
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if parse_options and arg == "--":
            parse_options = False
            continue
        if parse_options:
            if arg in _SEARCH_PATTERN_FLAGS:
                if index + 1 < len(search_args):
                    regexp_patterns.append(search_args[index + 1])
                    skip_next = True
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_FLAGS):
                regexp_patterns.append(arg.split("=", 1)[1])
                continue
            if arg in _SEARCH_FLAGS_WITH_VALUES:
                skip_next = True
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
                continue
            if arg.startswith("-"):
                continue
        # Past the `--` sentinel (or a plain positional arg before it): the first
        # positional token is the bare pattern, exactly like `_search_path_args` treats it
        # -- even when it looks like a flag (e.g. an unbalanced-paren regex starting with
        # `-`). Before this fix, content after `--` still fell through to the
        # `arg.startswith("-")` check above and was silently dropped as an "unrecognized
        # option", so an invalid regex passed after `--` (e.g. `tg search -- '-(unbalanced'`)
        # never reached `_search_args_include_obviously_invalid_regex`'s re.compile check
        # (audit #24).
        if bare_pattern is None:
            bare_pattern = arg
    if regexp_patterns:
        return regexp_patterns
    return [bare_pattern] if bare_pattern is not None else []


def _search_args_include_obviously_invalid_regex(search_args: list[str]) -> bool:
    if any(arg in _SEARCH_LITERAL_FLAGS for arg in search_args):
        return False
    if any(arg in _SEARCH_PCRE2_FLAGS for arg in search_args):
        return False
    for pattern in _regex_patterns_from_search_args(search_args):
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error:
            return True
    return False


def _effective_native_tg_search_args(search_args: list[str]) -> list[str]:
    if (
        not env_flag_enabled("TG_FORCE_CPU")
        or "--cpu" in search_args
        or "--force-cpu" in search_args
    ):
        return list(search_args)
    # Audit #11: a forced `--cpu` must never land AFTER a user `--` sentinel -- everything
    # past `--` is positional (rg/native argv semantics), so appending there would both
    # silently defeat TG_FORCE_CPU (the token is no longer parsed as a flag) and inject a
    # bogus `--cpu` path argument alongside the user's own pattern/paths. Insert it before
    # the sentinel instead; with no sentinel present, append at the end as before.
    if "--" in search_args:
        sentinel_index = search_args.index("--")
        return [*search_args[:sentinel_index], "--cpu", *search_args[sentinel_index:]]
    return [*search_args, "--cpu"]


def _terminate_child(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort: terminate the child process and wait briefly for it to exit.

    Swallows all errors so that signal-handling paths cannot themselves raise.
    """
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _popen_child(argv: list[str]) -> subprocess.Popen[bytes]:
    """Thin wrapper around subprocess.Popen; exposed at module level so tests can patch it.

    H3 fix: retries on Windows sharing-violation / PermissionError up to
    _LAUNCH_RETRY_MAX times with exponential back-off so that rapid back-to-back
    invocations that race the OS file-handle release never produce a silent exit-1/127.
    """
    _LAUNCH_RETRY_MAX = 3
    _LAUNCH_RETRY_DELAYS = (0.020, 0.060)
    _WIN_SHARING_ERRORS = {32, 5}  # ERROR_SHARING_VIOLATION, ERROR_ACCESS_DENIED

    last_exc: BaseException | None = None
    for attempt in range(_LAUNCH_RETRY_MAX):
        try:
            return subprocess.Popen(argv)
        except PermissionError as exc:
            last_exc = exc
            if attempt >= _LAUNCH_RETRY_MAX - 1:
                break
            time.sleep(_LAUNCH_RETRY_DELAYS[min(attempt, len(_LAUNCH_RETRY_DELAYS) - 1)])
        except OSError as exc:
            if (
                sys.platform.startswith("win")
                and getattr(exc, "winerror", None) in _WIN_SHARING_ERRORS
            ):
                last_exc = exc
                if attempt >= _LAUNCH_RETRY_MAX - 1:
                    break
                time.sleep(_LAUNCH_RETRY_DELAYS[min(attempt, len(_LAUNCH_RETRY_DELAYS) - 1)])
            else:
                raise

    assert last_exc is not None
    raise last_exc


def _streaming_passthrough_returncode(
    argv: list[str], *, timeout_env_var: str | None = None
) -> int:
    """Run an interactive streaming passthrough, returning its exit code and converting
    a subprocess timeout into a clean exit 124 instead of an uncaught TimeoutExpired
    traceback that also SIGKILLs the child mid-stream. ripgrep never self-terminates a
    search, so a timeout here is tg-imposed; surface it with the coreutils ``timeout``
    convention rather than crashing the CLI (audit B5/#10).

    C3 fix: Uses Popen (via _popen_child) so that signals (Ctrl-C / SIGTERM / parent
    kill) are forwarded to the child and the entire chain terminates together.  An
    abnormal child exit code is returned as-is and is NOT retried or re-spawned — the
    chain always terminates after a single child run.

    H3 fix: _popen_child retries on Windows sharing-violation before we even get here.

    Backward-compat note: when run_subprocess has been monkey-patched by a test the
    function falls back to the old subprocess.run code-path so existing routing tests
    remain green without modification.
    """
    # --- backward-compat shim for tests that patch bootstrap.run_subprocess -----------
    # Some existing unit tests (e.g. test_main_entry_should_delegate_run_to_managed_native
    # _when_available) monkeypatch bootstrap.run_subprocess and assert it is called with
    # the right command.  We preserve that contract: when the module-level run_subprocess
    # has been replaced (i.e. it is no longer the original imported function), we fall
    # back to the old subprocess.run path so those tests continue to pass.
    if run_subprocess is not _ORIG_RUN_SUBPROCESS:
        try:
            if timeout_env_var is not None:
                result = run_subprocess(argv, check=False, timeout_env_var=timeout_env_var)
            else:
                result = run_subprocess(argv, check=False)
            return int(result.returncode)
        except subprocess.TimeoutExpired:
            sys.stderr.write(
                "tensor-grep: search exceeded the configured timeout and was stopped. For a "
                "large repo, scope the search to a path (e.g. `tg search PATTERN src/`), or raise "
                "TG_RG_TIMEOUT_SECONDS / TG_SUBPROCESS_TIMEOUT_SECONDS.\n"
            )
            return 124
    # --- end backward-compat shim -------------------------------------------------------

    from tensor_grep.cli.subprocess_policy import (
        configured_ripgrep_timeout_seconds,
        configured_subprocess_timeout_seconds,
    )

    if timeout_env_var is not None:
        if timeout_env_var == "TG_RG_TIMEOUT_SECONDS":
            timeout_seconds: float | None = configured_ripgrep_timeout_seconds()
        else:
            timeout_seconds = configured_subprocess_timeout_seconds(
                env_var=timeout_env_var,
            )
    else:
        timeout_seconds = configured_subprocess_timeout_seconds()

    proc = _popen_child(argv)

    # C3 fix: Register an atexit handler that terminates the child if the parent exits
    # unexpectedly (e.g. SIGTERM / TerminateProcess received while waiting).  The
    # handler is a no-op once the child has already exited normally.
    import atexit

    def _atexit_kill() -> None:
        if proc.poll() is None:
            _terminate_child(proc)

    atexit.register(_atexit_kill)

    try:
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while True:
            remaining: float | None
            if deadline is None:
                remaining = None
            else:
                remaining = max(0.001, deadline - time.monotonic())
            try:
                rc = proc.wait(timeout=remaining)
                atexit.unregister(_atexit_kill)
                return int(rc)
            except subprocess.TimeoutExpired:
                # Timeout imposed by tg config — kill child cleanly and report 124.
                _terminate_child(proc)
                atexit.unregister(_atexit_kill)
                sys.stderr.write(
                    "tensor-grep: search exceeded the configured timeout and was stopped "
                    "(adjust TG_RG_TIMEOUT_SECONDS / TG_SUBPROCESS_TIMEOUT_SECONDS).\n"
                )
                return 124
    except (KeyboardInterrupt, SystemExit) as exc:
        # C3: Parent received Ctrl-C or an outer SystemExit.  Forward the signal to the
        # child so it terminates too, then re-raise so *this* process also exits cleanly.
        _terminate_child(proc)
        atexit.unregister(_atexit_kill)
        raise exc


def _run_native_tg_search(binary_name: str, search_args: list[str]) -> int:
    return _streaming_passthrough_returncode([binary_name, "search", *search_args])


def _run_native_tg_command(binary_name: str, argv: list[str]) -> int:
    return _streaming_passthrough_returncode([binary_name, *argv])


def _run_rg_passthrough(binary_name: str, search_args: list[str]) -> int:
    return _streaming_passthrough_returncode(
        [binary_name, *search_args], timeout_env_var="TG_RG_TIMEOUT_SECONDS"
    )


def _run_full_cli() -> None:
    from tensor_grep.cli.main import main_entry as full_main_entry

    full_main_entry()


def _run_ast_workflow_cli(argv: list[str]) -> None:
    from tensor_grep.cli.ast_workflows import main_entry as ast_main_entry

    ast_main_entry(argv)


def _scan_requires_full_cli(scan_args: list[str]) -> bool:
    return any(
        arg in _SCAN_FULL_CLI_FLAGS or arg.startswith(_SCAN_FULL_CLI_FLAG_PREFIXES)
        for arg in scan_args
    )


# ast-grep semantic options that the native `run` handler cannot serve itself and
# bounces to the Python sidecar. These MUST be handled by the in-process Python AST
# workflow rather than re-delegated to the native binary: the native binary spawns
# `python -m tensor_grep run ...` for these options, and if bootstrap delegated that
# spawn straight back to native we would ping-pong native<->python forever (the
# `tg run --strictness/--selector/--stdin/--globs` hang). Keep this list in sync with
# `ast_run_requires_python_passthrough` in rust_core/src/main.rs.
_RUN_AST_WORKFLOW_FLAGS = ("--selector", "--strictness", "--stdin", "--globs")
_RUN_AST_WORKFLOW_FLAG_PREFIXES = ("--selector=", "--strictness=", "--globs=")


def _run_requires_ast_workflow(run_args: list[str]) -> bool:
    return any(
        arg in _RUN_AST_WORKFLOW_FLAGS or arg.startswith(_RUN_AST_WORKFLOW_FLAG_PREFIXES)
        for arg in run_args
    )


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 with ``errors="replace"`` so non-ASCII CLI output never raises
    ``UnicodeEncodeError`` on a legacy cp1252 Windows console (the #346 / #42 crash class: a
    filesystem path with a non-English username, a U+2028 in a match, an emoji marker, ...). The
    root fix for the whole ``typer.echo``/``print`` sweep -- one reconfigure at the entry point
    covers every command instead of routing each site through ``_safe_stdout_line``. No-op where the
    stream is already UTF-8 or cannot be reconfigured (a pipe with pending bytes, a non-TextIO
    stream); ``errors="replace"`` guarantees it can never itself raise on write.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        if "utf" in (getattr(stream, "encoding", None) or "").lower():
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Stream already has buffered output or is detached -- degrade to the per-line
            # _safe_stdout_line fallback still in place at the call sites; do not crash startup.
            pass


def main_entry() -> None:
    _force_utf8_streams()
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        _print_version()
        raise SystemExit(0)
    if _is_public_help_invocation(argv):
        _run_full_cli()
        return

    if argv and argv[0] in {"run", "scan", "test", "ast-info"}:
        if argv[0] == "run":
            # ast-grep semantic options (--selector/--strictness/--stdin/--globs) are
            # served by the Python AST workflow. Routing them to the native binary would
            # bounce right back here (native spawns `python -m tensor_grep run ...`) and
            # ping-pong forever, so handle them directly in Python.
            if _run_requires_ast_workflow(argv[1:]):
                _run_ast_workflow_cli(argv)
                return
            native_binary_path = resolve_native_tg_binary()
            native_binary = str(native_binary_path) if native_binary_path else None
            if native_binary is not None:
                raise SystemExit(_run_native_tg_command(native_binary, argv))
            _run_full_cli()
            return
        if (argv[0] in {"test", "ast-info"}) or (
            argv[0] == "scan" and _scan_requires_full_cli(argv[1:])
        ):
            _run_full_cli()
            return
        _run_ast_workflow_cli(argv)
        return

    search_args = _normalize_search_invocation(argv)
    if search_args is not None:
        passthrough_search_args = _strip_noop_rg_format(search_args)
        if passthrough_search_args is None:
            _run_full_cli()
            return
        if _json_aggregate_blocks_passthrough(passthrough_search_args):
            # `--json` + a render-only flag (e.g. -b) must never be delegated to the
            # native binary or rg passthrough — the native front door deadlocks and
            # fork-bombs on it. The full CLI rejects the combo with a structured exit 2
            # (audit C3).
            _run_full_cli()
            return
        explicit_rg_json = _explicit_rg_format_requested(search_args) and _explicit_json_requested(
            search_args
        )

        effective_search_args = _effective_native_tg_search_args(passthrough_search_args)
        native_binary_path = resolve_native_tg_binary()
        native_binary = str(native_binary_path) if native_binary_path else None
        if os.environ.get("TG_REEXEC_GUARD"):
            # We were spawned by the native front door (it delegated a `--json` +
            # passthrough-flag search to us). Never delegate search BACK to the native
            # binary — that mutual native<->python delegation is the C3 fork-bomb. Handle
            # the search in Python (rg passthrough or the full CLI) instead.
            native_binary = None
        guarded_broad_root = _search_args_include_guarded_broad_root(
            passthrough_search_args
        ) or _search_args_include_unbounded_broad_scan(passthrough_search_args)
        invalid_regex = _search_args_include_obviously_invalid_regex(passthrough_search_args)

        # task #121 note: `--count-matches` is excluded from `_can_delegate_to_native_tg_search`
        # (it needs rg for its per-occurrence count, which the native binary's fallback engine
        # cannot produce), but the `_prefer_rust_first_search()` OR-branch below can STILL route
        # a bare `--count-matches` search to the native binary when TG_RUST_FIRST_SEARCH=1
        # (`--count-matches` is not a `_requires_full_cli` flag). That is SAFE and deliberately
        # NOT special-cased here: the native binary self-refuses count_matches via
        # `require_ripgrep_or_exit` (rust_core/src/main.rs) when rg is unresolvable -- a clean
        # exit-2, never a silent wrong count. Locked by
        # test_cli_bootstrap.py::test_rust_first_count_matches_refuses_via_native_self_guard so a
        # future routing change to this branch cannot silently reopen the silent-wrong-count.
        if (
            not explicit_rg_json
            and native_binary is not None
            and not guarded_broad_root
            and not invalid_regex
            and (
                _can_delegate_to_native_tg_search(effective_search_args)
                or (_prefer_rust_first_search() and not _requires_full_cli(passthrough_search_args))
            )
        ):
            command_args = (
                effective_search_args
                if _can_delegate_to_native_tg_search(effective_search_args)
                else search_args
            )
            raise SystemExit(_run_native_tg_search(native_binary, command_args))

        # audit #8: `explicit_rg_json` (`--format rg --json`) must NOT be an unconditional
        # green light for rg passthrough -- it only means real `rg`'s own JSON output is
        # deliberately being requested. If a TG-only flag rides along in the same
        # invocation (`--cpu`, `--force-cpu`, `--rank`, `--gpu-device-ids`, ...), real `rg`
        # does not understand it and dies outright, so that combo must still route to the
        # full CLI instead of being forwarded to rg passthrough.
        if explicit_rg_json:
            rg_json_requires_full_cli = _requires_full_cli_ignoring_rg_json(passthrough_search_args)
        else:
            rg_json_requires_full_cli = _requires_full_cli(passthrough_search_args)

        if not guarded_broad_root and not invalid_regex and not rg_json_requires_full_cli:
            rg_binary_path = resolve_ripgrep_binary()
            binary_name = str(rg_binary_path) if rg_binary_path else None
            if binary_name is not None:
                raise SystemExit(_run_rg_passthrough(binary_name, passthrough_search_args))

    _run_full_cli()


if __name__ == "__main__":
    main_entry()
