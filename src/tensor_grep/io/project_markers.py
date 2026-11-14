"""Zero-dependency project-marker check shared by broad-scan guards."""

from pathlib import Path

from tensor_grep.io.scan_limits import BROAD_WORKSPACE_PROJECT_MARKERS


def path_has_project_marker(path: Path) -> bool:
    for marker in BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False
