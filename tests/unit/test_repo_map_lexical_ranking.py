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


def test_context_pack_skips_source_fallback_when_parser_backed_matches_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    module_path = project / "src" / "payments.py"
    _write(
        module_path,
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
    )

    def fail_if_called(path: str, terms: list[str]) -> int:
        raise AssertionError(f"unexpected source fallback for {path}: {terms}")

    monkeypatch.setattr(repo_map, "_score_file_source_terms", fail_if_called)

    payload = repo_map.build_context_pack("createInvoice", project)

    assert payload["file_matches"][0]["path"] == str(module_path.resolve())


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
