"""F-04: `checkpoint undo` must not delete empty directories it never touched."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import checkpoint_store


def test_undo_keeps_untouched_empty_dirs_and_prunes_the_ones_it_emptied(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "keep_empty").mkdir(parents=True)
    (project / "logs" / "2026").mkdir(parents=True)
    (project / "a.txt").write_text("a\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(project))

    # Created AFTER the checkpoint: undo removes the file, so its new parent dirs become empty.
    (project / "new" / "sub").mkdir(parents=True)
    (project / "new" / "sub" / "b.txt").write_text("b\n", encoding="utf-8")

    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(project))

    assert not (project / "new" / "sub" / "b.txt").exists()
    # POSITIVE CONTROL: dirs undo itself emptied are still pruned (the sweep still works).
    assert not (project / "new").exists()
    # The finding: dirs that existed before and were never touched must survive.
    assert (project / "keep_empty").is_dir()
    assert (project / "logs" / "2026").is_dir()
