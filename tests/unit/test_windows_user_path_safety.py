"""F.6 (A-07): the persistent Windows User PATH is never overwritten when it cannot be read, and an
existing REG_EXPAND_SZ type is preserved from the ONE successful read. The registry is faked; the
real one is never reached."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tensor_grep.cli import windows_launcher as wl


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _fake_winreg(monkeypatch, *, query):
    """``query`` is an Exception to raise, a ``(value, type)`` tuple, or a list of those consumed
    one per QueryValueEx call (the last one repeats nothing: an exhausted list raises)."""
    sets: list[tuple[str, int]] = []
    queue = list(query) if isinstance(query, list) else None
    fake = types.SimpleNamespace(
        # HKEY_LOCAL_MACHINE: _ensure_windows_managed_native_first_on_path evaluates
        # _doctor_fresh_shell_path_value(), which reads HKLM.
        HKEY_CURRENT_USER=object(),
        HKEY_LOCAL_MACHINE=object(),
        KEY_SET_VALUE=2,
        KEY_READ=1,
        REG_SZ=1,
        REG_EXPAND_SZ=2,
        OpenKey=lambda *a, **k: _Key(),
        SetValueEx=lambda key, name, _r, vtype, value: sets.append((value, vtype)),
    )

    def _query(key, name):
        result = query
        if queue is not None:
            if not queue:
                raise PermissionError(13, "denied (second read)")
            result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    fake.QueryValueEx = _query
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return sets


def _managed(monkeypatch, tmp_path) -> Path:
    managed = tmp_path / "managed"
    monkeypatch.setattr(wl, "_windows_managed_native_bin_dir", lambda: managed)
    monkeypatch.setattr(wl, "_windows_python_subprocess_resolution_blocker", lambda **k: None)
    # `_self` in windows_launcher is the late-bound proxy to tensor_grep.cli.main, so patch the
    # fresh-shell PATH there (raising=True: a rename fails loudly). An EMPTY fresh-shell PATH keeps
    # the "managed dir already first" short-circuit from firing, so the controls really write.
    from tensor_grep.cli import main as cli_main

    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda *a, **k: "")
    monkeypatch.setenv("PATH", str(managed))
    return managed / "tg.exe"


def test_read_error_other_than_missing_value_is_not_empty_path(monkeypatch):
    _fake_winreg(monkeypatch, query=PermissionError(13, "denied"))
    with pytest.raises(OSError):
        wl._windows_user_path_value()


def test_missing_value_reads_as_empty(monkeypatch):  # control
    _fake_winreg(monkeypatch, query=FileNotFoundError(2, "missing"))
    assert wl._windows_user_path_value() == ""


def test_unreadable_user_path_is_never_overwritten(monkeypatch, tmp_path):
    sets = _fake_winreg(monkeypatch, query=PermissionError(13, "denied"))
    msg = wl._ensure_windows_managed_native_first_on_path(_managed(monkeypatch, tmp_path))
    assert sets == []
    assert msg is not None and "User PATH repair warning" in msg


def test_non_string_user_path_is_never_overwritten(monkeypatch, tmp_path):
    sets = _fake_winreg(monkeypatch, query=(b"\x00\x01", 3))
    wl._ensure_windows_managed_native_first_on_path(_managed(monkeypatch, tmp_path))
    assert sets == []


def test_absent_user_path_value_is_written(monkeypatch, tmp_path):  # control
    sets = _fake_winreg(monkeypatch, query=FileNotFoundError(2, "missing"))
    native = _managed(monkeypatch, tmp_path)
    wl._ensure_windows_managed_native_first_on_path(native)
    assert len(sets) == 1 and sets[0][0].split(";")[0] == str(native.parent)


def test_readable_user_path_is_prepended_and_percent_text_kept(monkeypatch, tmp_path):  # control
    sets = _fake_winreg(monkeypatch, query=(r"C:\Foo;%USERPROFILE%\bin", 2))
    native = _managed(monkeypatch, tmp_path)
    wl._ensure_windows_managed_native_first_on_path(native)
    assert len(sets) == 1
    assert sets[0][0].split(";")[0] == str(native.parent)
    assert sets[0][0].endswith(r"C:\Foo;%USERPROFILE%\bin") and sets[0][1] == 2


def test_expand_sz_type_preserved_without_percent(monkeypatch, tmp_path):
    sets = _fake_winreg(monkeypatch, query=(r"C:\Foo;C:\Bar", 2))
    wl._ensure_windows_managed_native_first_on_path(_managed(monkeypatch, tmp_path))
    assert len(sets) == 1 and sets[0][1] == 2


def test_reg_sz_stays_reg_sz(monkeypatch, tmp_path):  # control
    sets = _fake_winreg(monkeypatch, query=(r"C:\Foo;C:\Bar", 1))
    wl._ensure_windows_managed_native_first_on_path(_managed(monkeypatch, tmp_path))
    assert len(sets) == 1 and sets[0][1] == 1


def test_type_comes_from_one_read_never_a_second(monkeypatch, tmp_path):
    # The FIRST query succeeds with REG_EXPAND_SZ; any later query raises. A setter that re-reads
    # the type would silently downgrade to REG_SZ.
    sets = _fake_winreg(monkeypatch, query=[(r"C:\Foo;C:\Bar", 2)])
    wl._ensure_windows_managed_native_first_on_path(_managed(monkeypatch, tmp_path))
    assert len(sets) == 1 and sets[0][1] == 2
