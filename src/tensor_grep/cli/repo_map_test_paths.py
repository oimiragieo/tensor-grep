"""Test-file classification for the repo map (split out of repo_map.py: size ratchet)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from tensor_grep.cli.lang_suffixes import JS_TS_SUFFIXES

_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})
# The shared JS/TS suffix set (lang_suffixes imports nothing from tensor_grep, so this module
# stays import-pure: it must not import repo_map).
_JS_LIKE_SUFFIXES = JS_TS_SUFFIXES
_CLASS_TEST_SUFFIXES = frozenset({".java", ".kt", ".scala", ".cs", ".php"})

# Main's legacy rule: only `tests` / `__tests__` path parts. The singular `test` is honoured ONLY
# for parts relative to a resolved checkout root (council wave-2a r3): in a .git-less tree an
# absolute path's ancestors may include a `test` directory the user never meant.
_LEGACY_TEST_DIR_NAMES = frozenset({"tests", "__tests__"})


@lru_cache(maxsize=8192)
def _checkout_root(directory: str) -> str | None:
    current = Path(directory)
    try:
        if (current / ".git").exists():
            return directory
    except OSError:
        return None
    parent = current.parent
    return None if parent == current else _checkout_root(str(parent))


def _classification_parts(path: Path) -> tuple[tuple[str, ...], frozenset[str]]:
    if not path.is_absolute():
        return path.parts, _TEST_DIR_NAMES
    root = _checkout_root(str(path.parent))
    if root is None:
        return path.parts, _LEGACY_TEST_DIR_NAMES
    try:
        return path.relative_to(root).parts, _TEST_DIR_NAMES
    except ValueError:
        return path.parts, _LEGACY_TEST_DIR_NAMES


def _is_test_file(path: Path) -> bool:
    stem, suffix = path.stem, path.suffix.lower()
    if path.name.startswith("test_") or stem.endswith("_test"):
        return True
    if suffix in _JS_LIKE_SUFFIXES and stem.endswith((".test", ".spec")):
        return True
    if suffix in _CLASS_TEST_SUFFIXES and stem.endswith(("Test", "Tests")):
        return True
    parts, dir_names = _classification_parts(path)
    return any(part in dir_names for part in parts)
