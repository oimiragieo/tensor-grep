import fnmatch
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from tensor_grep.core.config import SearchConfig

if TYPE_CHECKING:
    from pathspec.gitignore import GitIgnoreSpec

# Attempt to load the blazing fast Rust PyO3 gitignore scanner
try:
    if "pytest" not in sys.modules:
        from tensor_grep.rust_core import RustDirectoryScanner

        HAS_RUST_SCANNER = True
    else:
        # Avoid linking errors in mocked test environments
        HAS_RUST_SCANNER = False
except (ImportError, ModuleNotFoundError):
    HAS_RUST_SCANNER = False


class DirectoryScanner:
    def __init__(self, config: SearchConfig | None = None):
        self.config = config or SearchConfig()

    def _load_ignore_spec(self, base_path: Path) -> "GitIgnoreSpec | None":
        if self.config.no_ignore or self.config.no_ignore_vcs or self.config.no_ignore_files:
            return None

        gitignore = base_path / ".gitignore"
        if not gitignore.exists():
            return None

        import pathspec

        patterns = gitignore.read_text(encoding="utf-8").splitlines()
        return pathspec.GitIgnoreSpec.from_lines(patterns)

    def walk(self, path_str: str) -> Iterator[str]:
        base_path = Path(path_str)

        if not base_path.exists():
            return

        if base_path.is_file():
            if self._should_include_file(base_path):
                yield str(base_path)
            return

        # Use the highly-optimized Rust PyO3 `ignore` crate if available
        if (
            HAS_RUST_SCANNER
            and not self.config.glob
            and not self.config.file_type
            and not self.config.type_not
        ):
            # Keep Python-side walking only for direct files or Python-only filters.
            scanner = RustDirectoryScanner(
                hidden=self.config.hidden, max_depth=self.config.max_depth
            )
            for file_path in scanner.walk(path_str):
                yield file_path
            return

        # Python Fallback Path
        max_depth = self.config.max_depth
        base_depth = len(base_path.parts)
        ignore_spec = self._load_ignore_spec(base_path)

        def _relative_posix(path: Path) -> str:
            return path.relative_to(base_path).as_posix()

        for root, dirs, files in os.walk(base_path):
            current_depth = len(Path(root).parts) - base_depth

            if max_depth is not None and current_depth >= max_depth:
                dirs.clear()  # Stop walking deeper
                continue

            # Filter directories (hidden, etc)
            if not self.config.hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            if ignore_spec is not None:
                dirs[:] = [
                    directory
                    for directory in dirs
                    if not ignore_spec.match_file(f"{_relative_posix(Path(root) / directory)}/")
                ]

            for file_name in files:
                # Filter hidden files
                if not self.config.hidden and file_name.startswith("."):
                    continue

                file_path = Path(root) / file_name
                if ignore_spec is not None and ignore_spec.match_file(_relative_posix(file_path)):
                    continue
                if self._should_include_file(file_path):
                    yield str(file_path)

    def _should_include_file(self, file_path: Path) -> bool:
        # Check explicit globs
        if self.config.glob:
            matched_glob = False
            file_name = file_path.name
            file_path_str = str(file_path)
            if self.config.glob_case_insensitive:
                file_name = file_name.lower()
                file_path_str = file_path_str.lower()
            for g in self.config.glob:
                # Simplistic handling: if it starts with !, it's an exclusion
                is_exclude = g.startswith("!")
                pattern = g[1:] if is_exclude else g
                if self.config.glob_case_insensitive:
                    pattern = pattern.lower()

                if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(file_path_str, pattern):
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
