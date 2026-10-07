"""Focused output is an explicit excerpt with faithful source coordinates."""

import pytest

from tensor_grep.cli.repo_map import _render_source_block


@pytest.mark.parametrize(
    ("extension", "block"),
    [
        (
            "js",
            'function calculate(total) {\n  const setup = 1;\n  if (total < 0) {\n    throw new Error("negative");\n  }\n  return total;\n}\n',
        ),
        (
            "ts",
            'function calculate(total: number) {\n  const setup = 1;\n  if (total < 0) {\n    throw new Error("negative");\n  }\n  return total;\n}\n',
        ),
        (
            "rs",
            'fn calculate(total: i32) -> i32 {\n  let setup = 1;\n  if total < 0 {\n    panic!("negative");\n  }\n  total\n}\n',
        ),
        (
            "go",
            'package example\nfunc calculate(total int) int {\n  setup := 1\n  if total < 0 {\n    panic("negative")\n  }\n  return total\n}\n',
        ),
    ],
)
def test_focused_polyglot_blocks(tmp_path, extension, block):
    line = next(line for line in block.splitlines(keepends=True) if "setup" in line)
    block = block.replace(line, "".join(line.replace("setup", f"setup_{i}") for i in range(15)))
    source = {
        "file": str(tmp_path / f"sample.{extension}"),
        "name": "calculate",
        "source": block,
        "start_line": 1,
        "end_line": len(block.splitlines()),
    }
    result = _render_source_block(
        source, render_profile="focused", optimize_context=False, focus_query="negative"
    )
    assert result["focus"]["fallback_reason"] is None
    assert "setup" not in result["rendered_source"]
    assert "if " in result["rendered_source"]
    assert result["rendered_source"].endswith("}\n")


def test_focused_unavailable_grammar_is_explicit_full_fallback(tmp_path, monkeypatch):
    from tensor_grep.cli import focused_rendering

    monkeypatch.setattr(focused_rendering, "_structural_parser_for_path", lambda _: None)
    block = "def calculate():\n    return 42\n"
    result = _render_source_block(
        {"file": str(tmp_path / "sample.py"), "source": block, "start_line": 1, "end_line": 2},
        render_profile="focused",
        optimize_context=True,
        focus_query="return",
    )
    assert result["rendered_source"] == block
    assert result["focus"]["fallback_reason"] == "grammar_unavailable"


def test_focused_context_reports_primary_elision_without_reordering(tmp_path):
    from tensor_grep.cli.repo_map import build_context_render

    block = 'def calculate(total):\n    """Invoice validation."""\n'
    block += "".join(f"    setup_{i} = {i}\n" for i in range(15))
    block += '    if total < 0:\n        raise ValueError("negative")\n    return total\n'
    (tmp_path / "invoice.py").write_text(block, encoding="utf-8")
    full = build_context_render("calculate negative", str(tmp_path), render_profile="full")
    focused = build_context_render("calculate negative", str(tmp_path), render_profile="focused")
    assert full["files"] == focused["files"]
    assert full["symbols"] == focused["symbols"]
    assert (
        full["sources"][0]["source"] == block.rstrip("\n") or full["sources"][0]["source"] == block
    )
    assert "setup_0" not in focused["sources"][0]["source"]
    assert focused["context_consistency"]["primary_symbol_truncated"]
    assert focused["context_consistency"]["confidence_downgraded"]
    assert (
        focused["context_consistency"]["omitted_primary_reason"]
        == "primary_symbol_elided_by_focused_profile"
    )
    assert any(row["reason"] == "focused_profile" for row in focused["omitted_sections"])
    assert focused["truncated"]


def test_focused_preserves_logic_block_docs_and_maps(tmp_path):
    block = (
        "def calculate(total):\n"
        "    # Preserve this explanation.\n"
        '    """Compute the invoice."""\n'
        + "".join(f"    setup_{i} = {i}\n" for i in range(12))
        + '    if total < 0:\n        raise ValueError("negative total")\n'
        + "".join(f"    cleanup_{i} = {i}\n" for i in range(12))
        + "    return total\n"
    )
    source = {
        "file": str(tmp_path / "sample.py"),
        "name": "calculate",
        "source": block,
        "start_line": 40,
        "end_line": 40 + len(block.splitlines()) - 1,
    }
    rendered = _render_source_block(
        source, render_profile="focused", optimize_context=False, focus_query="negative"
    )
    text = rendered["rendered_source"]
    assert "def calculate(total):" in text
    assert '"""Compute the invoice."""' in text
    assert '    if total < 0:\n        raise ValueError("negative total")' in text
    assert "setup_0" not in text and "cleanup_0" not in text
    assert "lines elided" in text
    assert rendered["focus"]["omitted_line_count"] > 20
    lines = text.splitlines()
    original = block.splitlines()
    mapped = set()
    for segment in rendered["line_map"]:
        for line in range(segment["rendered_start_line"], segment["rendered_end_line"] + 1):
            original_line = segment["original_start_line"] + line - segment["rendered_start_line"]
            assert lines[line - 1] == original[original_line - 40]
            mapped.add(line)
    assert all(i not in mapped for i, line in enumerate(lines, 1) if "lines elided" in line)
    assert rendered["source"] == text
    assert source["source"] == block


