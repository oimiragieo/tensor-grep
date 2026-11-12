import os
from pathlib import Path


def resolve_executable_path(binary_name: str) -> str | None:
    import shutil

    cwd = Path.cwd().resolve()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        path_dir = Path(directory or os.curdir).expanduser()
        try:
            path_dir = path_dir.resolve()
        except OSError:
            continue
        if path_dir == cwd or cwd in path_dir.parents:
            continue
        if candidate := shutil.which(str(path_dir / binary_name)):
            try:
                resolved = Path(candidate).resolve()
            except (OSError, RuntimeError):
                continue
            if resolved != cwd and cwd not in resolved.parents:
                return str(resolved)
    return None
