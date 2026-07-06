import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, repo_map, session_store
from tensor_grep.cli.main import app


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    payments = src_dir / "payments.py"
    billing = src_dir / "billing.py"
    reporting = src_dir / "reporting.py"
    test_path = tests_dir / "test_payments.py"

    _write(
        payments,
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    service_fee = subtotal + 5\n"
        "    grand_total = service_fee + 10\n"
        "    return grand_total\n",
    )
    _write(
        billing,
        "def invoice_total(total):\n    running_total = total + 1\n    return running_total\n",
    )
    _write(
        reporting,
        "def create_invoice_report(total):\n"
        "    invoice_label = f'invoice:{total}'\n"
        "    return invoice_label\n",
    )
    _write(
        test_path,
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 18\n",
    )

    return {
        "project": project,
        "payments": payments,
        "billing": billing,
        "reporting": reporting,
        "test": test_path,
    }


def _build_scored_payload(
    tmp_path: Path,
    *,
    primary_score: int = 95,
    secondary_score: int = 70,
    tertiary_score: int = 35,
) -> dict[str, object]:
    project = tmp_path / "payload"
    primary = (project / "src" / "primary.py").resolve()
    secondary = (project / "src" / "secondary.py").resolve()
    tertiary = (project / "src" / "tertiary.py").resolve()
    test_path = (project / "tests" / "test_primary.py").resolve()
    for path in [primary, secondary, tertiary, test_path]:
        _write(path, "# stub\n")

    return {
        "query": "invoice primary",
        "files": [str(primary), str(secondary), str(tertiary)],
        "file_matches": [
            {
                "path": str(primary),
                "score": primary_score,
                "graph_score": None,
                "reasons": ["symbol"],
            },
            {
                "path": str(secondary),
                "score": secondary_score,
                "graph_score": None,
                "reasons": ["symbol"],
            },
            {
                "path": str(tertiary),
                "score": tertiary_score,
                "graph_score": None,
                "reasons": ["symbol"],
            },
        ],
        "test_matches": [
            {"path": str(test_path), "score": 12, "graph_score": None, "reasons": ["test-import"]}
        ],
        "tests": [str(test_path)],
        "symbols": [
            {
                "file": str(primary),
                "name": "create_invoice",
                "kind": "function",
                "line": 1,
                "score": primary_score,
            },
            {
                "file": str(secondary),
                "name": "helper_invoice",
                "kind": "function",
                "line": 1,
                "score": secondary_score,
            },
            {
                "file": str(tertiary),
                "name": "archive_invoice",
                "kind": "function",
                "line": 1,
                "score": tertiary_score,
            },
        ],
        "file_summaries": [
            {
                "path": str(primary),
                "symbols": [{"kind": "function", "name": "create_invoice", "line": 1}],
            },
            {
                "path": str(secondary),
                "symbols": [{"kind": "function", "name": "helper_invoice", "line": 1}],
            },
            {
                "path": str(tertiary),
                "symbols": [{"kind": "function", "name": "archive_invoice", "line": 1}],
            },
        ],
        "sources": [
            {
                "file": str(primary),
                "name": "create_invoice",
                "source": "def create_invoice(total):\n    subtotal = total + 1\n    return subtotal\n",
                "rendered_source": "def create_invoice(total):\n    subtotal = total + 1\n    return subtotal\n",
            },
            {
                "file": str(secondary),
                "name": "helper_invoice",
                "source": "def helper_invoice(total):\n    invoice_total = total + 2\n    return invoice_total\n",
                "rendered_source": "def helper_invoice(total):\n    invoice_total = total + 2\n    return invoice_total\n",
            },
            {
                "file": str(tertiary),
                "name": "archive_invoice",
                "source": "def archive_invoice(total):\n    invoice_total = total + 3\n    return invoice_total\n",
                "rendered_source": "def archive_invoice(total):\n    invoice_total = total + 3\n    return invoice_total\n",
            },
        ],
        "max_files": 3,
        "max_symbols_per_file": 2,
        "edit_plan_seed": {"primary_file": str(primary)},
    }


def _section_score(section: dict[str, object]) -> int:
    provenance = section.get("provenance", {})
    if not isinstance(provenance, dict):
        return 0
    if "score" in provenance:
        return int(provenance.get("score", 0))
    matches = provenance.get("matches", [])
    if not isinstance(matches, list):
        return 0
    return max(
        (int(match.get("score", 0)) for match in matches if isinstance(match, dict)), default=0
    )


