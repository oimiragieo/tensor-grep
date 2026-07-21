from pathlib import Path

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_context_pack_matches_camel_case_query_against_snake_case_symbol(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    _write(
        module_path,
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
    )

    payload = repo_map.build_context_pack("createInvoice", project)

    assert payload["file_matches"][0]["path"] == str(module_path.resolve())


def test_context_pack_uses_bounded_source_evidence_with_parser_backed_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    _write(
        module_path,
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
    )

    calls: list[str] = []
    original = repo_map._score_file_source_terms

    def track_source_score(path: str, terms: list[str]) -> int:
        calls.append(path)
        return original(path, terms)

    monkeypatch.setattr(repo_map, "_score_file_source_terms", track_source_score)

    payload = repo_map.build_context_pack("createInvoice", project)

    assert calls == [str(module_path.resolve())]
    assert payload["file_matches"][0]["path"] == str(module_path.resolve())
    assert {"definition", "symbol", "source"} <= set(payload["file_matches"][0]["reasons"])


def test_context_pack_surfaces_source_terms_for_definition_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    _write(
        module_path,
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
    )

    payload = repo_map.build_context_pack("subtotal tax", project)

    assert payload["file_matches"][0]["path"] == str(module_path.resolve())


def test_context_pack_prefers_exact_symbol_match_over_partial_split_matches(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    core_path = project / "src" / "core.py"
    service_path = project / "src" / "service.py"
    _write(
        core_path,
        "def create_invoice(total):\n    return total\n",
    )
    _write(
        service_path,
        "from .core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )

    payload = repo_map.build_context_pack("create_invoice", project)

    assert payload["file_matches"][0]["path"] == str(core_path.resolve())


def test_context_pack_does_not_label_path_only_symbol_scores_as_definitions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    noisy_path = project / "src" / "safeParseJSON_helpers.py"
    _write(
        noisy_path,
        "def unrelated_helper(value):\n    return value\n",
    )

    payload = repo_map.build_context_pack("safeParseJSON", project)

    match = next(
        item for item in payload["file_matches"] if item["path"] == str(noisy_path.resolve())
    )
    assert "path" in match["reasons"]
    assert "definition" not in match["reasons"]
    assert "symbol" not in match["reasons"]


def test_symbol_impact_does_not_label_partial_symbol_matches_as_definitions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    target_path = project / ".claude" / "lib" / "utils.cjs"
    noisy_path = project / ".claude" / "lib" / "json-router.cjs"
    _write(
        target_path,
        "function safeParseJSON(value) {\n  return JSON.parse(value);\n}\n"
        "module.exports = { safeParseJSON };\n",
    )
    _write(
        noisy_path,
        "function parseJSONRoute(value) {\n  return value;\n}\n"
        "module.exports = { parseJSONRoute };\n",
    )

    payload = repo_map.build_symbol_impact("safeParseJSON", project)
    matches_by_path = {item["path"]: item for item in payload["file_matches"]}

    assert matches_by_path[str(target_path.resolve())]["reasons"][:1] == ["definition"]
    noisy_match = matches_by_path.get(str(noisy_path.resolve()))
    if noisy_match is not None:
        assert "definition" not in noisy_match["reasons"]
        assert "symbol" not in noisy_match["reasons"]


def test_context_render_limits_source_sections_to_one_per_file(tmp_path: Path) -> None:
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
        "invoice subtotal tax", project, max_files=1, max_sources=4
    )
    source_sections = [section for section in payload["sections"] if section["kind"] == "source"]

    assert [section["path"] for section in source_sections].count(str(module_path.resolve())) == 1


# ---------------------------------------------------------------------------
# Task #254: Blackbird-style ranking heuristics on the flat `_score_symbol` scorer.
# ---------------------------------------------------------------------------


def test_score_symbol_exact_boundary_beats_substring_only_match() -> None:
    """Heuristic 3: a query term that hits a symbol's name as a whole, word-boundary-respecting
    token (a `split_terms` member) outranks the same term only appearing embedded inside a
    longer, differently-tokenized identifier -- a raw substring hit `_score_text_terms` already
    credits identically to a clean token match. Isolated at the `_score_symbol` level (a direct
    call, not the full context-pack loop) so the comparison is not entangled with the outer
    loop's separate exact/bridge/covered query-match bonuses, which key off the symbol name
    matching the WHOLE query rather than a single term.

    Before heuristic 3, both symbols scored identically (3): `_score_text_terms` grants the same
    +1 credit whether "rank" hits `rank_symbol`'s name as a clean token or merely as a substring
    of `rerank_value_symbol`'s "rerank". The boundary bonus breaks that tie in favor of the
    cleaner match.
    """
    terms = ["rank"]
    boundary_symbol = {"name": "rank", "kind": "function", "file": "src/module_a.py"}
    substring_symbol = {"name": "rerank_value", "kind": "function", "file": "src/module_b.py"}

    boundary_score = repo_map._score_symbol(boundary_symbol, terms)
    substring_score = repo_map._score_symbol(substring_symbol, terms)

    assert boundary_score > substring_score
    # Pin the exact pre/post values so a future scoring-scale change can't silently make this
    # pass for the wrong reason (e.g. an unrelated bonus swamping the boundary delta).
    assert boundary_score == 4
    assert substring_score == 3


def test_score_symbol_test_file_hit_sinks_below_non_test_implementation() -> None:
    """Heuristic 2: a same-named symbol defined in a test file scores lower than a non-test
    implementation once a caller supplies `non_test_definition_names` (computed once per scoring
    pass via `_non_test_definition_names`). Without that opt-in (the default, `None`), the two
    score identically -- existing callers that have not been updated stay byte-for-byte
    unaffected, and a test-only symbol with NO non-test counterpart is never penalized."""
    terms = ["process", "widget", "report"]
    impl_symbol = {"name": "process_widget_report", "kind": "function", "file": "src/widgets.py"}
    test_symbol = {
        "name": "process_widget_report",
        "kind": "function",
        "file": "tests/test_widgets.py",
    }
    non_test_definition_names = repo_map._non_test_definition_names([impl_symbol, test_symbol])
    assert non_test_definition_names == frozenset({"process_widget_report"})

    impl_score = repo_map._score_symbol(
        impl_symbol, terms, non_test_definition_names=non_test_definition_names
    )
    test_score = repo_map._score_symbol(
        test_symbol, terms, non_test_definition_names=non_test_definition_names
    )

    assert test_score < impl_score

    # Opt-in only: a caller that does not pass `non_test_definition_names` sees no penalty at
    # all -- the two symbols still score identically, matching pre-#254 behavior exactly.
    assert repo_map._score_symbol(test_symbol, terms) == impl_score

    # A test-file symbol with NO non-test counterpart anywhere is never penalized -- there is
    # nothing to prefer it over.
    test_only_names = repo_map._non_test_definition_names([test_symbol])
    assert repo_map._score_symbol(
        test_symbol, terms, non_test_definition_names=test_only_names
    ) == repo_map._score_symbol(test_symbol, terms)
