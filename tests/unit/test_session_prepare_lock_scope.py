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


@pytest.mark.parametrize(
    "original_consumer",
    [None, "def unrelated():\n    return None\n"],
    ids=("added-path", "same-path-changed-content"),
)
def test_session_prepare_refuses_evidence_built_from_temporary_project_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_consumer: str | None,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "billing.py").write_text(
        "def calculate_late_fee(balance, days_late):\n"
        "    return balance * days_late\n\n"
        "def apply_late_fee(account):\n"
        "    return calculate_late_fee(account['balance'], account['days_late'])\n\n"
        "def process_billing_cycle(accounts):\n"
        "    return [apply_late_fee(account) for account in accounts]\n",
        encoding="utf-8",
    )
    for name in ("z_padding.py", "zz_padding.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    if original_consumer is not None:
        (project / "consumer.py").write_text(original_consumer, encoding="utf-8")

    opened = open_session(str(project), max_repo_files=1)
    payload = get_session(opened.session_id, str(project))
    assert payload["repo_map"]["scan_limit"]["possibly_truncated"] is True

    replacement = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement)
    (replacement / "consumer.py").write_text(
        "from billing import calculate_late_fee, apply_late_fee, process_billing_cycle\n\n"
        "def temporary_consumer(account):\n"
        "    calculate_late_fee(account['balance'], account['days_late'])\n"
        "    apply_late_fee(account)\n"
        "    return process_billing_cycle([account])\n",
        encoding="utf-8",
    )

    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        project.rename(parked)
        replacement.rename(project)
        try:
            snapshot = real_build(**kwargs)
            assert any(
                Path(str(caller.get("file"))).name == "consumer.py"
                for caller in snapshot.blast_radius_floor.get("top_callers", [])
            ), snapshot.blast_radius_floor
            return snapshot
        finally:
            project.rename(replacement)
            parked.rename(project)

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(
            opened.session_id,
            "the billing job should skip accounts that already paid earlier this month",
            str(project),
        )

    assert "last_prepare" not in get_session(opened.session_id, str(project))

    monkeypatch.setattr(service, "build_prepare_snapshot", real_build)
    stable = service.session_prepare(
        opened.session_id,
        "the billing job should skip accounts that already paid earlier this month",
        str(project),
    )
    assert stable["session_id"] == opened.session_id
    assert not any(
        Path(str(caller.get("file"))).name == "consumer.py"
        for caller in stable["blast_radius_floor"].get("top_callers", [])
    )
