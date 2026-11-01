"""Undo errors identify the failed operation rather than inventing a missing record."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import checkpoint_store
from tensor_grep.cli.main import app


@pytest.fixture
def project(tmp_path, monkeypatch):
    # Confine discovery caches as well as checkpoint files to the disposable fixture.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "project"
    root.mkdir()
    (root / "sample.txt").write_text("before", encoding="utf-8")
    return root


def _undo(root, checkpoint_id):
    result = CliRunner().invoke(app, ["checkpoint", "undo", checkpoint_id, str(root), "--json"])
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("failure_type", [OSError, FileNotFoundError])
@pytest.mark.parametrize("stage", ["metadata", "restore"])
def test_existing_checkpoint_io_failure_is_undo_failed(project, monkeypatch, failure_type, stage):
    created = checkpoint_store.create_checkpoint(str(project))
    target = project / "sample.txt"
    target.write_text("after", encoding="utf-8")
    metadata = checkpoint_store._metadata_path(project, created.checkpoint_id)
    calls = []
    failure = failure_type("owned undo I/O failure")
    if stage == "metadata":
        read_text = Path.read_text

        def fail_read(path, *args, **kwargs):
            if path == metadata:
                calls.append(path)
                raise failure
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_read)
    else:
        copy2 = checkpoint_store.shutil.copy2

        def fail_restore(source, destination, **kwargs):
            if Path(destination) == target:
                calls.append(target)
                raise failure
            return copy2(source, destination, **kwargs)

        monkeypatch.setattr(checkpoint_store.shutil, "copy2", fail_restore)

    result, payload = _undo(project, created.checkpoint_id)

    assert calls == [metadata if stage == "metadata" else target]
    assert result.exit_code == 1
    assert payload == {
        "version": 1,
        "schema_version": 1,
        "ok": False,
        "error": "undo_failed",
        "detail": str(failure),
        "checkpoint_id": created.checkpoint_id,
        "path": str(project),
    }
    assert metadata.exists()
    assert target.read_text(encoding="utf-8") == "after"


def test_missing_record_retains_not_found_and_library_compatibility(project):
    for operation in (checkpoint_store.undo_checkpoint, checkpoint_store.load_checkpoint_metadata):
        with pytest.raises(FileNotFoundError) as caught:
            operation("ckpt-missing", str(project))
        assert type(caught.value).__name__ == "CheckpointNotFoundError"
    result, payload = _undo(project, "ckpt-missing")
    assert result.exit_code == 1
    assert payload["error"] == "checkpoint_not_found"
    with pytest.raises(FileNotFoundError) as caught:
        checkpoint_store.resolve_latest_checkpoint(str(project))
    assert type(caught.value).__name__ == "CheckpointNotFoundError"
    result = CliRunner().invoke(app, ["checkpoint", "undo", "--last", str(project), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "checkpoint_not_found"


def test_missing_snapshot_retains_corrupt_error(project):
    created = checkpoint_store.create_checkpoint(str(project))
    (checkpoint_store._snapshot_path(project, created.checkpoint_id) / "sample.txt").unlink()
    result, payload = _undo(project, created.checkpoint_id)
    assert result.exit_code == 1
    assert payload["error"] == "checkpoint_corrupt"
    assert (project / "sample.txt").read_text(encoding="utf-8") == "before"


def test_unsafe_undo_retains_unsafe_error(project, monkeypatch):
    created = checkpoint_store.create_checkpoint(str(project))
    extra = project / "extra.txt"
    extra.write_text("keep", encoding="utf-8")
    read_bytes = Path.read_bytes

    def fail_read(path):
        if path == extra:
            raise OSError("owned unreadable extra")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    result, payload = _undo(project, created.checkpoint_id)
    assert result.exit_code == 1
    assert payload["error"] == "undo_unsafe"
    assert extra.read_text(encoding="utf-8") == "keep"


def test_successful_undo_payload_and_restoration_unchanged(project):
    created = checkpoint_store.create_checkpoint(str(project))
    (project / "sample.txt").write_text("after", encoding="utf-8")
    result, payload = _undo(project, created.checkpoint_id)
    assert result.exit_code == 0
    assert payload["schema_version"] == 1
    assert payload["checkpoint_id"] == created.checkpoint_id
    assert payload["restored_files"] == 1
    assert payload["removed_paths"] == 0
    assert "error" not in payload
    assert (project / "sample.txt").read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("missing", [False, True])
def test_mcp_undo_retains_sanitized_error_envelope(project, monkeypatch, missing):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(project))
    if missing:
        checkpoint_id = "ckpt-missing"
    else:
        checkpoint_id = checkpoint_store.create_checkpoint(str(project)).checkpoint_id
        metadata = checkpoint_store._metadata_path(project, checkpoint_id)
        metadata.write_text("{", encoding="utf-8")
    payload = json.loads(mcp_server.tg_checkpoint_undo(checkpoint_id, str(project)))
    assert set(payload) == {
        "version",
        "schema_version",
        "mcp_contract_version",
        "incomplete",
        "error",
        "path",
        "checkpoint_id",
    }
    assert payload["checkpoint_id"] == checkpoint_id
    category = "FileNotFoundError" if missing else "JSONDecodeError"
    assert payload["error"] == {
        "code": "internal_error",
        "message": f"tg_checkpoint_undo failed due to an internal error ({category}).",
        "retryable": False,
    }
