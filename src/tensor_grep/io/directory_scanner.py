import fnmatch
import os
import sys
from collections.abc import Iterator
from pathlib import Path

from tensor_grep.core.config import SearchConfig

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
            and not base_path.is_file()  # fallback to python for direct files until rust walker fully handles them safely
        ):
            # Note: We fallback to python if specific python-side flag filters like glob/type are set
            # to preserve compatibility until those are fully mapped to Rust builder.
            scanner = RustDirectoryScanner(
                hidden=self.config.hidden, max_depth=self.config.max_depth
            )
            for file_path in scanner.walk(path_str):
                yield file_path
            return

        # Python Fallback Path
        max_depth = self.config.max_depth
        base_depth = len(base_path.parts)

        for root, dirs, files in os.walk(base_path):
            current_depth = len(Path(root).parts) - base_depth

            if max_depth is not None and current_depth >= max_depth:
                dirs.clear()  # Stop walking deeper
                continue

            # Filter directories (hidden, etc)
            if not self.config.hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file_name in files:
                # Filter hidden files
                if not self.config.hidden and file_name.startswith("."):
                    continue

                file_path = Path(root) / file_name
                if self._should_include_file(file_path):
                    yield str(file_path)

    def _should_include_file(self, file_path: Path) -> bool:
        # Check explicit globs
        if self.config.glob:
            matched_glob = False
            for g in self.config.glob:
                # Simplistic handling: if it starts with !, it's an exclusion
                is_exclude = g.startswith("!")
                pattern = g[1:] if is_exclude else g

                if fnmatch.fnmatch(file_path.name, pattern) or fnmatch.fnmatch(
                    str(file_path), pattern
                ):
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
