import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from tensor_grep.core.config import SearchConfig

if TYPE_CHECKING:
    from pathspec.gitignore import GitIgnoreSpec

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
_GENERATED_RELATIVE_DIRS = {
    ".claude/context",
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

# The Rust PyO3 directory scanner (`RustDirectoryScanner`) is NOT exported by the `rust_core`
# extension (only `RustBackend` + functions are), so the old `from rust_core import
# RustDirectoryScanner` always raised and this flag was permanently False — the Python walk below was
# always the only scan path. Kept as a documented flag so a future native scanner can re-enable a
# fast path (which must also export the class) without re-plumbing the call site.
HAS_RUST_SCANNER = False

# Defensive traversal budget (round-5 Q14 hardening): a pathological tree (very deep/wide fanout,
# or a filesystem fan-bomb) must not make a single `walk()` call consume unbounded time/memory.
# Counts total dir+file entries the walk touches; once the cap is exceeded the walk STOPS and
# flags the truncation (never a silent drop) -- mirroring the `possibly_truncated` /
# `truncation_cause` DoS-guard style used by `cli/inventory.py` and `cli/repo_map.py`.
# Env-overridable for the rare legitimately-huge monorepo.
_MAX_SCAN_ENTRIES_ENV = "TG_DIR_SCAN_MAX_ENTRIES"
DEFAULT_MAX_SCAN_ENTRIES = 200_000

# Defensive byte cap on `.gitignore` (round-5 Q15 hardening): a giant (crafted or accidental)
# .gitignore must not be slurped into memory whole. Read at most this many bytes; the rest is
# ignored and flagged via `gitignore_truncated` rather than silently swallowed.
_GITIGNORE_MAX_BYTES_ENV = "TG_GITIGNORE_MAX_BYTES"
DEFAULT_GITIGNORE_MAX_BYTES = 1024 * 1024  # 1 MiB is generous for a real .gitignore


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class DirectoryScanner:
    def __init__(
        self,
        config: SearchConfig | None = None,
        *,
        max_scan_entries: int | None = None,
        gitignore_max_bytes: int | None = None,
    ):
        self.config = config or SearchConfig()
        self.max_scan_entries = (
            max_scan_entries
            if max_scan_entries is not None
            else _configured_positive_int(_MAX_SCAN_ENTRIES_ENV, DEFAULT_MAX_SCAN_ENTRIES)
        )
        self.gitignore_max_bytes = (
            gitignore_max_bytes
            if gitignore_max_bytes is not None
            else _configured_positive_int(_GITIGNORE_MAX_BYTES_ENV, DEFAULT_GITIGNORE_MAX_BYTES)
        )
        # Truncation signals -- sticky (never reset to False) so a scanner instance reused across
        # multiple `walk()` calls (see `ast_workflows.py`) doesn't lose an earlier truncation.
        self.scan_truncated = False
        self.scan_truncation_cause: str | None = None
        self.gitignore_truncated = False

    def _load_ignore_spec(self, base_path: Path) -> "GitIgnoreSpec | None":
        if self.config.no_ignore or self.config.no_ignore_vcs or self.config.no_ignore_files:
            return None

        gitignore = base_path / ".gitignore"
        if not gitignore.exists():
            return None

        import pathspec

        # Byte-cap the read (Q15): request one extra byte so we can tell whether the file
        # actually exceeded the cap without loading it whole.
        with gitignore.open("rb") as handle:
            raw = handle.read(self.gitignore_max_bytes + 1)
        if len(raw) > self.gitignore_max_bytes:
            raw = raw[: self.gitignore_max_bytes]
            self.gitignore_truncated = True
            # Drop a dangling partial final line so a byte-boundary cut mid-pattern (e.g.
            # "*.b") isn't fed to pathspec as a misleading glob.
            last_newline = raw.rfind(b"\n")
            if last_newline != -1:
                raw = raw[:last_newline]

        patterns = raw.decode("utf-8", errors="replace").splitlines()
        return pathspec.GitIgnoreSpec.from_lines(patterns)

    @staticmethod
    def _ancestor_spec_stack(
        base_path: Path,
        root_path: Path,
        dir_specs: "dict[Path, GitIgnoreSpec | None]",
    ) -> "list[tuple[Path, GitIgnoreSpec]]":
        # Collect the loaded .gitignore specs from base_path down to root_path (shallow-first).
        # os.walk is top-down, so every ancestor of root_path was visited (and its spec loaded)
        # before root_path is processed.
        chain: list[Path] = []
        directory = root_path
        while True:
            chain.append(directory)
            if directory == base_path or directory.parent == directory:
                break
            directory = directory.parent
        chain.reverse()
        return [(d, spec) for d in chain if (spec := dir_specs.get(d)) is not None]

    def _path_ignored_by_stack(
        self,
        path: Path,
        is_dir: bool,
        spec_stack: "list[tuple[Path, GitIgnoreSpec]]",
    ) -> bool:
        # Apply the ancestor .gitignore specs shallowest-first; the DEEPEST spec with an opinion
        # wins (git precedence: a nested .gitignore overrides a parent's). pathspec's tri-state
        # check_file returns include=True (matched an ignore) / False (negated re-include) / None
        # (no match), so a deeper `!keep.log` correctly overrides a parent's `*.log` ignore.
        decision: bool | None = None
        for spec_dir, spec in spec_stack:
            try:
                rel = path.relative_to(spec_dir).as_posix()
            except ValueError:
                continue
            if not rel or rel == ".":
                continue
            if is_dir:
                rel = f"{rel}/"
            result = spec.check_file(rel)
            if result.include is not None:
                decision = result.include
        return bool(decision)

    def _should_descend_dir(self, base_path: Path, root: Path, directory: str) -> bool:
        if self.config.no_ignore or self.config.no_ignore_files:
            return True
        normalized = directory.lower()
        candidate = root / directory
        try:
            relative = candidate.relative_to(base_path).as_posix().lower()
        except ValueError:
            relative = directory.lower()
        base_prefixed = f"{base_path.name.lower()}/{relative}"
        if normalized in _GENERATED_DIR_NAMES:
            return False
        return (
            relative not in _GENERATED_RELATIVE_DIRS
            and base_prefixed not in _GENERATED_RELATIVE_DIRS
        )

    def walk(self, path_str: str) -> Iterator[str]:
        base_path = Path(path_str)

        if not base_path.exists():
            return

        if base_path.is_file():
            if self._should_include_file(base_path, Path(base_path.name)):
                yield str(base_path)
            return

        # Python scan path (the only path — the native RustDirectoryScanner fast path was never
        # exported by rust_core; see HAS_RUST_SCANNER above).
        max_depth = self.config.max_depth
        base_depth = len(base_path.parts)
        # Nested-.gitignore support: cache each directory's own spec as os.walk descends, then test
        # paths against the full ancestor chain so a nested subdir/.gitignore is honored, not just
        # the root one. base_path's spec is loaded up front; each deeper dir loads lazily on entry.
        dir_specs: dict[Path, GitIgnoreSpec | None] = {base_path: self._load_ignore_spec(base_path)}

        def _relative_posix(path: Path) -> str:
            return path.relative_to(base_path).as_posix()

        entries_visited = 0

        for root, dirs, files in os.walk(base_path):
            # Traversal budget (Q14): count this root plus every dir/file entry it exposes
            # BEFORE any filtering, since those are the entries the walk had to stat via
            # readdir. Once the budget is exceeded, stop descending and surface truncation
            # rather than silently dropping the rest of the tree.
            entries_visited += 1 + len(dirs) + len(files)
            if entries_visited > self.max_scan_entries:
                self.scan_truncated = True
                self.scan_truncation_cause = "max-scan-entries"
                dirs.clear()
                break

            current_depth = len(Path(root).parts) - base_depth

            if max_depth is not None and current_depth >= max_depth:
                dirs.clear()  # Stop walking deeper
                continue

            # Filter directories (hidden, etc)
            if not self.config.hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            root_path = Path(root)
            if root_path not in dir_specs:
                dir_specs[root_path] = self._load_ignore_spec(root_path)
            spec_stack = self._ancestor_spec_stack(base_path, root_path, dir_specs)

            dirs[:] = [
                directory
                for directory in dirs
                if self._should_descend_dir(base_path, root_path, directory)
            ]

            if spec_stack:
                dirs[:] = [
                    directory
                    for directory in dirs
                    if not self._path_ignored_by_stack(root_path / directory, True, spec_stack)
                ]

            for file_name in files:
                # Filter hidden files
                if not self.config.hidden and file_name.startswith("."):
                    continue

                file_path = Path(root) / file_name
                relative_path = Path(_relative_posix(file_path))
                if spec_stack and self._path_ignored_by_stack(file_path, False, spec_stack):
                    continue
                if self._should_include_file(file_path, relative_path):
                    yield str(file_path)

    def _should_include_file(self, file_path: Path, relative_path: Path | None = None) -> bool:
        # Check explicit globs
        if self.config.glob:
            matched_glob = False
            candidates = [file_path.name, str(file_path), file_path.as_posix()]
            if relative_path is not None:
                candidates.append(relative_path.as_posix())
            if self.config.glob_case_insensitive:
                candidates = [candidate.lower() for candidate in candidates]
            for g in self.config.glob:
                # Simplistic handling: if it starts with !, it's an exclusion
                is_exclude = g.startswith("!")
                pattern = g[1:] if is_exclude else g
                if self.config.glob_case_insensitive:
                    pattern = pattern.lower()

                if any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates):
                    if is_exclude:
                        return False
                    matched_glob = True

            # If globs were provided and none matched, exclude
            if not matched_glob:
                return False

        # Check explicit types (extensions)
        if self.config.file_type:
            matched_type = False
            ext = file_path.suffix.lstrip(".")
            for t in self.config.file_type:
                if ext == t:
                    matched_type = True
                    break
            if not matched_type:
                return False

        if self.config.type_not:
            ext = file_path.suffix.lstrip(".")
            for t in self.config.type_not:
                if ext == t:
                    return False

        return True
