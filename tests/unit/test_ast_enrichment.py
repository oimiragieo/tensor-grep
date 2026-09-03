import tempfile
from pathlib import Path

import pytest

from tensor_grep.cli.ast_enrichment import (
    enrich_match_with_container,
    enrich_search_items_with_containers,
)


@pytest.fixture
def sample_python_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "sample.py"
        file_path.write_text(
            """# top-level comment
import os

GLOBAL_VAR = 42

def calculate_total(price, tax):
    subtotal = price * 1.1
    result = subtotal + tax
    return result

class InvoiceService:
    def __init__(self, currency="USD"):
        self.currency = currency

    def generate_invoice(self, order_id):
        total = calculate_total(100, 5)
        return f"{self.currency}: {total}"
""",
            encoding="utf-8",
        )
        yield file_path


def test_enrich_match_inside_function(sample_python_file):
    # Line 8 is inside `calculate_total`
    container = enrich_match_with_container(sample_python_file, 8)
    assert container is not None
    assert container["name"] == "calculate_total"
    assert container["kind"] in ("function", "def")
    assert container["range"][0] <= 7
    assert container["range"][1] >= 9


def test_enrich_match_inside_class_method(sample_python_file):
    # Line 17 is inside `InvoiceService.generate_invoice`
    container = enrich_match_with_container(sample_python_file, 17)
    assert container is not None
    assert container["name"] == "generate_invoice"
    assert container["range"][0] <= 16
    assert container["range"][1] >= 17


def test_enrich_match_top_level_none(sample_python_file):
    # Line 4 is `GLOBAL_VAR = 42`
    container = enrich_match_with_container(sample_python_file, 4)
    assert container is None


def test_enrich_search_items_bounded(sample_python_file):
    items = [
        {"path": str(sample_python_file), "line_number": 8, "line": "    result = subtotal + tax"},
        {
            "path": str(sample_python_file),
            "line_number": 17,
            "line": "        total = calculate_total(100, 5)",
        },
    ]
    enriched, diagnostics = enrich_search_items_with_containers([sample_python_file], items)
    assert len(enriched) == 2
    assert enriched[0]["container"]["name"] == "calculate_total"
    assert enriched[1]["container"]["name"] == "generate_invoice"
    assert diagnostics["enriched_items"] == 2
    assert not diagnostics["truncated"]


def test_enrichment_file_limit_guardrail(sample_python_file):
    items = [{"path": f"fake_path_{i}.py", "line_number": 8, "line": "code"} for i in range(10)]
    # Enforce limit of 2 files
    _, diagnostics = enrich_search_items_with_containers([], items, file_limit=2)
    assert diagnostics["total_files"] == 10
    assert diagnostics["parsed_files"] == 2
    assert diagnostics["truncated"] is True
