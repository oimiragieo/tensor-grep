"""Path-containment regression tests for the checkpoint rollback safety net.

These import ONLY ``checkpoint_store`` (no ``cli.main`` / rust_core), so they run
standalone and in CI. They guard against the S1 audit finding: ``undo_checkpoint``
restored ``metadata["entries"]`` keys by joining them to the checkpoint root with no
containment check, so a tampered metadata file with an absolute or ``..`` entry could
overwrite or delete files anywhere the process can write (arbitrary file write/delete,
reachable from the MCP ``tg_checkpoint_undo`` tool, CLI ``tg checkpoint undo``, and
policy rollback).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store


def _write_metadata(root: Path, checkpoint_id: str, entries: dict[str, bool]) -> None:
    meta_path = checkpoint_store._metadata_path(root, checkpoint_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": checkpoint_store._CHECKPOINT_VERSION,
        "checkpoint_id": checkpoint_id,
        "mode": "filesystem-snapshot",
        "root": str(root),
        "scope": "tree",
        "original_path": str(root),
        "created_at": "2026-01-01T00:00:00+00:00",
        "file_count": len(entries),
        "entries": entries,
        "active": True,
    }
    meta_path.write_text(json.dumps(payload), encoding="utf-8")


def test_undo_restores_in_root_file_roundtrip(tmp_path: Path) -> None:
    """Baseline: a normal create→modify→undo restores the original content."""
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "pkg" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(root))
    target.write_text("MUTATED\n", encoding="utf-8")

    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize(
    "evil_rel",
    [
        "../escape_relative.txt",
        "../../escape_two_levels.txt",
        "pkg/../../escape_via_subdir.txt",
    ],
)
def test_undo_refuses_parent_traversal_entries(tmp_path: Path, evil_rel: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = (root / evil_rel).resolve()
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("DO NOT TOUCH\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-relative"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    # A snapshot blob the attacker would want copied over `outside`.
    (snapshot_dir / "payload").write_text("ATTACKER CONTENT\n", encoding="utf-8")
    _write_metadata(root, checkpoint_id, {evil_rel: True})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    # The out-of-root file must be untouched.
    assert outside.read_text(encoding="utf-8") == "DO NOT TOUCH\n"


def test_undo_refuses_absolute_entries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-absolute"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    # Absolute entry marked as deleted → undo would unlink the victim outright.
    _write_metadata(root, checkpoint_id, {str(victim): False})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "SECRET\n"


@pytest.mark.parametrize(
    "evil_id",
    ["../escape", "../../escape", "sub/../../escape", ".."],
)
def test_checkpoint_dir_refuses_traversal_id(tmp_path: Path, evil_id: str) -> None:
    """Audit HIGH: checkpoint_id itself (not just entry keys) was joined to the store
    with no validation, so an absolute/`..` id escaped the checkpoint store."""
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError):
        checkpoint_store._checkpoint_dir(root, evil_id)


def test_load_metadata_refuses_traversal_to_external_file(tmp_path: Path) -> None:
    """Audit HIGH: an unvalidated checkpoint_id let load_checkpoint_metadata read a
    metadata.json OUTSIDE the checkpoint store (arbitrary metadata disclosure)."""
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external_store"
    external.mkdir()
    (external / "metadata.json").write_text(json.dumps({"secret": "leak"}), encoding="utf-8")

    storage = checkpoint_store._checkpoint_storage_dir(root)
    evil_id = os.path.relpath(external, storage)  # ..(/..)+/external_store
    with pytest.raises(ValueError):
        checkpoint_store.load_checkpoint_metadata(evil_id, str(root))


def test_undo_refuses_traversal_checkpoint_id(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint("../../evil-ckpt", str(root))


def test_create_checkpoint_does_not_disclose_symlink_target(tmp_path: Path) -> None:
    """Audit HIGH: create_checkpoint followed symlinks, copying the CONTENT of a file OUTSIDE
    the checkpoint root into the snapshot (out-of-root disclosure, and it could re-materialize
    into the tree on undo). Symlinks must not be followed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("in-repo content\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-OUT-OF-ROOT\n", encoding="utf-8")
    try:
        (repo / "link.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    created = checkpoint_store.create_checkpoint(str(repo))
    snapshot = checkpoint_store._snapshot_path(repo, created.checkpoint_id)

    # No snapshotted regular file may contain the out-of-root secret content.
    for path in snapshot.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert "SECRET-OUT-OF-ROOT" not in path.read_text(encoding="utf-8", errors="ignore")


def _write_rust_format_metadata(repo: Path, checkpoint_id: str, entries: dict[str, bool]) -> None:
    """Write a checkpoint metadata.json in the RUST create path's exact wire format.

    The native `create_checkpoint` (rust_core/src/main.rs `CheckpointMetadata`) serializes only
    {version, checkpoint_id, mode, root, scope, original_path, created_at, file_count, entries}
    -- it does NOT emit the Python-only ``active`` / ``skipped_nested_repos`` fields that
    ``checkpoint_store._write_checkpoint_metadata`` adds. Writing the leaner Rust shape here makes
    these tests double as a cross-language contract guard: they fail if ``undo_checkpoint`` ever
    starts requiring a Python-only metadata field a Rust-created checkpoint never wrote.
    """
    meta_path = checkpoint_store._metadata_path(repo, checkpoint_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": checkpoint_store._CHECKPOINT_VERSION,
        "checkpoint_id": checkpoint_id,
        "mode": "filesystem-snapshot",
        "root": str(repo),
        "scope": "tree",
        "original_path": str(repo),
        "created_at": "2026-01-01T00:00:00+00:00",
        "file_count": len(entries),
        "entries": entries,
    }
    meta_path.write_text(json.dumps(payload), encoding="utf-8")


def test_rust_created_out_of_root_symlink_checkpoint_fails_closed_on_undo(tmp_path: Path) -> None:
    """audit #178 F1: pin the Python-undo half of the Rust-create -> Python-undo contract for an
    OUT-OF-ROOT symlink.

    The fixed Rust create path (`tg run/rewrite --apply --checkpoint`, main.rs
    `copy_checkpoint_entry`) now stores a tracked out-of-root symlink AS a symlink in the
    snapshot instead of following it and baking the target's bytes in -- proved on the create
    side by rust_core test ``test_create_checkpoint_does_not_disclose_symlink_target``. This test
    reproduces exactly that snapshot state (an out-of-root symlink stored as a symlink, plus a
    metadata.json in Rust's wire format) and pins the OTHER half: ``undo_checkpoint`` must FAIL
    CLOSED -- undo's read-only pre-flight ``_resolve_within_root`` (checkpoint_store.py:124-139,
    called at :1232-1235) resolves the stored snapshot symlink and refuses it (ValueError)
    because its target escapes the snapshot root, BEFORE mutating a single working-tree file. So
    the out-of-root target's content is never materialized into the repo and the working tree is
    left completely intact (fail-closed-but-not-restorable -- the accurate round-trip contract).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    keep = repo / "keep.py"
    keep.write_text("original in-repo content\n", encoding="utf-8")

    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-OUT-OF-ROOT\n", encoding="utf-8")

    checkpoint_id = "ckpt-20260101000000-deadbeef"
    snapshot_dir = checkpoint_store._snapshot_path(repo, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "keep.py").write_text("original in-repo content\n", encoding="utf-8")

    # Mirror the Rust create path: the tracked symlink is stored AS a symlink pointing at its
    # original out-of-root target -- never the target's bytes.
    try:
        (snapshot_dir / "link.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    _write_rust_format_metadata(repo, checkpoint_id, {"keep.py": True, "link.txt": True})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(repo))

    # Fail-closed: the out-of-root target is untouched and its content was NOT written anywhere
    # into the working tree, and the pre-existing in-repo file is unchanged.
    assert secret.read_text(encoding="utf-8") == "SECRET-OUT-OF-ROOT\n"
    assert keep.read_text(encoding="utf-8") == "original in-repo content\n"
    for path in repo.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and checkpoint_store._CHECKPOINT_DIRNAME not in path.parts
        ):
            assert "SECRET-OUT-OF-ROOT" not in path.read_text(encoding="utf-8", errors="ignore")


def test_rust_created_dangling_symlink_checkpoint_fails_closed_on_undo(tmp_path: Path) -> None:
    """audit #178 F1 (companion): a tracked DANGLING symlink (target missing) captured via the
    Rust create path is likewise refused fail-closed on undo -- the stored snapshot symlink
    resolves in-root but its target does not exist, so undo's missing-source probe
    (checkpoint_store.py:1246-1257) raises CheckpointCorruptError before any working-tree file is
    touched. Pins the dangling half of the round-trip comment's fail-closed claim."""
    repo = tmp_path / "repo"
    repo.mkdir()
    keep = repo / "keep.py"
    keep.write_text("original in-repo content\n", encoding="utf-8")

    checkpoint_id = "ckpt-20260101000000-feedface"
    snapshot_dir = checkpoint_store._snapshot_path(repo, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "keep.py").write_text("original in-repo content\n", encoding="utf-8")

    # An in-root RELATIVE symlink whose target does not exist in the snapshot (dangling).
    try:
        (snapshot_dir / "link.txt").symlink_to("missing_sibling")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    _write_rust_format_metadata(repo, checkpoint_id, {"keep.py": True, "link.txt": True})

    with pytest.raises(checkpoint_store.CheckpointCorruptError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(repo))

    # Fail-closed: working tree left intact, no partial restore.
    assert keep.read_text(encoding="utf-8") == "original in-repo content\n"


def test_create_checkpoint_prunes_to_retention_cap(tmp_path: Path, monkeypatch) -> None:
    """Round-4 DoS: the checkpoint store had no retention cap, so every `tg checkpoint create`
    copied the whole scope into a new snapshot dir with unbounded disk growth. Retain only the
    newest TG_CHECKPOINT_MAX; drop older checkpoints (metadata + snapshot) entirely."""
    monkeypatch.setenv("TG_CHECKPOINT_MAX", "3")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "f.py").write_text("x\n", encoding="utf-8")

    ids: list[str] = []
    for i in range(6):
        (root / "f.py").write_text(f"x{i}\n", encoding="utf-8")
        ids.append(checkpoint_store.create_checkpoint(str(root)).checkpoint_id)

    records = checkpoint_store._load_index(root)
    assert len(records) == 3, "index must be bounded to TG_CHECKPOINT_MAX"
    assert {r.checkpoint_id for r in records} == set(ids[-3:]), "keep the 3 newest"

    for dropped in ids[:-3]:
        assert not checkpoint_store._checkpoint_dir(root, dropped).exists()  # snapshot removed
    for kept in ids[-3:]:
        assert checkpoint_store._checkpoint_dir(root, kept).exists()