def _max_section_tokens(payload: dict[str, object]) -> int:
    sections = payload.get("sections", [])
    assert isinstance(sections, list)
    return max((int(section["token_estimate"]) for section in sections), default=0)


def _assert_within_budget(payload: dict[str, object], budget: int) -> None:
    assert int(payload["token_estimate"]) <= budget + _max_section_tokens(payload)


def test_estimate_tokens_is_zero_for_empty_string() -> None:
    assert repo_map._estimate_tokens("") == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a", 1),
        ("abcd", 2),
        ("a" * 7, 2),
        ("a" * 8, 3),
    ],
)
def test_estimate_tokens_uses_deterministic_ceil_heuristic(text: str, expected: int) -> None:
    assert repo_map._estimate_tokens(text) == expected


def test_estimate_tokens_is_deterministic() -> None:
    text = "invoice summary\nSource:\n```text\nreturn total + tax\n```"
    assert repo_map._estimate_tokens(text) == repo_map._estimate_tokens(text)


def test_source_budget_truncation_marks_noncontiguous_tail_graft() -> None:
    text = (
        "def build_payload():\n"
        "    payload = {\n"
        + "".join(f"        'field_{index}': {index},\n" for index in range(40))
        + "    }\n"
        "    raise RuntimeError('boom')\n"
    )

    truncated, selected_lines, was_truncated = repo_map._truncate_source_text_to_budget(
        text,
        max_tokens=24,
        max_chars=None,
    )

    assert was_truncated is True
    assert selected_lines[-2] == 0
    assert selected_lines[-1] == len(text.splitlines())
    assert "lines omitted by source budget" in truncated
    assert "raise RuntimeError" in truncated


def test_source_budget_tail_graft_preserves_tail_line_map() -> None:
    text = (
        "def build_payload():\n"
        "    payload = {\n"
        + "".join(f"        'field_{index}': {index},\n" for index in range(40))
        + "    }\n"
        "    raise RuntimeError('boom')\n"
    )
    line_count = len(text.splitlines())
    sources = [
        {
            "file": "payments.py",
            "name": "build_payload",
            "rendered_source": text,
            "line_map": [
                {
                    "rendered_start_line": 1,
                    "rendered_end_line": line_count,
                    "original_start_line": 1,
                    "original_end_line": line_count,
                }
            ],
        }
    ]

    budgeted_sources, _, _ = repo_map._apply_source_output_budget(
        sources,
        max_tokens=24,
        max_render_chars=None,
    )

    budgeted = budgeted_sources[0]
    rendered_source = str(budgeted["rendered_source"])
    rendered_lines = rendered_source.splitlines()
    expanded = agent_capsule._expanded_line_map(budgeted, rendered_source)
    marker_index = next(
        index for index, line in enumerate(rendered_lines) if "lines omitted by source budget" in line
    )
    tail_index = next(
        index for index, line in enumerate(rendered_lines) if "raise RuntimeError" in line
    )

    assert expanded[marker_index]["line"] is None
    assert expanded[tail_index]["line"] == line_count


def test_render_sections_include_token_estimates_and_total(tmp_path: Path) -> None:
    rendered, sections, truncated, token_estimate, omitted_sections = (
        repo_map._render_context_string_and_sections(_build_scored_payload(tmp_path))
    )

    assert rendered
    assert truncated is False
    assert token_estimate >= sum(int(section["token_estimate"]) for section in sections)
    assert omitted_sections == []
    assert all(isinstance(section["token_estimate"], int) for section in sections)


def test_sections_are_ordered_by_salience_when_primary_is_highest_score(tmp_path: Path) -> None:
    _, sections, _, _, _ = repo_map._render_context_string_and_sections(
        _build_scored_payload(tmp_path)
    )

    ranked_sections = [section for section in sections if section["kind"] != "query"]
    ranked_scores = [_section_score(section) for section in ranked_sections]
    assert ranked_scores == sorted(ranked_scores, reverse=True)


