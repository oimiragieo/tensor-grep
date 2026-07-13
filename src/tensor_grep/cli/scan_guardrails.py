from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.io.directory_scanner import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".cache",
    ".cargo",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

_BROAD_SYSTEM_SCAN_DIR_NAMES = {
    "appdata",
    "program files",
    "program files (x86)",
    "programdata",
    "temp",
    "tmp",
    "users",
    "windows",
}

_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS


@dataclass(frozen=True)
class BroadScanRefusal:
    kind: str
    names: list[str]


class BroadScanRefusedError(ValueError):
    def __init__(self, refusal: BroadScanRefusal):
        self.refusal = refusal
        super().__init__(format_broad_scan_error(refusal))


def _normalized_path(path: Path) -> str:
    return os.path.normcase(str(path))


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()


def _same_path(left: Path, right: Path) -> bool:
    return _normalized_path(_safe_resolve(left)) == _normalized_path(_safe_resolve(right))


def _path_has_project_marker(path: Path) -> bool:
    for marker in _BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


def _direct_temp_paths() -> list[Path]:
    paths: list[Path] = []
    for env_name in ("TEMP", "TMP"):
        raw_path = os.environ.get(env_name)
        if raw_path:
            paths.append(Path(raw_path))
    return paths


def _is_drive_or_filesystem_root(path: Path) -> bool:
    resolved = _safe_resolve(path)
    return resolved.parent == resolved


def _system_root_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    temp_paths = _direct_temp_paths()
    home_path = Path.home()
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = _safe_resolve(Path(raw_path))
        try:
            if not path.is_dir():
                continue
        except OSError:
            continue
        if _is_drive_or_filesystem_root(path):
            found.add(str(path))
            continue
        if _same_path(path, home_path):
            found.add("user home")
            continue
        if any(_same_path(path, temp_path) for temp_path in temp_paths):
            found.add(path.name or "temp")
            continue
        if _path_has_project_marker(path):
            continue
        path_name = path.name.lower()
        if path_name in _BROAD_SYSTEM_SCAN_DIR_NAMES:
            found.add(path.name)
    return sorted(found, key=lambda item: item.lower())


def _generated_root_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    generated_names = {name.lower() for name in _BROAD_GENERATED_SCAN_DIR_NAMES}
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = _safe_resolve(Path(raw_path))
        try:
            if not path.is_dir() or _path_has_project_marker(path):
                continue
        except OSError:
            continue
        path_name = path.name.lower()
        if path_name in generated_names:
            found.add(path.name)
    return sorted(found, key=lambda item: item.lower())


def _workspace_project_child_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = _safe_resolve(Path(raw_path))
        try:
            if not path.is_dir():
                continue
            # Item #158 (`tg scan` sibling of #154 in main.py): a root carrying its OWN project
            # marker is not skipped outright -- it can *also* be a workspace parent (a marked
            # root with its own `package.json` that also holds many independently-marked sibling
            # projects). A marked root uses the higher "marked-root" threshold, since an ordinary
            # single project can legitimately carry a handful of marked children (a Cargo
            # workspace member, a vendored submodule) without being a workspace parent; an
            # unmarked root keeps the original (lower) threshold.
            threshold = (
                _BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
                if _path_has_project_marker(path)
                else _BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
            )
            child_project_names: list[str] = []
            for child in path.iterdir():
                try:
                    if child.is_dir() and _path_has_project_marker(child):
                        child_project_names.append(child.name)
                except OSError:
                    continue
            if len(child_project_names) >= threshold:
                found.update(child_project_names)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


# A max_depth only counts as a real traversal BOUND if it is modest. Treating any non-None
# value as "bounded" let `--max-depth 1000000` rubber-stamp a hostile-root scan (defeating the
# broad-scan refusal entirely). Deeper-than-this scans of a system/generated/workspace root must
# opt in explicitly via --allow-broad-generated-scan.
_MAX_REASONABLE_SCAN_DEPTH = 50


def _is_bounded_depth(max_depth: int | None) -> bool:
    return max_depth is not None and 0 <= max_depth <= _MAX_REASONABLE_SCAN_DEPTH


def _has_scan_bound(
    *,
    globs: list[str] | None,
    file_types: list[str] | None,
    max_depth: int | None,
) -> bool:
    return bool(_is_bounded_depth(max_depth) or globs or file_types)


def find_broad_scan_refusal(
    paths: list[str],
    *,
    globs: list[str] | None = None,
    file_types: list[str] | None = None,
    max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
) -> BroadScanRefusal | None:
    if allow_broad_generated_scan:
        return None

    # Direct system roots need a traversal-depth bound. A glob alone still asks
    # the scanner to walk hostile roots such as %TEMP%, AppData, or C:\.
    system_roots = _system_root_names(paths)
    if system_roots and not _is_bounded_depth(max_depth):
        return BroadScanRefusal("system-root", system_roots)

    if _has_scan_bound(globs=globs, file_types=file_types, max_depth=max_depth):
        return None

    generated_roots = _generated_root_names(paths)
    if generated_roots:
        return BroadScanRefusal("generated-root", generated_roots)

    workspace_projects = _workspace_project_child_names(paths)
    if workspace_projects:
        return BroadScanRefusal("workspace-root", workspace_projects)

    return None


def ensure_scan_not_broad(
    paths: list[str],
    *,
    globs: list[str] | None = None,
    file_types: list[str] | None = None,
    max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
) -> None:
    refusal = find_broad_scan_refusal(
        paths,
        globs=globs,
        file_types=file_types,
        max_depth=max_depth,
        allow_broad_generated_scan=allow_broad_generated_scan,
    )
    if refusal is not None:
        raise BroadScanRefusedError(refusal)


def format_broad_scan_error(refusal: BroadScanRefusal) -> str:
    visible_names = ", ".join(refusal.names[:8])
    if len(refusal.names) > 8:
        visible_names = f"{visible_names}, ..."

    if refusal.kind == "workspace-root":
        detail = (
            "path looks like a multi-project workspace root "
            f"({visible_names}). Scope --path to one project, add --glob, --type, "
            "--max-depth, or pass --allow-broad-generated-scan to opt in."
        )
    elif refusal.kind == "system-root":
        detail = (
            "path is a system, temp, or user-wide root "
            f"({visible_names}). Scope --path to a project/subdirectory, add --max-depth, "
            "or pass --allow-broad-generated-scan to opt in."
        )
    else:
        detail = (
            "path is a generated, cache, or dependency root "
            f"({visible_names}). Scope --path to source files, add --glob, --type, "
            "--max-depth, or pass --allow-broad-generated-scan to opt in."
        )

    return (
        "Error: broad AST scan refused as a safety guard, not a zero-match result: "
        f"{detail}\n"
        "For bounded scans:\n"
        "tg scan --ruleset <name> --path <project-or-subdir>\n"
        "tg scan --ruleset <name> --path <path> --max-depth <N>\n"
        'tg scan --ruleset <name> --path <path> --glob "*.py"\n'
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )
