import json
from pathlib import Path
from typing import Any

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
        index
        for index, line in enumerate(rendered_lines)
        if "lines omitted by source budget" in line
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
    assert consistency["omitted_primary_reason"] == "primary_symbol_truncated_by_source_budget"


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


# --- T2: agent confidence must reflect graph-corroborated resolution, not render token budget ---
#
# `tg agent` returns the correct primary target and blast-radius confirms real callers, yet
# `confidence.overall` used to stay pinned at the `payload["truncated"]` ladder value (0.72) or the
# capsule-own-budget primary-omission floor (0.55) purely because SOME lower-ranked source got cut
# for render/token budget -- a render artifact, not a resolution-quality signal. These tests build a
# REAL two-file project so blast-radius call-site collection (`_collect_capsule_call_site_evidence`)
# runs for real, while `repo_map.build_context_render` is monkeypatched to deterministically control
# `payload["truncated"]` and which sources the capsule's own snippet budget keeps (mirrors the
# pattern in test_agent_capsule_token_budget_confidence.py / test_agent_capsule_lsp_confidence.py).

_T2_PRIMARY_SYMBOL = "handle_widget_request"
_T2_CALLER_SYMBOL = "process_incoming_request"
_T2_PRIMARY_SOURCE = "def handle_widget_request(payload):\n    return payload\n"
_T2_CALLER_SOURCE = (
    "def process_incoming_request(payload):\n    return handle_widget_request(payload)\n"
)


def _write_t2_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "t2_workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    handler_file = project / "handler.py"
    handler_file.write_text(_T2_PRIMARY_SOURCE, encoding="utf-8")
    caller_file = project / "caller.py"
    caller_file.write_text(_T2_CALLER_SOURCE, encoding="utf-8")
    return {"project": project, "handler": handler_file, "caller": caller_file}


def _t2_context_payload(*, primary_file: Path, caller_file: Path) -> dict[str, Any]:
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "native",
        # The render was cut for TOKEN BUDGET (some other, lower-ranked source) -- a pure render
        # artifact. The primary's own snippet below is small and fits any reasonable budget.
        "truncated": True,
        "files": [str(primary_file), str(caller_file)],
        "file_matches": [],
        "sources": [
            {
                "file": str(primary_file),
                "symbol": _T2_PRIMARY_SYMBOL,
                "name": _T2_PRIMARY_SYMBOL,
                "start_line": 1,
                "end_line": 1,
                "source": _T2_PRIMARY_SOURCE,
            },
            {
                "file": str(caller_file),
                "symbol": _T2_CALLER_SYMBOL,
                "name": _T2_CALLER_SYMBOL,
                "start_line": 1,
                "end_line": 2,
                "source": _T2_CALLER_SOURCE,
            },
        ],
        "validation_commands": ["uv run pytest -q"],
        "edit_plan_seed": {
            "primary_file": str(primary_file),
            "primary_symbol": {"name": _T2_PRIMARY_SYMBOL, "kind": "function"},
            "primary_span": {"start_line": 1, "end_line": 1},
            # Deliberately no "confidence.overall" -- matches real `repo_map` output, which never
            # sets an "overall" key; `_primary_target` falls back to the raw 0.9 seed default.
            "validation_plan": [
                {
                    "runner": "pytest",
                    "scope": "repo",
                    "target": "",
                    "command": "uv run pytest -q",
                    "confidence": 0.55,
                    "detection": "detected",
                }
            ],
            "validation_commands": ["uv run pytest -q"],
            "validation_alignment": {"status": "aligned", "kept_count": 1, "filtered_count": 0},
            "edit_ordering": [str(primary_file)],
        },
        "navigation_pack": {
            "primary_target": {
                "file": str(primary_file),
                "symbol": _T2_PRIMARY_SYMBOL,
                "kind": "function",
                "start_line": 1,
                "end_line": 1,
            },
            "follow_up_reads": [],
        },
        "candidate_edit_targets": {"files": [str(primary_file)], "symbols": [], "tests": []},
        "context_consistency": {
            "primary_file_included": True,
            "rendered_context_includes_primary": True,
        },
    }