def test_budget_aware_selection_prefers_higher_score_sections(tmp_path: Path) -> None:
    _, sections, truncated, _, omitted_sections = repo_map._render_context_string_and_sections(
        _build_scored_payload(tmp_path),
        max_tokens=42,
    )

    assert truncated is True
    included_scores = [
        _section_score(section) for section in sections if section["kind"] != "query"
    ]
    omitted_scores = [int(section["score"]) for section in omitted_sections]
    assert included_scores
    assert omitted_scores
    assert min(included_scores) >= max(omitted_scores)


def test_primary_file_sections_are_prioritized_when_budget_is_tight(tmp_path: Path) -> None:
    payload = _build_scored_payload(
        tmp_path,
        primary_score=55,
        secondary_score=90,
        tertiary_score=25,
    )
    primary_file = payload["edit_plan_seed"]["primary_file"]
    rendered, sections, truncated, _, omitted_sections = (
        repo_map._render_context_string_and_sections(
            payload,
            max_tokens=32,
        )
    )

    assert rendered
    assert truncated is True
    assert any(section.get("path") == primary_file for section in sections)
    assert any(section["file"] != primary_file for section in omitted_sections)


def test_primary_source_beats_summary_when_budget_is_tight(tmp_path: Path) -> None:
    payload = _build_scored_payload(tmp_path)
    primary_file = payload["edit_plan_seed"]["primary_file"]

    rendered, sections, truncated, _, omitted_sections = (
        repo_map._render_context_string_and_sections(
            payload,
            max_tokens=24,
        )
    )

    assert rendered
    assert truncated is True
    assert any(
        section.get("kind") == "source" and section.get("path") == primary_file
        for section in sections
    )
    assert "def create_invoice" in rendered
    assert any(
        section.get("kind") == "summary" and section.get("file") == primary_file
        for section in omitted_sections
    )


def test_rendered_source_sections_are_deduplicated_per_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    _write(
        module_path,
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n\n"
        "def invoice_subtotal(total, tax):\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n",
    )

    payload = repo_map.build_context_render(
        "invoice subtotal tax",
        project,
        max_files=1,
        max_sources=4,
    )

    source_sections = [section for section in payload["sections"] if section["kind"] == "source"]
    assert [section["path"] for section in source_sections].count(str(module_path.resolve())) == 1


def test_context_render_caps_source_payload_when_max_tokens_is_set(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    filler = "\n".join(f"    debug_line_{index:03d} = {index}" for index in range(120))
    _write(
        module_path,
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * 0.1\n"
        "    total = subtotal + tax\n"
        f"{filler}\n"
        "    return total\n",
    )

    payload = repo_map.build_context_render(
        "create invoice tax",
        project,
        max_files=1,
        max_sources=1,
        max_tokens=64,
        optimize_context=True,
        render_profile="llm",
    )

    source = payload["sources"][0]
    rendered_source = source["rendered_source"]
    assert "def create_invoice" in rendered_source
    assert "tax = subtotal * 0.1" in rendered_source
    assert "return total" in rendered_source
    assert "debug_line_119" not in rendered_source
    assert repo_map._estimate_tokens(rendered_source) <= 64
    assert source["source_budget"]["truncated"] is True
    assert payload["source_budget"]["truncated_sources"] == 1
    assert payload["truncated"] is True
    assert any(
        section.get("kind") == "source_payload"
        and section.get("file") == str(module_path.resolve())
        for section in payload["omitted_sections"]
    )


def test_context_consistency_downgrades_when_primary_symbol_source_is_truncated(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    filler = "\n".join(f"    debug_line_{index:03d} = {index}" for index in range(160))
    _write(
        module_path,
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * 0.1\n"
        f"{filler}\n"
        "    total = subtotal + tax\n"
        "    return total\n",
    )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=1,
        max_sources=1,
        max_tokens=48,
        optimize_context=True,
        render_profile="llm",
    )

    consistency = payload["context_consistency"]
    assert consistency["primary_symbol"] == "create_invoice"
    assert consistency["primary_symbol_included"] is True
    assert consistency["rendered_context_includes_primary_symbol"] is True
    assert consistency["primary_symbol_truncated"] is True
    assert consistency["confidence_downgraded"] is True
    assert (
        consistency["omitted_primary_reason"]
        == "primary_symbol_truncated_by_source_budget"
    )


@pytest.mark.parametrize("render_profile", ["full", "compact", "llm"])
def test_max_tokens_works_for_each_render_profile(tmp_path: Path, render_profile: str) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_tokens=48,
        optimize_context=(render_profile != "full"),
        render_profile=render_profile,
    )

    assert payload["render_profile"] == render_profile
    assert payload["max_tokens"] == 48
    _assert_within_budget(payload, 48)


