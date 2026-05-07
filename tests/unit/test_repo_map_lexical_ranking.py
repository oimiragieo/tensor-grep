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
