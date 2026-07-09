"""``tg inventory`` — a single-pass, walk-only repository manifest.

Emits files / bytes / language / category (code·doc·config·test·other) counts, a
top-level-directory breakdown, and the largest files — machine-readable via ``--json``.

Design (round-4 [e], 3-lens design council 2026-07-03):

* **Walk-only, no AST parse** — reuses the same gitignore-aware walker
  (``repo_map._iter_repo_files``) that ``orient`` / ``callers`` / ``blast-radius``
  trust, so counts stay truth-consistent with every other ``tg`` command and inherit
  its ``.tensor-grep`` / ``.git`` / vendor exclusions and ``follow_symlinks=False`` for
  free. Counting is cheap, so this is distinct from (and much faster than) the AST
  ``tg map`` path.
* **Language labels are an extension heuristic** — surfaced honestly via
  ``coverage.language_scope`` rather than pretending to be a linguist-grade classifier.
* **Binary files are detected and tracked separately** (``_looks_like_binary_file``) so a
  committed blob never inflates a language/category count — the numbers agents diff.
* **Truncation is never silent** — a repo larger than ``max_files`` is surfaced via
  ``scan_limit.possibly_truncated`` + ``truncation_cause`` (suppression != absence).
* **Fail closed** — a nonexistent path raises rather than reporting an empty inventory.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tensor_grep.cli.repo_map import (
    _DeadlineBreakFlag,
    _iter_repo_files,
    _looks_like_binary_file,
)

INVENTORY_SCHEMA_VERSION = 1
# Walk-only inventory is O(files) with only a stat()+8KB-read per file, orders of
# magnitude cheaper than the AST DEFAULT_AGENT_REPO_MAP_LIMIT (512) which budgets a
# full parse per file. Reusing 512 here would silently truncate any repo above ~500
# files and defeat the "whole-repo manifest" purpose.
DEFAULT_MAX_INVENTORY_FILES = 50_000
_LARGEST_FILES_LIMIT = 10

# Extension -> language label. Kept LOCAL rather than reusing repo_map._target_language_for_path
# (python/js/ts/rust only, with 10 narrow symbol-navigation callers we must not perturb).
_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".kt": "kotlin",
    ".lua": "lua",
    ".php": "php",
    ".swift": "swift",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".adoc": "asciidoc",
    ".txt": "text",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".cfg": "ini",
    ".ini": "ini",
}
# Common extension-less files, classified by basename so they are not dropped into "other".
_LANGUAGE_BY_BASENAME: dict[str, str] = {
    "makefile": "make",
    "dockerfile": "dockerfile",
    "license": "text",
    "readme": "text",
}

_CODE_SUFFIXES = frozenset({
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".php",
    ".py",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
})
_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})
_CONFIG_SUFFIXES = frozenset({".json", ".toml", ".yaml", ".yml", ".cfg", ".ini"})


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix:
        return _LANGUAGE_BY_SUFFIX.get(suffix, "other")
    return _LANGUAGE_BY_BASENAME.get(path.name.lower(), "other")


def _is_test_path(path: Path, root: Path) -> bool:
    name = path.name.lower()
    if name.startswith("test_") or name.startswith("test."):
        return True
    if ".test." in name or ".spec." in name:
        return True
    stem = path.stem.lower()
    if stem.endswith("_test") or stem.endswith(".test") or stem.endswith(".spec"):
        return True
    try:
        parent_parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
    except ValueError:
        parent_parts = set()
    return bool(parent_parts & {"tests", "test", "__tests__"})


def _category_for(path: Path, root: Path) -> str:
    # Test detection takes precedence: a test file is *also* code by extension, but its
    # role is what an agent wants counted. Categories are exclusive and partition the
    # non-binary files.
    if _is_test_path(path, root):
        return "test"
    suffix = path.suffix.lower()
    if suffix in _CODE_SUFFIXES:
        return "code"
    if suffix in _DOC_SUFFIXES:
        return "doc"
    if suffix in _CONFIG_SUFFIXES:
        return "config"
    return "other"


def _relative_posix(path: Path, root: Path) -> str:
    # Round-8 audit: `tg inventory <FILE>` walks a single file whose path IS the root, so
    # relative_to(root) yields a useless "." -- report the basename instead so largest_files names
    # the file. (For a directory root, files are always deeper, so path == root never fires there.)
    if path == root:
        return path.name
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _sorted_records(
    files: dict[str, int], byts: dict[str, int], key_name: str
) -> list[dict[str, Any]]:
    # bytes desc (dominant-signal-first for agent triage), name asc tie-break so the
    # ordering is fully deterministic and byte-stable across runs.
    return [
        {key_name: name, "files": files[name], "bytes": byts.get(name, 0)}
        for name in sorted(files, key=lambda n: (-byts.get(n, 0), n))
    ]


def build_inventory(
    path: str = ".",
    *,
    max_files: int = DEFAULT_MAX_INVENTORY_FILES,
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a walk-only inventory manifest for ``path``.

    Raises ``FileNotFoundError`` when ``path`` does not exist (fail closed — a missing
    path must never read as a valid empty repository).

    ``deadline_seconds``, when supplied, bounds the WALL-CLOCK time of the per-file
    stat()+``_looks_like_binary_file`` loop (the real cost on a huge workspace — the
    walk itself is cheap; see module docstring). A deadline that fires returns a
    partial, honestly-labeled inventory (``scan_limit.truncation_cause == "deadline"``)
    rather than hanging for minutes. It never lowers ``max_files`` itself.
    """
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"inventory path does not exist: {path}")

    # #52 fix (loop A): compute the absolute deadline BEFORE the walk (not after) and thread it
    # into _iter_repo_files -- previously the walk below had no time bound at all, only a
    # max_files COUNT bound (default 50_000), so a slow/huge walk could burn the entire
    # --deadline budget before the per-file loop further down even started (the 76s dogfood gap
    # on `tg inventory --deadline 30`). walk_deadline_hit folds into the per-file loop's own
    # deadline_hit local below, just like build_repo_map folds in its own walk-phase flag.
    deadline = time.monotonic() + deadline_seconds if deadline_seconds is not None else None
    walk_deadline_hit = _DeadlineBreakFlag()

    # Probe one file past the cap: _iter_repo_files' bucketed early-stop (repo_map.py)
    # can honor a real max_files bound and stop walking once it has enough, so we thread
    # the cap straight into the iterator instead of walking the whole tree and slicing
    # afterward. Asking for max_files + 1 (not max_files) lets us still tell "exactly
    # max_files files exist" apart from "more files exist" for the truncation notice,
    # without ever walking further than one file past the cap.
    walked = _iter_repo_files(
        root, max_files=max_files + 1, deadline_monotonic=deadline, deadline_hit=walk_deadline_hit
    )
    possibly_truncated = False
    truncation_cause: str | None = None
    if len(walked) > max_files:
        walked = walked[:max_files]
        possibly_truncated = True
        # The walker pre-excludes vendor/cache/index dirs (_SKIP_DIR_NAMES), so any
        # truncation is real project files, never vendor noise.
        truncation_cause = "project-files"

    resolved_root = root.resolve()
    total_files = 0
    total_bytes = 0
    binary_files = 0
    binary_bytes = 0
    lang_files: dict[str, int] = {}
    lang_bytes: dict[str, int] = {}
    cat_files: dict[str, int] = {}
    cat_bytes: dict[str, int] = {}
    dir_files: dict[str, int] = {}
    largest: list[tuple[int, str]] = []

    deadline_hit = walk_deadline_hit.hit

    for file_path in walked:
        if deadline is not None and time.monotonic() >= deadline:
            deadline_hit = True
            break
        try:
            size = file_path.stat().st_size
        except OSError:
            # Unreadable/vanished mid-walk: skip rather than count a phantom.
            continue
        total_files += 1
        total_bytes += size
        largest.append((size, _relative_posix(file_path, resolved_root)))

        # Top-level-directory breakdown: only files nested under a subdirectory. Root-level
        # files (README.md) count in totals but are not a "directory"; a single-file path
        # therefore yields an empty top_level_dirs.
        try:
            parts = file_path.relative_to(resolved_root).parts
        except ValueError:
            parts = ()
        if len(parts) > 1:
            dir_files[parts[0]] = dir_files.get(parts[0], 0) + 1

        if _looks_like_binary_file(file_path):
            binary_files += 1
            binary_bytes += size
            continue

        language = _language_for(file_path)
        lang_files[language] = lang_files.get(language, 0) + 1
        lang_bytes[language] = lang_bytes.get(language, 0) + size
        category = _category_for(file_path, resolved_root)
        cat_files[category] = cat_files.get(category, 0) + 1
        cat_bytes[category] = cat_bytes.get(category, 0) + size

    largest.sort(key=lambda item: (-item[0], item[1]))

    if deadline_hit:
        # The deadline BROKE the per-file loop before it processed the full walked set, so the time
        # budget -- not the file cap -- is the BINDING constraint on the count (strictly fewer than
        # max_files were processed). Label it "deadline" even when the walk was ALSO cap-truncated:
        # raising --max-repo-files would not help here, extending --deadline would. If the loop had
        # instead COMPLETED within budget (deadline_hit=False), the pre-loop "project-files" cap label
        # stands. (Dogfood 2026-07-05: the earlier "keep project-files" guard mislabeled a real 20s
        # deadline hit on C:/dev/projects as a file-cap truncation.)
        possibly_truncated = True
        truncation_cause = "deadline"

    return {
        "version": INVENTORY_SCHEMA_VERSION,
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "path": str(path),
        "coverage": {"language_scope": "extension-heuristic"},
        "totals": {"files": total_files, "bytes": total_bytes},
        "binary": {"files": binary_files, "bytes": binary_bytes},
        "languages": _sorted_records(lang_files, lang_bytes, "language"),
        "categories": _sorted_records(cat_files, cat_bytes, "category"),
        "top_level_dirs": [{"dir": name, "files": dir_files[name]} for name in sorted(dir_files)],
        "largest_files": [
            {"path": rel, "bytes": size} for size, rel in largest[:_LARGEST_FILES_LIMIT]
        ],
        "scan_limit": {
            "max_files": max_files,
            "scanned_files": total_files,
            "possibly_truncated": possibly_truncated,
            "truncation_cause": truncation_cause,
        },
    }


