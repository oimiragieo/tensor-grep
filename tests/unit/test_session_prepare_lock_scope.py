from contextlib import contextmanager
from pathlib import Path

import pytest

from tensor_grep.cli import session_resume_service as service
from tensor_grep.cli.session_store import SessionStaleError


def _install_surface(monkeypatch, tmp_path: Path, *, current_tag: str):
    state = {"locked": False, "wrote_locked": False}
    initial = {"session_provenance": {"tag": "initial"}, "snapshot": []}

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

    def write(_path, _payload):
        state["wrote_locked"] = state["locked"]

    monkeypatch.setattr(service, "_session_root_for_payload", lambda *_args: tmp_path)
    monkeypatch.setattr(service, "_session_payload_path", lambda *_args: tmp_path / "session.json")
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
    monkeypatch.setattr(service, "_write_json_atomic", write)
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
