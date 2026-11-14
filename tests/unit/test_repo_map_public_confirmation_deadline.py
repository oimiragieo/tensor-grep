"""New confirmation parsing must honor the public context request's current deadline."""

import inspect
import json

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.parametrize("expire_at_test_score", [False, True])
def test_public_context_admits_confirmation_reads_only_with_live_budget(
    tmp_path, monkeypatch, record_property, expire_at_test_score
):
    target = tmp_path / "origin.py"
    test_file = tmp_path / "tests/test_other.py"
    test_file.parent.mkdir()
    target.write_text("def unique_target():\n    return 1\n", encoding="utf-8")
    test_file.write_text(
        "from origin import unique_target\ndef test_other():\n    assert unique_target()\n",
        encoding="utf-8",
    )
    repo = rm.build_repo_map(tmp_path)
    clock = {"now": 100.0}
    deadline = 150.0
    scored, confirmation_reads, other_postdeadline_reads = [], [], []
    original_score = rm._score_file_path
    original_read = rm.lang_registry.read_source_text

    def score(path, terms):
        result = original_score(path, terms)
        if str(path) == str(test_file) and any(
            frame.function == "_context_tests" for frame in inspect.stack(context=0)
        ):
            scored.append(str(path))
            if expire_at_test_score:
                clock["now"] = 200.0
        return result

    def reader(path):
        functions = [frame.function for frame in inspect.stack(context=0)[:10]]
        if str(path) == str(test_file) and "_candidate_evidence" in functions:
            confirmation_reads.append({"now": clock["now"], "path": str(path)})
        elif clock["now"] >= deadline:
            other_postdeadline_reads.append({"path": str(path), "functions": functions})
        return original_read(path)

    monkeypatch.setattr(rm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rm, "_score_file_path", score)
    monkeypatch.setattr(rm.lang_registry, "read_source_text", reader)
    payload = rm.build_context_pack_from_map(repo, "unique_target", deadline_monotonic=deadline)
    assert scored, "actual public _context_tests score seam was not exercised"
    record_property("confirmation_reads", json.dumps(confirmation_reads))
    record_property("other_postdeadline_reads_not_asserted", json.dumps(other_postdeadline_reads))
    if expire_at_test_score:
        assert payload["partial"] is True
        assert payload["deadline_limit"]["deadline_exceeded"] is True
        assert not any(read["now"] >= deadline for read in confirmation_reads), (
            "new test-import confirmation parse began after the original public deadline"
        )
    else:
        assert confirmation_reads, "live confirmation-read positive control was not exercised"
        assert all(read["now"] < deadline for read in confirmation_reads)
        assert payload.get("partial") is not True
        assert any(row["path"] == str(test_file) for row in payload["test_matches"])
