from __future__ import annotations

import ntpath
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, decode_selection

_REFUSAL = "managed runtime selection could not be trusted"
_SELECTION_FILE = "runtime-selection.json"


@dataclass(frozen=True)
class ManagedRuntimePaths:
    selection: ManagedRuntimeSelection
    native: Path
    python: Path


def _final_key(path: str | None) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return ntpath.normcase(ntpath.normpath(path))


def _is_direct_child(child: str | None, parent: str | None, name: str) -> bool:
    child_key = _final_key(child)
    parent_key = _final_key(parent)
    return (
        child_key is not None
        and parent_key is not None
        and child_key == ntpath.join(parent_key, ntpath.normcase(name))
    )


def read_managed_runtime(*, trusted_root: Path) -> ManagedRuntimePaths | None:
    if sys.platform != "win32":
        raise OSError("managed runtime selection is supported only on Windows")
    root = trusted_root
    if not isinstance(root, Path):
        raise OSError(_REFUSAL)
    root_text = str(root)
    if root_text.startswith(("\\\\", "\\\\?\\", "\\\\.\\")) or "\x00" in root_text:
        raise OSError(_REFUSAL)
    drive, _tail = ntpath.splitdrive(root_text)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isascii() or not drive[0].isalpha():
        raise OSError(_REFUSAL)
    if any(part in {".", ".."} for part in root_text.replace("/", "\\").split("\\")):
        raise OSError(_REFUSAL)
    if root.anchor.casefold() != f"{drive}\\".casefold() or not 2 <= len(root.parts) <= 65:
        raise OSError(_REFUSAL)
    try:
        with ExitStack() as held:
            user_sid = trust._win_current_user_sid()
            if user_sid is None:
                raise OSError(_REFUSAL)
            current = Path(root.anchor)
            handle = winsec.open_no_follow(str(current), directory=True, share=3)
            if handle is None:
                raise OSError(_REFUSAL)
            held.callback(winsec.close_handle, handle)
            parent_final = winsec.final_path(handle)
            if _final_key(parent_final) != _final_key(str(current)):
                raise OSError(_REFUSAL)
            for component in root.parts[1:]:
                current = current / component
                child = winsec.open_no_follow(str(current), directory=True, share=3)
                if child is None:
                    raise OSError(_REFUSAL)
                held.callback(winsec.close_handle, child)
                child_final = winsec.final_path(child)
                if not _is_direct_child(child_final, parent_final, component):
                    raise OSError(_REFUSAL)
                if current == root:
                    if not trust._parent_handle_ok(child):
                        raise OSError(_REFUSAL)
                else:
                    queried = winsec.owner_and_dacl_entries(child)
                    if queried is None or not trust._windows_owner_ok(queried[0], user_sid):
                        raise OSError(_REFUSAL)
                    if not trust._windows_ancestor_dacl_ok(queried[1], user_sid):
                        raise OSError(_REFUSAL)
                parent_final = child_final
            try:
                leaf = winsec.open_no_follow(
                    str(current / _SELECTION_FILE), share=5, raise_missing=True
                )
            except FileNotFoundError:
                return None
            if leaf is None:
                raise OSError(_REFUSAL)
            held.callback(winsec.close_handle, leaf)
            if not trust._windows_handle_trusted(leaf, check_dacl=True):
                raise OSError(_REFUSAL)
            if winsec.link_count(leaf) != 1:
                raise OSError(_REFUSAL)
            if not _is_direct_child(winsec.final_path(leaf), parent_final, _SELECTION_FILE):
                raise OSError(_REFUSAL)
            raw = winsec.read_all(leaf, limit=512)
            if raw is None:
                raise OSError(_REFUSAL)
    except Exception:
        raise OSError(_REFUSAL) from None
    selection = decode_selection(raw)
    generation_root = root / "runtimes" / selection.generation
    return ManagedRuntimePaths(
        selection=selection,
        native=generation_root / "bin" / "tg.exe",
        python=generation_root / ".venv" / "Scripts" / "python.exe",
    )