def test_capsule_uplifts_render_truncated_confidence_for_corroborated_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) A render-token-budget-only cut (`payload["truncated"]`) with a graph-corroborated
    primary must not be reported as resolution uncertainty: confidence rises to 0.8 and ask_user
    is no longer required.
    """
    paths = _write_t2_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _t2_context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        _T2_PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=8000,
    )

    # The primary's own snippet was NOT cut -- only some other, lower-ranked source was, which is
    # exactly the render-truncated-but-primary-included case F4's original mechanism could not
    # reach (it only covered the capsule's own snippet-budget omission of the primary itself).
    assert payload["context_consistency"]["capsule_primary_file_omitted"] is False
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload["confidence"]["overall"] == 0.8
    assert payload["primary_target"]["confidence"] == 0.8
    assert payload["context_consistency"]["confidence_basis"] == "resolution-quality"
    assert payload["ask_user_before_editing"]["required"] is False


def test_capsule_scan_truncated_disqualifies_render_truncated_uplift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-1 (1D) T2 disqualifier: the SAME render-truncated + graph-corroborated fixture as the
    uplift test above must NOT uplift when the underlying repo SCAN was also truncated
    (`scan_limit.possibly_truncated`) -- the ranking that produced this "corroborated" primary
    never saw the whole repository, so call-site evidence collected against an incomplete scan
    cannot justify raising confidence to the same degree as a genuinely complete scan.
    """
    paths = _write_t2_project(tmp_path)

    def fake_context_render(*args: object, **kwargs: object) -> dict[str, Any]:
        payload = _t2_context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
        )
        payload["scan_limit"] = {
            "max_repo_files": 1,
            "scanned_files": 1,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        }
        payload["scan_remediation"] = "raise --max-repo-files"
        return payload

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        _T2_PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=8000,
    )

    assert payload["context_consistency"]["capsule_primary_file_omitted"] is False
    assert payload["call_site_evidence"]["status"] == "collected"
    # NOT uplifted: confidence must stay below the 0.8 uplift ceiling despite collected call-site
    # evidence, because the scan itself was truncated.
    assert payload["confidence"]["overall"] < 0.8
    assert payload["primary_target"]["confidence"] < 0.8
    assert payload["context_consistency"].get("confidence_basis") != "resolution-quality"
    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        "scan was truncated" in reason for reason in payload["ask_user_before_editing"]["reasons"]
    )
    # Additive scan-truncation signals propagate onto the capsule (PR-1 1D data propagation).
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["scan_remediation"] == "raise --max-repo-files"
    assert payload["result_incomplete"] is True


def test_capsule_render_truncated_genuine_tie_still_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) Same fixture, but with a genuine equal-confidence alternative target: the render-budget
    uplift must NOT override a real ambiguity signal -- confidence stays at/under the tie clamp and
    ask_user is still required.
    """
    paths = _write_t2_project(tmp_path)
    alternative_file = paths["project"] / "archive.py"
    alternative_file.write_text(
        "def archive_widget_request(payload):\n    return payload\n", encoding="utf-8"
    )

    def fake_context_render(*args: object, **kwargs: object) -> dict[str, Any]:
        payload = _t2_context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
        )
        payload["candidate_edit_targets"]["symbols"] = [
            {
                "file": str(alternative_file.resolve()),
                "name": "archive_widget_request",
                "kind": "function",
                "line": 1,
                "score": 95,
            }
        ]
        payload["file_matches"] = [
            {
                "path": str(alternative_file.resolve()),
                "score": 95,
                "reasons": ["symbol"],
                "provenance": ["heuristic"],
            }
        ]
        return payload

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        _T2_PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=8000,
    )

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["confidence"]["overall"] <= 0.74
    assert payload["ask_user_before_editing"]["required"] is True


def test_capsule_render_truncated_genuine_misroute_still_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) A genuine misroute (the primary was never ranked/selected at all -- `primary_file_included`
    is False) must stay at the 0.55 safety floor even when `payload["truncated"]` is True and a
    caller exists -- corroboration must never override a "never ranked" signal.
    """
    paths = _write_t2_project(tmp_path)

    def fake_context_render(*args: object, **kwargs: object) -> dict[str, Any]:
        payload = _t2_context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
        )
        payload["context_consistency"] = {
            "primary_file_included": False,
            "rendered_context_includes_primary": False,
        }
        return payload

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        _T2_PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=8000,
    )

    assert payload["context_consistency"]["primary_file_included"] is False
    assert payload["confidence"]["overall"] <= 0.55
    assert payload["ask_user_before_editing"]["required"] is True


def test_call_site_evidence_gates_on_caller_supplied_seed_confidence(
    tmp_path: Path,
) -> None:
    """T2 circular-gate fix: `_collect_capsule_call_site_evidence` must gate on the caller-supplied
    PRE-cap seed confidence, not `target["confidence"]` -- otherwise a target whose displayed
    confidence was already capped below 0.75 by an upstream trust/tie/budget cap could never earn
    the very call-site evidence that would justify relief from that cap.
    """
    paths = _write_t2_project(tmp_path)
    capped_target = {
        "file": str(paths["handler"].resolve()),
        "symbol": _T2_PRIMARY_SYMBOL,
        "kind": "function",
        "confidence": 0.55,  # simulates an already-capped displayed confidence
    }

    related_call_sites, evidence = agent_capsule._collect_capsule_call_site_evidence(
        _T2_PRIMARY_SYMBOL,
        str(paths["project"]),
        capped_target,
        include_blast_radius=True,
        max_files=3,
        max_repo_files=None,
        seed_confidence=0.9,
    )

    assert evidence["status"] == "collected"
    assert related_call_sites


def test_call_site_evidence_still_skips_on_low_seed_confidence(tmp_path: Path) -> None:
    """Companion to the above: a genuinely low seed confidence must still skip collection even when
    `target["confidence"]` itself looks high -- the seed value is authoritative for this gate.
    """
    paths = _write_t2_project(tmp_path)
    displayed_high_target = {
        "file": str(paths["handler"].resolve()),
        "symbol": _T2_PRIMARY_SYMBOL,
        "kind": "function",
        "confidence": 0.9,
    }

    related_call_sites, evidence = agent_capsule._collect_capsule_call_site_evidence(
        _T2_PRIMARY_SYMBOL,
        str(paths["project"]),
        displayed_high_target,
        include_blast_radius=True,
        max_files=3,
        max_repo_files=None,
        seed_confidence=0.5,
    )

    assert related_call_sites == []
    assert evidence["status"] == "skipped"
    assert evidence["reason"] == "primary target confidence below call-site collection threshold"


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