def test_select_retained_checkpoints_prunes_by_created_at_not_insert_position(
    tmp_path: Path,
) -> None:
    """M8: created_at is stamped BEFORE the caller acquires index_lock, so concurrent
    writers can insert out of created_at order. Retention selection must sort by
    created_at (newest first) before slicing, not trust list position -- otherwise it can
    drop a genuinely NEWER checkpoint (the `checkpoint undo` safety net) and keep an older
    one."""
    root = tmp_path / "repo"
    root.mkdir()

    # Deliberately out-of-order: position 0 is the OLDEST record (as if its writer won the
    # lock-acquisition race despite being stamped first), positions 1/2 are progressively
    # newer -- mirroring a concurrent-insert race where lock-arrival order != created_at
    # order.
    records = [
        checkpoint_store.CheckpointRecord(
            version=checkpoint_store._CHECKPOINT_VERSION,
            checkpoint_id="oldest",
            mode="filesystem-snapshot",
            root=str(root),
            created_at="2026-01-01T00:00:00+00:00",
            file_count=1,
        ),
        checkpoint_store.CheckpointRecord(
            version=checkpoint_store._CHECKPOINT_VERSION,
            checkpoint_id="newest",
            mode="filesystem-snapshot",
            root=str(root),
            created_at="2026-01-03T00:00:00+00:00",
            file_count=1,
        ),
        checkpoint_store.CheckpointRecord(
            version=checkpoint_store._CHECKPOINT_VERSION,
            checkpoint_id="middle",
            mode="filesystem-snapshot",
            root=str(root),
            created_at="2026-01-02T00:00:00+00:00",
            file_count=1,
        ),
    ]

    retained, _dirs_to_delete = checkpoint_store._select_retained_checkpoints(
        root, records, max_records=2
    )

    retained_ids = {record.checkpoint_id for record in retained}
    # Position-based (buggy) pruning would keep {"oldest", "newest"} (records[:2]).
    # created_at-based (fixed) pruning must keep the two NEWEST: {"newest", "middle"}.
    assert retained_ids == {"newest", "middle"}
    assert "oldest" not in retained_ids


