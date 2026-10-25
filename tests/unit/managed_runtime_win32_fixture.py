"""Finite Win32 boundary fixture; production trust predicates remain real."""

from __future__ import annotations

from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec

_USER = "S-1-5-21-111-222-333-1001"


_SYSTEM = "S-1-5-18"


_EVERYONE = "S-1-1-0"


_FULL = 0x1F01FF


_DRIVE = "C:\\"


_PARENT = "C:\\managed"


_ROOT_PATH = "C:\\managed\\tensor-grep"


_LEAF_PATH = "C:\\managed\\tensor-grep\\runtime-selection.json"


_DRIVE_HANDLE = "drive-root"


_PARENT_HANDLE = "managed-parent"


_ROOT_HANDLE = "managed-root"


_LEAF_HANDLE = "selector"


_FIRST_BYTES = (
    b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3"}\n'
)


def _nt_path(path: str) -> str:
    return "\\\\?\\" + path


class _ReaderBoundary:
    def __init__(self, payload: bytes = _FIRST_BYTES) -> None:
        self.handles = {
            _DRIVE: _DRIVE_HANDLE,
            _PARENT: _PARENT_HANDLE,
            _ROOT_PATH: _ROOT_HANDLE,
        }
        self.leaf = _LEAF_PATH
        self.closed: list[Any] = []
        self.open_calls: list[tuple[str, bool, int, bool]] = []
        self.final_paths: dict[str, str | None] = {
            _DRIVE_HANDLE: _nt_path(_DRIVE),
            _PARENT_HANDLE: _nt_path(_PARENT),
            _ROOT_HANDLE: _nt_path(_ROOT_PATH),
            _LEAF_HANDLE: _nt_path(_LEAF_PATH),
        }
        self.security: dict[str, tuple[str, list[tuple[str, int]]] | None] = {
            _DRIVE_HANDLE: (_SYSTEM, [(_EVERYONE, _FULL)]),
            _PARENT_HANDLE: (_USER, [(_USER, _FULL)]),
            _ROOT_HANDLE: (_USER, [(_USER, _FULL)]),
            _LEAF_HANDLE: (_USER, [(_USER, _FULL)]),
        }
        self.links: int | None = 1
        self.payload: bytes | None = payload
        self.read_calls: list[tuple[str, int]] = []
        self.refused_paths: set[str] = set()
        self.missing_leaf = False
        self.raise_final_for: tuple[str, ...] = ()
        self.raise_entries_for: tuple[str, ...] = ()

    def open_no_follow(
        self,
        path: str,
        *,
        directory: bool = False,
        share: int = 1,
        raise_missing: bool = False,
    ) -> str | None:
        self.open_calls.append((path, directory, share, raise_missing))
        if path in self.refused_paths:
            return None
        if not directory and path == self.leaf and self.missing_leaf:
            if raise_missing:
                raise FileNotFoundError(path)
            return None
        if directory:
            return self.handles.get(path)
        return _LEAF_HANDLE if path == self.leaf else None

    def final_path(self, handle: str) -> str | None:
        if handle in self.raise_final_for:
            raise OSError("private path detail")
        return self.final_paths.get(handle)

    def owner_and_dacl_entries(self, handle: str) -> tuple[str, list[tuple[str, int]]] | None:
        if handle in self.raise_entries_for:
            raise OSError("private security detail")
        return self.security.get(handle)

    def owner_and_dacl_sids(self, handle: str) -> tuple[str, list[str]] | None:
        queried = self.owner_and_dacl_entries(handle)
        if queried is None:
            return None
        return queried[0], [sid for sid, _mask in queried[1]]

    def read_all(self, handle: str, *, limit: int = 8192) -> bytes | None:
        self.read_calls.append((handle, limit))
        return self.payload


def _install_reader_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: _ReaderBoundary | None = None
) -> _ReaderBoundary:
    if boundary is None:
        boundary = _ReaderBoundary()
    monkeypatch.setattr(winsec, "open_no_follow", boundary.open_no_follow)
    monkeypatch.setattr(winsec, "close_handle", boundary.closed.append)
    monkeypatch.setattr(winsec, "final_path", boundary.final_path)
    monkeypatch.setattr(winsec, "owner_and_dacl_entries", boundary.owner_and_dacl_entries)
    monkeypatch.setattr(winsec, "owner_and_dacl_sids", boundary.owner_and_dacl_sids)
    monkeypatch.setattr(winsec, "link_count", lambda _handle: boundary.links)
    monkeypatch.setattr(winsec, "read_all", boundary.read_all)
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: _USER)
    monkeypatch.setattr(trust, "_win_owner_and_dacl", boundary.owner_and_dacl_sids)
    monkeypatch.setattr(trust, "_win_dacl_entries", boundary.owner_and_dacl_entries)
    return boundary
