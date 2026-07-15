"""Disk-usage budget regression tests for checkpoint creation (audit H4).

``create_checkpoint()`` copied the WHOLE scope into a new snapshot dir on every call with
no file-count cap, no per-file size cap, no running-byte budget, and no free-space check.
The only existing control (``TG_CHECKPOINT_MAX``) bounds RETAINED checkpoint COUNT, and
pruning runs AFTER the full copy, so peak transient disk usage was unbounded per call --
reachable via ``tg checkpoint create``, the MCP ``tg_checkpoint_create`` tool, and
``tg_rewrite_apply(checkpoint=true)``.

These tests import only ``checkpoint_store`` (no ``cli.main`` / rust_core), so they run
standalone and in CI without any compiled extension present.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store
from tensor_grep.cli.checkpoint_store import CheckpointBudgetExceededError


def _storage_dir_entries(root: Path) -> list[Path]:
    storage_dir = checkpoint_store._checkpoint_storage_dir(root)
    if not storage_dir.exists():
        return []
    return list(storage_dir.iterdir())


def test_create_checkpoint_succeeds_under_default_budget(tmp_path: Path) -> None:
    """Baseline: an ordinary small checkpoint is unaffected by the new default budgets."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("small file\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(root))

    assert created.file_count == 1
    snapshot = checkpoint_store._snapshot_path(root, created.checkpoint_id)
    assert (snapshot / "a.py").read_text(encoding="utf-8") == "small file\n"


def test_create_checkpoint_refuses_file_over_per_file_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single file over the configured per-file cap must refuse the whole checkpoint,
    and no half-created checkpoint directory may be left behind."""
    monkeypatch.setenv("TG_CHECKPOINT_MAX_FILE_BYTES", "1024")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.bin").write_bytes(b"x" * 4096)  # 4 KiB, over the 1 KiB cap

    with pytest.raises(CheckpointBudgetExceededError):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], "a checkpoint dir was left behind on refusal"


def test_create_checkpoint_refuses_when_total_bytes_exceed_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many small files whose SUM exceeds the total-per-checkpoint cap must also refuse,
    even though no single file trips the per-file cap."""
    monkeypatch.setenv("TG_CHECKPOINT_MAX_FILE_BYTES", "1000000")  # generous per-file
    monkeypatch.setenv("TG_CHECKPOINT_MAX_TOTAL_BYTES", "2048")  # tiny total cap
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(5):
        (root / f"f{i}.bin").write_bytes(b"y" * 1000)  # 5 x 1000 = 5000 > 2048 total

    with pytest.raises(CheckpointBudgetExceededError):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], "a checkpoint dir was left behind on refusal"


def test_create_checkpoint_refuses_when_free_space_margin_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination filesystem reporting too little free space must refuse the checkpoint
    even when the per-file/total caps alone would allow it."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "f.bin").write_bytes(b"z" * 1000)

    real_usage = shutil.disk_usage(tmp_path)
    fake_usage = real_usage._replace(free=500)  # far below any sane margin
    monkeypatch.setattr(checkpoint_store.shutil, "disk_usage", lambda _path: fake_usage)

    with pytest.raises(CheckpointBudgetExceededError):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], "a checkpoint dir was left behind on refusal"


def test_create_checkpoint_cleans_up_snapshot_dir_on_mid_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure DURING the copy loop (after the pre-flight budget check passed) must not
    leave a half-copied snapshot directory behind -- e.g. a file grows or a permission
    error appears between the pre-flight stat and the actual copy."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("a\n", encoding="utf-8")
    (root / "b.py").write_text("b\n", encoding="utf-8")

    original_copy2 = checkpoint_store.shutil.copy2
    calls = {"n": 0}

    def _boom_on_second_copy(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated mid-copy failure")
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(checkpoint_store.shutil, "copy2", _boom_on_second_copy)

    with pytest.raises(OSError):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], "a half-copied checkpoint dir was left behind"


def test_create_checkpoint_cleans_up_snapshot_dir_on_mid_copy_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit #125a: KeyboardInterrupt/SystemExit are BaseException, not Exception -- a Ctrl+C
    mid-copy must trigger the same half-copied-dir cleanup as an ordinary OSError (see
    test_create_checkpoint_cleans_up_snapshot_dir_on_mid_copy_failure above), and the interrupt
    must still propagate to the caller afterward, not be swallowed by the cleanup handler."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("a\n", encoding="utf-8")
    (root / "b.py").write_text("b\n", encoding="utf-8")

    original_copy2 = checkpoint_store.shutil.copy2
    calls = {"n": 0}

    def _interrupt_on_second_copy(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(checkpoint_store.shutil, "copy2", _interrupt_on_second_copy)

    with pytest.raises(KeyboardInterrupt):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], (
        "a half-copied checkpoint dir was left behind after a KeyboardInterrupt"
    )


def test_create_checkpoint_cleans_up_snapshot_dir_on_metadata_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit #125a: the cleanup try/except must extend through the metadata-write step, not
    stop at the end of the copy loop -- a failure while writing metadata.json (after the copy
    has fully succeeded) must not orphan the now fully-copied-but-unindexed per-checkpoint
    directory."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("a\n", encoding="utf-8")

    def _boom_metadata_write(*args, **kwargs):
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(checkpoint_store, "_write_checkpoint_metadata", _boom_metadata_write)

    with pytest.raises(OSError):
        checkpoint_store.create_checkpoint(str(root))

    assert _storage_dir_entries(root) == [], (
        "a fully-copied checkpoint dir was left behind after a metadata-write failure"
    )


def test_create_checkpoint_respects_raised_env_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caps are env-configurable: a repo with legitimately large tracked assets can
    raise the limit instead of being permanently blocked."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.bin").write_bytes(b"x" * 4096)

    monkeypatch.setenv("TG_CHECKPOINT_MAX_FILE_BYTES", "1024")
    with pytest.raises(CheckpointBudgetExceededError):
        checkpoint_store.create_checkpoint(str(root))

    monkeypatch.setenv("TG_CHECKPOINT_MAX_FILE_BYTES", "8192")  # raise the cap
    created = checkpoint_store.create_checkpoint(str(root))
    assert created.file_count == 1


def test_create_checkpoint_undo_roundtrip_still_works_after_budget_fix(tmp_path: Path) -> None:
    """Baseline: a normal create -> modify -> undo roundtrip is unaffected by the budget
    pre-flight (default budgets are generous relative to test-sized fixtures)."""
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "pkg" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(root))
    target.write_text("MUTATED\n", encoding="utf-8")

    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
    assert target.read_text(encoding="utf-8") == "original\n"
