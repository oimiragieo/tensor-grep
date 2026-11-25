"""Regression tests for `tg importers` test-file resolution and importer tagging."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import repo_map


def test_build_file_importers_includes_and_tags_test_importers(tmp_path: Path) -> None:
    """BUG-07: `tests/test_billing.py` importing `src/billing.py` is returned with `is_test=True`."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    billing = root / "src" / "billing.py"
    billing.write_text(
        "def create_invoice():\n    return 1\ndef compute_tax():\n    return 2\n",
        encoding="utf-8",
    )
    app_file = root / "src" / "app.py"
    app_file.write_text("from billing import create_invoice\n", encoding="utf-8")
    test_file = root / "tests" / "test_billing.py"
    test_file.write_text("from billing import create_invoice, compute_tax\n", encoding="utf-8")

    payload = repo_map.build_file_importers(billing, root)
    assert payload["importer_count"] == 2
    assert payload["production_importer_count"] == 1
    assert payload["test_importer_count"] == 1
    by_file = {item["file"]: item for item in payload["importers"]}
    assert by_file[str(app_file.resolve())]["is_test"] is False
    assert by_file[str(test_file.resolve())]["is_test"] is True
