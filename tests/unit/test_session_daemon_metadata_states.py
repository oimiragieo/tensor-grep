"""Malformed metadata is NOT proof of absence (PR #1197 closure audit).

``_read_daemon_metadata`` collapsed "file absent", "read failed" and "not valid JSON / not a dict" into
``None``, and the exit table then accepted that as ``proof="no_metadata"`` (exit 0). The reader now tells
the three states apart (``_metadata_state``: ``absent`` / ``unreadable`` / ``invalid`` / ``ok``). Only
ABSENT may produce ``proof "no_metadata"``; UNREADABLE and INVALID keep the file and are unconfirmed
(``metadata_unreadable`` / ``metadata_invalid``) -> exit 2 for both ``stop`` and ``status``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_stop_cli as cli
from tensor_grep.cli import session_daemon_trust as trust


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.3)


def _meta_path(root: Path) -> Path:
    path = sd._daemon_metadata_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_INVALID_BYTES = [
    b"{",
    b"",
    b"[1, 2]",
    b'"text"',
    b"null",
    b"123",
    b"true",
    b"\xff\xfe\x00bad-utf8",
]


@pytest.mark.parametrize("content", _INVALID_BYTES, ids=lambda b: repr(b)[:18])
def test_invalid_metadata_is_unconfirmed_and_kept_for_stop_and_status(
    tmp_path: Path, content: bytes
) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    path.write_bytes(content)
    stopped = sd.stop_session_daemon(str(root))
    assert stopped["running"] is True, "malformed metadata was taken as proof of absence"
    assert stopped["unconfirmed_reason"] == "metadata_invalid"
    assert cli.stop_exit_code(stopped) == 2
    assert path.read_bytes() == content  # the file is kept untouched
    status = sd.get_session_daemon_status(str(root))
    assert status["metadata_error"] == "metadata_invalid"
    assert cli.status_exit_code(status) == 2
    assert path.read_bytes() == content


def test_unreadable_metadata_is_unconfirmed_and_kept(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    path.mkdir()  # reading a directory as a file raises OSError on every platform
    stopped = sd.stop_session_daemon(str(root))
    assert stopped["running"] is True
    assert stopped["unconfirmed_reason"] == "metadata_unreadable"
    assert cli.stop_exit_code(stopped) == 2
    status = sd.get_session_daemon_status(str(root))
    assert status["metadata_error"] == "metadata_unreadable"
    assert cli.status_exit_code(status) == 2
    assert path.is_dir()


def test_a_read_oserror_is_unreadable_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    path.write_text("{}", encoding="utf-8")

    def _denied(_path: Path) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(trust, "_read_metadata_text", _denied)
    assert trust._metadata_state(path) == ("unreadable", None)
    stopped = sd.stop_session_daemon(str(root))
    assert stopped["unconfirmed_reason"] == "metadata_unreadable"
    assert cli.stop_exit_code(stopped) == 2
    assert path.exists()


def test_an_empty_dict_file_is_existing_metadata_not_absence(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    path.write_text("{}", encoding="utf-8")
    stopped = sd.stop_session_daemon(str(root))
    assert stopped["running"] is True
    assert stopped["unconfirmed_reason"] == "endpoint_unverifiable"
    assert cli.stop_exit_code(stopped) == 2
    assert path.exists()


def test_a_truly_absent_file_is_still_a_clean_no_metadata_exit_0(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    assert trust._metadata_state(_meta_path(root)) == ("absent", None)
    stopped = sd.stop_session_daemon(str(root))
    assert stopped["running"] is False
    assert stopped["proof"] == "no_metadata"
    assert cli.stop_exit_code(stopped) == 0
    status = sd.get_session_daemon_status(str(root))
    assert status["running"] is False
    assert "metadata_error" not in status
    assert cli.status_exit_code(status) == 0


def test_the_three_states_are_told_apart(tmp_path: Path) -> None:
    path = tmp_path / "daemon.json"
    assert trust._metadata_state(path) == ("absent", None)
    path.write_text('{"a": 1}', encoding="utf-8")
    assert trust._metadata_state(path) == ("ok", {"a": 1})
    path.write_text("[1]", encoding="utf-8")
    assert trust._metadata_state(path) == ("invalid", None)
    path.unlink()
    path.mkdir()
    assert trust._metadata_state(path) == ("unreadable", None)


def test_the_legacy_reader_never_returns_a_non_dict(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    for content in (b"[1]", b"{", b'"x"'):
        _meta_path(root).write_bytes(content)
        assert sd._read_daemon_metadata(root) is None


def test_the_guarded_remover_never_deletes_unreadable_or_invalid_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    path.write_bytes(b"{")
    sd._remove_daemon_metadata(root, expected_pid=1, expected_port=2)
    assert path.read_bytes() == b"{"


def test_the_cli_exits_2_for_invalid_metadata_in_both_modes(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    root = tmp_path.resolve()
    _meta_path(root).write_bytes(b"{")
    for command in ("stop", "status"):
        for flags in ([], ["--json"]):
            result = CliRunner().invoke(app, ["session", "daemon", command, str(root), *flags])
            assert result.exit_code == 2, (command, flags, result.exit_code, result.output)
            assert result.output.isascii()
            if flags:
                payload: dict[str, Any] = json.loads(result.output)
                assert payload["running"] is not None
                assert "error" in payload
    assert _meta_path(root).read_bytes() == b"{"


# ---- a dangling link at daemon.json is an EXISTING entry, never "absent" ----


def _dangling_symlink(path: Path, tmp_path: Path) -> None:
    target = tmp_path / "gone-target"
    try:
        path.symlink_to(target)  # the target never exists
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")


def _dangling_junction(path: Path, tmp_path: Path) -> None:
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("junctions are a Windows reparse-point variant")
    target = tmp_path / "junction-target"
    target.mkdir()
    done = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(target)], capture_output=True, check=False
    )
    if done.returncode != 0:
        pytest.skip("cannot create a junction here")
    target.rmdir()  # the junction now dangles


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_a_dangling_link_is_unreadable_not_absent_for_stop_and_status(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path.resolve()
    path = _meta_path(root)
    (_dangling_symlink if kind == "symlink" else _dangling_junction)(path, tmp_path)
    assert os.path.lexists(path), "precondition: the dangling entry exists"
    assert trust._metadata_state(path) == ("unreadable", None)
    stopped = sd.stop_session_daemon(str(root))
    assert stopped.get("proof") != "no_metadata", "a dangling link was taken as proof of absence"
    assert stopped["running"] is True
    assert stopped["unconfirmed_reason"] == "metadata_unreadable"
    assert cli.stop_exit_code(stopped) == 2
    status = sd.get_session_daemon_status(str(root))
    assert status["metadata_error"] == "metadata_unreadable"
    assert cli.status_exit_code(status) == 2
    assert os.path.lexists(path), "the entry must be kept"


def test_a_failed_inspection_of_a_missing_file_is_unreadable_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "daemon.json"  # genuinely missing, but the lstat that would prove it fails

    def _boom(_p: Any) -> Any:
        raise PermissionError("cannot inspect")

    monkeypatch.setattr(trust, "_lstat", _boom)
    assert trust._metadata_state(path) == ("unreadable", None)