def render_inventory_text(inventory: dict[str, Any]) -> str:
    """One-screen human summary mirroring tg's other summary conventions."""
    totals = inventory["totals"]
    lines = [
        f"inventory: {totals['files']} files, {_human_bytes(totals['bytes'])} "
        f"({inventory['path']})",
    ]
    binary = inventory["binary"]
    if binary["files"]:
        lines.append(f"  binary: {binary['files']} files, {_human_bytes(binary['bytes'])}")
    if inventory["languages"]:
        top_langs = ", ".join(
            f"{rec['language']} {rec['files']}" for rec in inventory["languages"][:6]
        )
        lines.append(f"  languages: {top_langs}")
    if inventory["categories"]:
        cats = ", ".join(f"{rec['category']} {rec['files']}" for rec in inventory["categories"])
        lines.append(f"  categories: {cats}")
    scan = inventory["scan_limit"]
    if scan["possibly_truncated"]:
        if scan["truncation_cause"] == "deadline":
            lines.append(
                "  [!] stopped after the time budget (cause=deadline); "
                "counts are a floor, not complete."
            )
        else:
            lines.append(
                f"  [!] truncated at max_files={scan['max_files']} "
                f"(cause={scan['truncation_cause']}); counts are a floor, not complete."
            )
    return "\n".join(lines)


def _human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
