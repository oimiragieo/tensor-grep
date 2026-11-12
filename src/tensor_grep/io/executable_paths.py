import os
from pathlib import Path


def resolve_executable_path(binary_name: str) -> str | None:
    import shutil

    raw_extensions = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    extensions = [ext for ext in raw_extensions.split(os.pathsep) if ext]
    cwd = Path.cwd().resolve()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        path_dir = Path(directory or os.curdir).expanduser()
        try:
            path_dir = path_dir.resolve()
        except OSError:
            continue
        if path_dir == cwd or cwd in path_dir.parents:
            continue
        names = [binary_name]
        if os.name == "nt" and not any(
            binary_name.lower().endswith(ext.lower()) for ext in extensions
        ):
            names.extend(binary_name + ext for ext in extensions)
        for name in names:
            if candidate := shutil.which(str(path_dir / name)):
                try:
                    resolved = Path(candidate).resolve()
                except (OSError, RuntimeError):
                    continue
                if resolved != cwd and cwd not in resolved.parents:
                    return str(resolved)
    return None