def test_focused_no_body_match_preserves_full_source(tmp_path):
    block = "def calculate(total):\n    return total\n"
    source = {
        "file": str(tmp_path / "sample.py"),
        "name": "calculate",
        "source": block,
        "start_line": 1,
        "end_line": 2,
    }
    result = _render_source_block(
        source, render_profile="focused", optimize_context=True, focus_query="calculate"
    )
    assert result["rendered_source"] == block
    assert result["focus"]["fallback_reason"] == "no_body_match"


@pytest.mark.parametrize(
    ("block", "query", "reason"),
    [
        ("def broken(:\n    return invoice\n", "invoice", "incomplete_parse"),
        ("invoice = 42\n", "invoice", "no_declaration_body"),
        ("def f():\n    unused = 1\n    return invoice\n", "invoice", "no_size_reduction"),
        ("def f():\n    return 1\n", "", "no_query_terms"),
        ("x" * 1_000_001, "x", "focus_input_limit"),
        ("x\n" * 10_001, "x", "focus_input_limit"),
    ],
    ids=["invalid", "no-declaration", "no-savings", "no-query", "char-limit", "line-limit"],
)
def test_focused_fallbacks_leave_source_intact(tmp_path, block, query, reason):
    result = _render_source_block(
        {
            "file": str(tmp_path / "sample.py"),
            "source": block,
            "start_line": 1,
            "end_line": len(block.splitlines()),
        },
        render_profile="focused",
        optimize_context=False,
        focus_query=query,
    )
    assert result["rendered_source"] == block
    assert result["focus"]["fallback_reason"] == reason
    assert result["focus"]["omitted_ranges"] == []


def test_focused_tight_budget_keeps_maps_honest(tmp_path):
    from tensor_grep.cli.repo_map import build_context_render

    block = 'def calculate(total):\n    """Invoice validation."""\n'
    block += "".join(f"    setup_{i} = {i}\n" for i in range(30))
    block += '    if total < 0:\n        raise ValueError("negative")\n    return total\n'
    path = tmp_path / "invoice.py"
    path.write_text(block, encoding="utf-8")
    result = build_context_render(
        "calculate negative", str(tmp_path), render_profile="focused", max_render_chars=180
    )
    assert result["truncated"]
    assert any(row["reason"] == "focused_profile" for row in result["omitted_sections"])
    for source in result["sources"]:
        lines = source["rendered_source"].splitlines()
        for row in source["line_map"]:
            for line in range(row["rendered_start_line"], row["rendered_end_line"] + 1):
                original_line = row["original_start_line"] + line - row["rendered_start_line"]
                assert lines[line - 1] == block.splitlines()[original_line - 1]


def test_mcp_focused_render_roundtrip(tmp_path, monkeypatch):
    import json

    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    block = "def calculate(total):\n" + "".join(f"    setup_{i} = {i}\n" for i in range(15))
    block += "    return total # negative\n"
    (tmp_path / "invoice.py").write_text(block, encoding="utf-8")
    result = json.loads(
        mcp_server.tg_context(
            action="render",
            query="calculate negative",
            path=str(tmp_path),
            render_profile="focused",
        )
    )
    assert result["render_profile"] == "focused"
    assert result["sources"][0]["focus"]["omitted_line_count"] == 15
    assert result["context_consistency"]["primary_symbol_truncated"]


def test_session_render_keeps_profiles_separate(tmp_path):
    from tensor_grep.cli.session_store import open_session, session_context_render

    block = "def calculate(total):\n" + "".join(f"    setup_{i} = {i}\n" for i in range(15))
    block += "    return total # negative\n"
    (tmp_path / "invoice.py").write_text(block, encoding="utf-8")
    session = open_session(str(tmp_path))
    args = (session.session_id, "calculate negative", str(tmp_path))
    full = session_context_render(*args, render_profile="full")
    focused = session_context_render(*args, render_profile="focused")
    repeated = session_context_render(*args, render_profile="full")
    assert full["sources"] == repeated["sources"]
    assert "setup_0" in full["sources"][0]["source"]
    assert focused["sources"][0]["focus"]["omitted_line_count"] == 15
    assert focused["context_consistency"]["primary_symbol_truncated"]


def test_focused_full_fallback_names_budget_as_omission_cause(tmp_path):
    from tensor_grep.cli.repo_map import build_context_render

    block = "def calculate(total):\n" + "".join(f"    setup_{i} = {i}\n" for i in range(30))
    block += "    return total\n"
    (tmp_path / "invoice.py").write_text(block, encoding="utf-8")
    payload = build_context_render(
        "calculate", str(tmp_path), render_profile="focused", max_tokens=40
    )
    assert payload["sources"][0]["focus"]["fallback_reason"] == "no_body_match"
    assert (
        payload["context_consistency"]["omitted_primary_reason"]
        == "primary_symbol_truncated_by_source_budget"
    )
