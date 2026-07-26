"""#308: `undo_checkpoint` must say which post-checkpoint edits it discarded.

`checkpoint_store` stores no content hashes at all -- metadata is `{rel_path: exists_bool}` --
and `CheckpointUndoResult` carried only `restored_files` / `removed_paths` counts. So undo
reverted a file another agent had edited since the checkpoint and reported the same plain success
as a no-op revert. Same silent-loss family as #297, which fixed the destroys-uncaptured-content
half; this is the disclosure half.

Discarding post-checkpoint work is undo's JOB, so this is disclosure and never a refusal. What
was missing is the answer to "what did I just lose?".

The CONTROL arms carry the weight here. A field that is always populated is indistinguishable
from one that works, and a field that is never populated is indistinguishable from a detector
wired to the wrong timestamp -- so both arms are asserted, and `test_divergence_is_sampled_before
_mutation` pins the ordering bug that would make the field silently always-empty.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from tensor_grep.cli import checkpoint_store


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("original a\n", encoding="utf-8")
    (root / "b.py").write_text("original b\n", encoding="utf-8")
    return root


def _touch_after_checkpoint(path: Path, text: str) -> None:
    """Rewrite a file with an mtime unambiguously after the checkpoint.

    The explicit `os.utime` is the premise, not decoration: checkpoint creation and the edit can
    land inside the same filesystem timestamp granularity (Windows FAT/NTFS and some CI
    filesystems are coarse), and then the detector correctly sees no divergence and the test
    would fail for a reason that has nothing to do with the code under test.
    """
    path.write_text(text, encoding="utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))


def test_undo_names_the_files_whose_post_checkpoint_edits_it_discarded(tmp_path: Path) -> None:
    root = _project(tmp_path)
    created = checkpoint_store.create_checkpoint(str(root))

    _touch_after_checkpoint(root / "a.py", "edited by another agent\n")

    result = checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert "a.py" in result.diverged_paths, (
        "undo reverted a file edited after the checkpoint and did not say so -- the exact "
        f"silent loss #308 exists to close (got {result.diverged_paths!r})"
    )
    # b.py was untouched, so it must NOT be named: a detector that lists every file is noise
    # rather than signal, and would make the assertion above pass for the wrong reason.
    assert "b.py" not in result.diverged_paths
    # The revert itself must still happen -- this is disclosure, never a refusal.
    assert (root / "a.py").read_text(encoding="utf-8") == "original a\n"


def test_an_undo_with_no_post_checkpoint_edits_names_nothing() -> None:
    """CONTROL. Without it, a detector that returns every path satisfies the test above."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp))
        created = checkpoint_store.create_checkpoint(str(root))
        result = checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
        assert result.diverged_paths == [], (
            "a checkpoint undone with no intervening edits must report no discarded work; "
            f"got {result.diverged_paths!r}"
        )


def test_divergence_is_sampled_before_mutation(tmp_path: Path) -> None:
    """The ordering bug that would make this field permanently, silently empty.

    Undo rewrites the very files it is reporting on. Sampling mtimes AFTER the commit phase would
    read timestamps undo itself had just written, every one of them newer than the checkpoint --
    or, depending on how the copy preserves metadata, none of them. Either way the field would
    stop tracking reality while still looking implemented.

    Asserted through observable behaviour: a file edited after the checkpoint is still named even
    though undo has by then overwritten it and reset its mtime via `copy2`.
    """
    root = _project(tmp_path)
    created = checkpoint_store.create_checkpoint(str(root))
    _touch_after_checkpoint(root / "a.py", "edited\n")

    result = checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert result.diverged_paths == ["a.py"]
    # Premise: undo really did overwrite the file, so the pre-flight sample was the only chance
    # to observe the divergence.
    assert (root / "a.py").read_text(encoding="utf-8") == "original a\n"


def test_an_unparseable_created_at_yields_no_claim_rather_than_a_false_one(
    tmp_path: Path,
) -> None:
    """Fails OPEN, narrowly and on purpose.

    This field gates no behaviour -- it only adds disclosure -- so a missing entry costs the
    caller information while a fabricated entry would tell them a file was clobbered when it was
    not. That is the opposite of the fail-CLOSED rule for completeness fields, and the asymmetry
    is why: there, silence is the dangerous answer; here, a false accusation is.
    """
    targets = {"x.py": tmp_path / "x.py"}
    (tmp_path / "x.py").write_text("hi\n", encoding="utf-8")

    assert checkpoint_store._paths_modified_since_checkpoint("", targets) == []
    assert checkpoint_store._paths_modified_since_checkpoint("not-a-timestamp", targets) == []

    # CONTROL: the same helper with a REAL, old timestamp must still report the file, or the two
    # assertions above would pass simply because the helper never returns anything.
    assert checkpoint_store._paths_modified_since_checkpoint(
        "2000-01-01T00:00:00+00:00", targets
    ) == ["x.py"]
