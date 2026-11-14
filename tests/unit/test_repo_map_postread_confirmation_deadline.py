"""A completed source read may not admit new confirmation parsing after deadline expiry."""

import inspect
import json

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.parametrize("expire_after_read", [False, True])
def test_public_confirmation_rechecks_deadline_after_read_before_ast_parse(
    tmp_path, monkeypatch, record_property, expire_after_read
):
    target = tmp_path / "origin.py"
    test_file = tmp_path / "tests/test_other.py"
    test_file.parent.mkdir()
    target.write_text("def unique_target():\n    return 1\n", encoding="utf-8")
    test_source = (
        "from origin import unique_target\ndef test_other():\n    assert unique_target()\n"
    )
    test_file.write_text(test_source, encoding="utf-8")
    repo = rm.build_repo_map(tmp_path)
    clock = {"now": 100.0}
    deadline = 150.0
    read_completions, cached_invocations, actual_ast_parses = [], [], []
    real_read = rm.lang_registry.read_source_text
    real_cached_parse = rm._cached_ast_parse
    real_ast_parse = rm.ast.parse

    def candidate_stack():
        return [frame.function for frame in inspect.stack(context=0)[:12]]

    def reader(path):
        started_at = clock["now"]
        source = real_read(path)
        if str(path) == str(test_file) and "_candidate_evidence" in candidate_stack():
            # Force a miss only in this isolated confirmation AST cache, proving actual work.
            real_cached_parse.cache_clear()
            if expire_after_read:
                clock["now"] = 200.0
            read_completions.append({"started": started_at, "completed": clock["now"]})
        return source

    def cached_parse(source):
        if source == test_source and "_candidate_evidence" in candidate_stack():
            cached_invocations.append(clock["now"])
        return real_cached_parse(source)

    def ast_parse(source, *args, **kwargs):
        functions = candidate_stack()
        if source == test_source and "_candidate_evidence" in functions:
            actual_ast_parses.append({"now": clock["now"], "functions": functions})
        return real_ast_parse(source, *args, **kwargs)

    monkeypatch.setattr(rm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(rm.lang_registry, "read_source_text", reader)
    monkeypatch.setattr(rm, "_cached_ast_parse", cached_parse)
    monkeypatch.setattr(rm.ast, "parse", ast_parse)
    payload = rm.build_context_pack_from_map(repo, "unique_target", deadline_monotonic=deadline)
    assert read_completions, "actual public confirmation read-completion seam was not exercised"
    assert all(read["started"] < deadline for read in read_completions)
    record_property("read_completions", json.dumps(read_completions))
    record_property("cached_parser_invocations", json.dumps(cached_invocations))
    record_property("actual_ast_parses", json.dumps(actual_ast_parses))
    if expire_after_read:
        assert payload["partial"] is True
        assert payload["deadline_limit"]["deadline_exceeded"] is True
        assert not any(parse["now"] >= deadline for parse in actual_ast_parses), (
            "new AST parsing began after an in-budget read completed beyond the deadline"
        )
        assert not any(now >= deadline for now in cached_invocations)
    else:
        assert actual_ast_parses, "live-budget control did not invoke the actual AST parser"
        assert cached_invocations
        assert all(parse["now"] < deadline for parse in actual_ast_parses)
        assert payload.get("partial") is not True
        assert any(row["path"] == str(test_file) for row in payload["test_matches"])
