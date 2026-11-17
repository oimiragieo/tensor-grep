import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from tensor_grep.cli import session_resume_service as service
from tensor_grep.cli.session_store import SessionStaleError, get_session, open_session


def _install_surface(monkeypatch, tmp_path: Path, *, current_tag: str):
    state = {"locked": False, "wrote_locked": False}
    initial = {"session_provenance": {"tag": "initial"}, "snapshot": []}
    session_path = tmp_path / "session.json"
    session_path.write_text("{}", encoding="utf-8")

    @contextmanager
    def lock(_path):
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    def build(**_kwargs):
        assert state["locked"] is False
        return object()

    def write(_root, _path, _payload, **_kwargs):
        state["wrote_locked"] = state["locked"]

    monkeypatch.setattr(service, "_session_root_for_payload", lambda *_args: tmp_path)
    monkeypatch.setattr(service, "_session_payload_path", lambda *_args: session_path)
    monkeypatch.setattr(service, "_load_session_payload", lambda *_args: dict(initial))
    monkeypatch.setattr(
        service,
        "get_session",
        lambda *_args: {"session_provenance": {"tag": current_tag}, "snapshot": []},
    )
    monkeypatch.setattr(service, "index_lock", lock)
    monkeypatch.setattr(service, "build_prepare_snapshot", build)
    monkeypatch.setattr(service, "asdict", lambda _snapshot: {"query": "needle"})
    monkeypatch.setattr(service, "seal_session", lambda *_args: None)
    monkeypatch.setattr(service, "atomic_write_bytes_confined", write)
    return state


def test_session_prepare_builds_outside_index_lock_and_publishes_inside(
    tmp_path, monkeypatch
) -> None:
    state = _install_surface(monkeypatch, tmp_path, current_tag="initial")

    result = service.session_prepare("session", "needle", str(tmp_path))

    assert result["query"] == "needle"
    assert state["wrote_locked"] is True


def test_session_prepare_refuses_concurrent_payload_change(tmp_path, monkeypatch) -> None:
    state = _install_surface(monkeypatch, tmp_path, current_tag="newer")

    with pytest.raises(SessionStaleError, match="changed while preparing"):
        service.session_prepare("session", "needle", str(tmp_path))

    assert state["wrote_locked"] is False


def test_session_prepare_refuses_byte_identical_session_store_swap(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")
    opened = open_session(str(project), max_repo_files=10)
    root = Path(opened.root)
    sessions = root / ".tensor-grep" / "sessions"
    replacement = sessions.with_name("sessions-replacement")
    original = sessions.with_name("sessions-original")
    shutil.copytree(sessions, replacement)

    def build(**_kwargs):
        sessions.rename(original)
        replacement.rename(sessions)
        return object()

    monkeypatch.setattr(service, "build_prepare_snapshot", build)
    monkeypatch.setattr(service, "asdict", lambda _snapshot: {"query": "needle"})

    with pytest.raises(SessionStaleError, match="store changed while preparing"):
        service.session_prepare(opened.session_id, "needle", str(project))

    assert "last_prepare" not in get_session(opened.session_id, str(project))
    original_payload = json.loads(
        (original / f"{opened.session_id}.json").read_text(encoding="utf-8")
    )
    assert "last_prepare" not in original_payload
