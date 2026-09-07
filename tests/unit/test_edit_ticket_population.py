from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.edit_ticket_service import (
    _IGNORED_DEPENDENCY_DIRS,
    _walk_tracked_files_bounded,
    build_edit_ready_ticket,
    verify_edit_ticket,
)


def test_dependency_tree_is_pruned_before_descent(tmp_path: Path) -> None:
    """AGT-04: node_modules/.venv/etc must never be entered, not merely filtered after a full
    walk -- pruning before descent is what keeps a huge vendored tree from being read at all."""
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    dep_dir = tmp_path / "node_modules" / "some-pkg"
    dep_dir.mkdir(parents=True)
    (dep_dir / "index.js").write_text("module.exports = {}\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert "src.py" in files
    assert not any("node_modules" in path for path in files)
    assert population["status"] == "complete"


def test_all_known_dependency_dirs_are_pruned(tmp_path: Path) -> None:
    for name in _IGNORED_DEPENDENCY_DIRS:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "junk.bin").write_bytes(b"x")
    (tmp_path / "kept.py").write_text("1\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert set(files) == {"kept.py"}
    assert population["status"] == "complete"


def test_tracked_dotfile_survives_pruning(tmp_path: Path) -> None:
    """A tracked dotfile (e.g. .gitignore) must still be hashed -- pruning targets dependency
    directory NAMES, not a blanket dot-prefix exclusion."""
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert ".gitignore" in files
    assert ".github/workflows/ci.yml" in files
    assert population["status"] == "complete"


def test_file_count_budget_reports_incomplete_not_false_pass(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("1\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path, max_files=3)

    assert population["status"] == "incomplete"
    assert population["reason"] == "file_count_limit"
    assert population["verified"] is False
    assert len(files) <= 3


def test_aggregate_byte_budget_reports_incomplete(tmp_path: Path) -> None:
    for i in range(4):
        (tmp_path / f"f{i}.bin").write_bytes(b"x" * 1000)

    _files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=1500)

    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"
    assert population["verified"] is False


def test_per_file_byte_budget_skips_oversized_file_without_false_pass(tmp_path: Path) -> None:
    small = tmp_path / "small.py"
    small.write_text("1\n", encoding="utf-8")
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * 10000)

    files, population = _walk_tracked_files_bounded(tmp_path, max_file_bytes=100)

    assert "small.py" in files
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"


def test_verify_edit_ticket_fails_closed_on_incomplete_population(tmp_path: Path) -> None:
    """A ticket built from a truncated population must never let verify_edit_ticket return PASS
    -- an unread file could hide undeclared drift outside the ticket's allowed scope."""
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("b = 1\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
        max_files=1,
    )
    assert ticket.population_status["status"] == "incomplete"

    allowed.write_text("a = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "population_incomplete"


def test_verify_edit_ticket_still_passes_on_complete_population(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )
    assert ticket.population_status["status"] == "complete"

    allowed.write_text("a = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "PASS"


def test_legacy_ticket_without_population_status_still_verifies(tmp_path: Path) -> None:
    """Backward compatibility: a ticket serialized before this change (no population_status key)
    must still round-trip and verify -- this is a fail-open compatibility path documented as
    such, not a silent relaxation of the new fail-closed default for NEW tickets."""
    from tensor_grep.cli.edit_ticket_service import EditReadyTicketV1

    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")

    legacy = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )
    legacy_dict = legacy.to_dict()
    del legacy_dict["population_status"]
    reloaded = EditReadyTicketV1.from_dict(legacy_dict)
    assert reloaded.population_status == {"status": "unknown", "verified": None}

    allowed.write_text("a = 2\n", encoding="utf-8")
    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=reloaded,
        modified_files=["allowed.py"],
    )
    assert result["verdict"] == "PASS"
