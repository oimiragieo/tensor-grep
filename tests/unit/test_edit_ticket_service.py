from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.edit_ticket_service import (
    EditReadyTicketV1,
    build_edit_ready_ticket,
    compute_working_tree_fingerprint,
    verify_edit_ticket,
)


def test_compute_working_tree_fingerprint_deterministic(tmp_path: Path) -> None:
    f1 = tmp_path / "foo.py"
    f1.write_text("print('hello')\n", encoding="utf-8")
    fp1 = compute_working_tree_fingerprint(str(tmp_path))
    fp2 = compute_working_tree_fingerprint(str(tmp_path))
    assert fp1 == fp2
    assert len(fp1) == 64

    f1.write_text("print('hello world')\n", encoding="utf-8")
    fp3 = compute_working_tree_fingerprint(str(tmp_path))
    assert fp3 != fp1


def test_build_edit_ready_ticket_contract(tmp_path: Path) -> None:
    src_file = tmp_path / "module.py"
    src_file.write_text("def hello():\n    return 42\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(src_file),
        query="return 42",
        allowed_files=["module.py"],
    )

    assert isinstance(ticket, EditReadyTicketV1)
    assert ticket.ticket_id.startswith("ticket_")
    assert ticket.version == 1
    assert "module.py" in ticket.allowed_files
    assert ticket.working_tree_fingerprint != ""
    assert isinstance(ticket.pre_edit_fingerprints, dict)
    assert "module.py" in ticket.pre_edit_fingerprints

    d = ticket.to_dict()
    assert d["version"] == 1
    assert d["ticket_id"] == ticket.ticket_id
    ticket2 = EditReadyTicketV1.from_dict(d)
    assert ticket2.ticket_id == ticket.ticket_id


def test_verify_edit_contract_violation_on_unallowed_file(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )

    unallowed = tmp_path / "other.py"
    unallowed.write_text("b = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py", "other.py"],
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "edit_contract_violated"
    assert "other.py" in result["violations"]


def test_verify_edit_fails_on_undeclared_out_of_scope_modification(tmp_path: Path) -> None:
    """The whole point of a fingerprinted ticket: catch a file that changed on disk but was
    never DECLARED in modified_files -- e.g. an agent that silently touched a file outside its
    ticket's scope and only reported the files it wants credit for. A verifier that trusts the
    caller-supplied modified_files list alone can be defeated by simply omitting the
    out-of-scope file from that list."""
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    other = tmp_path / "other.py"
    other.write_text("b = 1\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )

    # Agent edits allowed.py (declared) AND silently edits other.py (undeclared, out of scope).
    allowed.write_text("a = 2\n", encoding="utf-8")
    other.write_text("b = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],  # other.py NOT declared -- this is the attack
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "edit_contract_violated"
    assert "other.py" in result["violations"]


def test_verify_edit_fails_when_declared_file_was_not_actually_modified(tmp_path: Path) -> None:
    """A hallucinated edit: the agent CLAIMS to have modified allowed.py but the file's
    fingerprint is unchanged from the ticket's pre-edit snapshot."""
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )

    # allowed.py is NOT actually touched, but the agent claims it modified it.
    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "declared_edit_not_applied"
    assert "allowed.py" in result["violations"]


def test_verify_edit_catches_undeclared_change_to_a_dotfile(tmp_path: Path) -> None:
    """Codex Sol delta-verification audit 2026-09-06 CRITICAL finding: _walk_tracked_files
    excluded every path with ANY dot-prefixed component, not just VCS/cache internals -- so a
    legitimate tracked dotfile (.github/workflows/ci.yml, .gitignore) could be silently modified
    outside a ticket's allowed_files and escape both the pre-edit snapshot and the verify-time
    re-walk entirely. Only .git (the VCS internals directory) and __pycache__ should be excluded;
    ordinary tracked dotfiles must be fingerprinted like any other file."""
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_file = workflow_dir / "ci.yml"
    workflow_file.write_text("name: CI\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )

    # Agent edits allowed.py (declared) AND silently tampers with a CI workflow (undeclared,
    # security-sensitive, and NOT in allowed_files).
    allowed.write_text("a = 2\n", encoding="utf-8")
    workflow_file.write_text(
        "name: CI\non: [push]\njobs: {pwn: {runs-on: self-hosted}}\n", encoding="utf-8"
    )

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "FAIL"
    assert ".github/workflows/ci.yml" in result["violations"]


def test_verify_edit_pass_when_matching_allowed_files_and_fingerprint(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )

    allowed.write_text("a = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "PASS"
    assert result["reason"] is None