# ---------------------------------------------------------------------------
# H3: snapshot SOURCE containment (symlinked/junctioned ancestor directory)
# ---------------------------------------------------------------------------
#
# S1 (above) confines the undo TARGET (`root / rel_path`) via `_resolve_within_root`.
# The snapshot SOURCE composition (`snapshot_dir / rel_path`) had no equivalent guard:
# `shutil.copy2(source, ..., follow_symlinks=False)` only refuses a symlink at the FINAL
# path component. A snapshot tree whose ANCESTOR directory is a symlink (or, on Windows, a
# directory junction) pointing outside the snapshot is transparently traversed by the OS --
# `tg checkpoint undo` then reads host-file content THROUGH the link and copies it into an
# otherwise validly-confined working-tree target: arbitrary-file-read-into-working-tree. A
# malicious repo can ship a pre-crafted `.tensor-grep/checkpoints/<id>/` (metadata.json +
# a snapshot tree with a symlinked ancestor) that a victim's `tg checkpoint undo <id>`
# then reads through.


def test_undo_refuses_source_with_symlinked_ancestor_directory(tmp_path: Path) -> None:
    """A symlinked ANCESTOR dir inside the snapshot tree must be refused, not traversed."""
    root = tmp_path / "repo"
    root.mkdir()

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\nSECRET\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-ancestor-symlink"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    evil_ancestor = snapshot_dir / "subdir"
    try:
        evil_ancestor.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    # `subdir/id_rsa` is itself a perfectly ordinary, non-traversal, in-root TARGET path --
    # S1's target containment alone has nothing to object to here.
    _write_metadata(root, checkpoint_id, {"subdir/id_rsa": True})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    # The host secret must never have been copied into the working tree.
    leaked_target = root / "subdir" / "id_rsa"
    assert not leaked_target.exists(), "host file content was copied into the working tree"


