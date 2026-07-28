import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from tensor_grep.core.config import SearchConfig

# perf (+10% campaign #6 / F2.4): the 5 broad-scan-guard constants below (plus the private
# `_GENERATED_DIR_NAMES` skip-list they're partly derived from) are now DEFINED in
# `tensor_grep.io.scan_limits` -- a zero-dependency sibling module (no `SearchConfig` import) --
# and re-exported here via the `import ... as ...` idiom (same redundant-alias convention
# `cli/bootstrap.py` already uses for `run_subprocess`) so this module's public surface is
# unchanged for any existing `from tensor_grep.io.directory_scanner import <NAME>` caller. A
# caller that wants ONLY the constants (`cli/bootstrap.py`'s 3 guard helpers, `cli/main.py`'s
# module-level broad-scan literals, `cli/scan_guardrails.py`) should import directly from
# `tensor_grep.io.scan_limits` instead -- that path never executes THIS module's own
# `SearchConfig` import (see `tensor_grep.io.scan_limits`'s module docstring for the full
# rationale and `tests/unit/test_bootstrap_fast_path_imports.py` for the regression tests that
# pin it).
from tensor_grep.io.scan_limits import (
    _GENERATED_DIR_NAMES,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD as BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD as BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_PROJECT_MARKERS as BROAD_WORKSPACE_PROJECT_MARKERS,
)
from tensor_grep.io.scan_limits import (
    IMPLICIT_SEARCH_WALK_FILE_CEILING as IMPLICIT_SEARCH_WALK_FILE_CEILING,
)
from tensor_grep.io.scan_limits import (
    UNBOUNDED_VENDORED_ROOT_DIR_NAMES as UNBOUNDED_VENDORED_ROOT_DIR_NAMES,
)

if TYPE_CHECKING:
    from pathspec.gitignore import GitIgnoreSpec

_GENERATED_RELATIVE_DIRS = {
    ".claude/context",
}

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

# Task #276 slice 1: `os.walk()`'s DEFAULT `onerror=None` silently swallows an `os.scandir()`
# failure (e.g. a permission-denied or since-deleted subdirectory) -- the offending subtree is
# simply omitted from the walk with no exception, no truncation flag, nothing. A caller reading
# `scan_truncated` had no way to learn results are missing; `tg search --cpu`/`--force-cpu`
# exited 0 with a silently partial result. Cap the sample of offending paths recorded (a tree
# with thousands of denied dirs must not accumulate unboundedly) while still counting every hit.
_MAX_UNREADABLE_PATH_SAMPLE = 5


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
        # Unreadable-path diagnostics (task #276 slice 1) -- also sticky, same rationale as
        # above. `unreadable_path_count` is the TRUE total (never capped); `unreadable_path_sample`
        # is a bounded preview for a human-readable message.
        self.unreadable_path_count = 0
        self.unreadable_path_sample: list[str] = []

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

    def _on_walk_error(self, exc: OSError) -> None:
        """``os.walk()`` ``onerror`` callback (task #276 slice 1).

        The default (``onerror=None``) makes `os.walk` silently skip a directory it cannot
        `os.scandir()` (permission denied, or removed mid-walk) -- no exception, no signal,
        the subtree just never appears. That is indistinguishable from "this subtree has no
        files", so a caller (e.g. `tg search --cpu`) reported a confidently-empty-looking but
        actually-partial result at exit 0. Recording the failure here and flagging
        `scan_truncated` turns that into an explicit, machine-visible partial-coverage signal.

        Never raises: matches `os.walk`'s "keep walking the rest of the tree" contract -- an
        unreadable directory is a partial-coverage fact, not a fatal error for the whole scan.
        """
        self.scan_truncated = True
        if self.scan_truncation_cause is None:
            # First-truncation-cause-wins (mirrors `SearchResult.incomplete_reason`'s
            # first-wins merge convention): if the entry-count cap (below) is hit later in the
            # same walk, its unconditional assignment takes over as the more severe stop
            # reason, but this one is never silently lost either -- `unreadable_path_count`/
            # `unreadable_path_sample` stay populated regardless of which cause "wins" here.
            self.scan_truncation_cause = "unreadable_path"
        self.unreadable_path_count += 1
        if len(self.unreadable_path_sample) < _MAX_UNREADABLE_PATH_SAMPLE:
            offending_path = getattr(exc, "filename", None) or str(exc)
            self.unreadable_path_sample.append(str(offending_path))

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

        for root, dirs, files in os.walk(base_path, onerror=self._on_walk_error):
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
                # SYMLINK-FOLLOW DISCLOSURE. `os.walk(followlinks=False)` (the default above) stops
                # us DESCENDING into a symlinked directory -- it does NOT stop a symlink-to-a-file
                # appearing in `files`. Every consumer of this walk then does a plain
                # `open(path, "rb")`, which follows the link to its real target regardless of the
                # walk's setting. So a git-tracked symlink (an ordinary mode-120000 blob) pointing
                # at `~/.ssh/id_rsa`, a sibling project's `.env`, or /etc/passwd disclosed that
                # file's contents into search output, the on-disk symbol cache, and -- because this
                # is the walker behind `tg agent`/`orient`/the MCP tools -- an LLM agent's context.
                #
                # Proven on a temp fixture before the fix: a link at `repo/leak.txt` -> an
                # out-of-root `secret.txt` was YIELDED by this loop and `open()` returned its
                # contents.
                #
                # `checkpoint_store.py` already carries this guard (`is_dir() and not
                # is_symlink()`), and AGENTS.md names symlink-follow as a repo-wide SWEEP target
                # rather than a one-file patch -- this is the core walker the sweep missed, which
                # matters more than the site it landed on: checkpoint create/restore is occasional,
                # this is the default read path for ordinary search.
                #
                # Skipped, not resolved-and-bounded: a link whose target sits INSIDE the root is
                # still reachable by its real path, so skipping loses no legitimate content while
                # a resolve-then-compare would have to get root-containment exactly right on every
                # platform to avoid reintroducing the escape.
                if file_path.is_symlink():
                    continue
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