@pytest.mark.parametrize("max_tokens", [24, 32, 40, 56])
def test_rendered_token_estimate_stays_within_one_section_tolerance(
    tmp_path: Path, max_tokens: int
) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_tokens=max_tokens,
    )

    _assert_within_budget(payload, max_tokens)


def test_char_budget_can_be_tighter_than_token_budget(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_tokens=500,
        max_render_chars=120,
    )

    assert payload["truncated"] is True
    assert len(payload["rendered_context"]) <= 120
    assert payload["omitted_sections"]


def test_token_budget_can_be_tighter_than_char_budget(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_tokens=32,
        max_render_chars=1000,
    )

    assert payload["truncated"] is True
    assert payload["max_render_chars"] == 1000
    _assert_within_budget(payload, 32)


def test_omitted_sections_report_metadata_for_token_truncation(tmp_path: Path) -> None:
    _, _, truncated, _, omitted_sections = repo_map._render_context_string_and_sections(
        _build_scored_payload(tmp_path),
        max_tokens=36,
    )

    assert truncated is True
    assert omitted_sections
    assert all(
        {"file", "symbol", "score", "token_estimate"} <= set(section)
        for section in omitted_sections
    )


def test_omitted_sections_report_metadata_for_char_truncation(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_render_chars=100,
    )

    assert payload["truncated"] is True
    assert payload["omitted_sections"]
    truncation_sections = [
        section for section in payload["omitted_sections"] if section.get("kind") != "primary"
    ]
    primary_sections = [
        section for section in payload["omitted_sections"] if section.get("kind") == "primary"
    ]
    assert all(
        {"file", "symbol", "score", "token_estimate"} <= set(section)
        for section in truncation_sections
    )
    assert all({"file", "reason"} <= set(section) for section in primary_sections)


def test_build_context_render_includes_max_tokens_and_model(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=3,
        max_sources=3,
        max_tokens=64,
        model="gpt-test",
    )

    assert payload["max_tokens"] == 64
    assert payload["model"] == "gpt-test"
    assert isinstance(payload["token_estimate"], int)


def test_session_context_render_accepts_max_tokens_and_model(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = session_store.open_session(str(paths["project"])).session_id

    payload = session_store.session_context_render(
        session_id,
        "create invoice",
        str(paths["project"]),
        max_files=3,
        max_sources=3,
        max_tokens=44,
        model="gpt-test",
    )

    assert payload["session_id"] == session_id
    assert payload["max_tokens"] == 44
    assert payload["model"] == "gpt-test"
    _assert_within_budget(payload, 44)


def test_session_serve_context_render_accepts_max_tokens_and_model(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = session_store.open_session(str(paths["project"])).session_id

    payload = session_store.serve_session_request(
        session_id,
        {
            "command": "context_render",
            "query": "create invoice",
            "max_files": 3,
            "max_sources": 3,
            "max_tokens": 44,
            "model": "gpt-test",
        },
        str(paths["project"]),
    )

    assert payload["session_id"] == session_id
    assert payload["max_tokens"] == 44
    assert payload["model"] == "gpt-test"
    _assert_within_budget(payload, 44)


def test_cli_context_render_accepts_max_tokens_and_model(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--max-files",
            "3",
            "--max-sources",
            "3",
            "--max-tokens",
            "48",
            "--model",
            "gpt-test",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["max_tokens"] == 48
    assert payload["model"] == "gpt-test"
    _assert_within_budget(payload, 48)


def test_cli_session_context_render_accepts_max_tokens_and_model(tmp_path: Path) -> None:
    project = _build_project(tmp_path)["project"]
    session_id = session_store.open_session(str(project)).session_id
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "session",
            "context-render",
            session_id,
            str(project),
            "--query",
            "create invoice",
            "--max-files",
            "3",
            "--max-sources",
            "3",
            "--max-tokens",
            "48",
            "--model",
            "gpt-test",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == session_id
    assert payload["max_tokens"] == 48
    assert payload["model"] == "gpt-test"
    _assert_within_budget(payload, 48)