def test_undo_refuses_source_with_junctioned_ancestor_directory(tmp_path: Path) -> None:
    """Windows variant: a directory JUNCTION as the snapshot ancestor.

    Junctions are the more realistic Windows attack surface than symlinks: creating one
    needs no elevated privilege / Developer Mode (unlike `CreateSymbolicLink`), so this
    works for any local attacker on any Windows box. `Path.resolve()` follows a junction
    exactly like a symlink (both are NTFS reparse points resolved the same way by
    `GetFinalPathNameByHandle`), but `Path.is_symlink()` returns False for a junction -- so
    any guard that only checked `is_symlink()` on an ancestor would silently miss this.
    Proves the fix (`_resolve_within_root` via `Path.resolve()`) catches both mechanisms.
    """
    if os.name != "nt":
        pytest.skip("junctions are a Windows-only reparse-point mechanism")

    root = tmp_path / "repo"
    root.mkdir()

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "id_rsa").write_text("SECRET-VIA-JUNCTION\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-junction"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    evil_ancestor = snapshot_dir / "subdir"

    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(evil_ancestor), str(outside)],
        check=True,
        capture_output=True,
    )
    assert not evil_ancestor.is_symlink(), "sanity: a junction must NOT read as is_symlink()"

    _write_metadata(root, checkpoint_id, {"subdir/id_rsa": True})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    leaked_target = root / "subdir" / "id_rsa"
    assert not leaked_target.exists(), (
        "host file content was copied into the working tree via a junction"
    )


def test_undo_refuses_source_escape_without_touching_other_valid_entries(tmp_path: Path) -> None:
    """A malicious entry must abort the WHOLE undo before mutating any file -- including
    other, perfectly legitimate entries in the same checkpoint (matches the H2 all-or-nothing
    pre-flight contract: undo must not partially apply)."""
    root = tmp_path / "repo"
    root.mkdir()
    legit_target = root / "legit.py"
    legit_target.write_text("ORIGINAL\n", encoding="utf-8")

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET\n", encoding="utf-8")

    checkpoint_id = "ckpt-mixed-legit-and-evil"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "legit.py").write_text("SNAPSHOTTED\n", encoding="utf-8")
    evil_ancestor = snapshot_dir / "subdir"
    try:
        evil_ancestor.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    _write_metadata(
        root,
        checkpoint_id,
        {"legit.py": True, "subdir/secret.txt": True},
    )

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    # The legit file must be untouched -- the whole undo aborted pre-flight.
    assert legit_target.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert not (root / "subdir" / "secret.txt").exists()


def test_undo_still_restores_normal_nested_snapshot_after_source_containment_fix(
    tmp_path: Path,
) -> None:
    """Baseline: an ordinary multi-level nested checkpoint (no symlinks anywhere) must keep
    round-tripping after source containment is enforced -- the fix must not be over-strict."""
    root = tmp_path / "repo"
    root.mkdir()
    nested = root / "a" / "b" / "c" / "deep.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("deep-original\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(root))
    nested.write_text("deep-MUTATED\n", encoding="utf-8")

    result = checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
    assert result.restored_files == 1
    assert nested.read_text(encoding="utf-8") == "deep-original\n"
