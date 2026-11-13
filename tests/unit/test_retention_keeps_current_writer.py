from contextlib import contextmanager
from threading import Event, Thread, current_thread

import pytest

from tensor_grep.cli import checkpoint_store, session_store
from tensor_grep.io.checkpoint_directory import require_supported


@pytest.fixture(params=["session", "checkpoint"])
def storage(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == "checkpoint":
        try:
            require_supported()
        except OSError as exc:
            pytest.skip(str(exc))
    (tmp_path / "source.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    monkeypatch.setenv(f"TG_{kind.upper()}_MAX", "1")
    store = session_store if kind == "session" else checkpoint_store
    create = store.open_session if kind == "session" else store.create_checkpoint
    return kind, store, lambda: create(str(tmp_path)), tmp_path


def assert_live(storage, result):
    kind, store, _create, root = storage
    identifier = getattr(result, f"{kind}_id")
    if kind == "session":
        assert store._session_payload_path(root, identifier).is_file(), (
            "returned payload was pruned"
        )
    records = store._load_index(root)
    assert [getattr(record, f"{kind}_id") for record in records] == [identifier]
    if kind == "session":
        assert store.get_session(identifier, str(root))["session_id"] == identifier
    else:
        assert store.load_checkpoint_metadata(identifier, str(root))["checkpoint_id"] == identifier
        assert (store._snapshot_path(root, identifier) / "source.py").read_bytes() == (
            root / "source.py"
        ).read_bytes()


def test_sequential_creation_retains_returned_record(storage):
    _kind, _store, create, _root = storage
    first = create()
    assert_live(storage, first)
    second = create()
    assert first.created_at < second.created_at
    assert_live(storage, second)


def test_older_writer_publishing_last_retains_its_returned_record(storage, monkeypatch):
    _kind, store, create, _root = storage
    ready, release = Event(), Event()
    results, errors = [], []
    real_lock = store.index_lock

    @contextmanager
    def gated_lock(*args, **kwargs):
        if current_thread() is worker:
            ready.set()
            assert release.wait(20), "older writer was not released"
        with real_lock(*args, **kwargs):
            yield

    def older_writer():
        try:
            results.append(create())
        except BaseException as exc:
            errors.append(exc)
            ready.set()

    monkeypatch.setattr(store, "index_lock", gated_lock)
    worker = Thread(target=older_writer, daemon=True)
    worker.start()
    try:
        assert ready.wait(20), "older writer did not reach index publication"
        if errors:
            raise errors[0]
        newer = create()
        assert_live(storage, newer)
    finally:
        release.set()
        worker.join(20)
    assert not worker.is_alive(), "older writer did not finish"
    if errors:
        raise errors[0]
    older = results[0]
    assert older.created_at < newer.created_at
    assert_live(storage, older)
