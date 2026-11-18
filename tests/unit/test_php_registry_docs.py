import inspect

from tensor_grep.cli import lang_php, repo_map  # noqa: F401
from tensor_grep.cli.lang_registry import LANGUAGE_REGISTRY


def test_php_scope_matches_registry_cross_file_hooks() -> None:
    doc = inspect.getdoc(lang_php) or ""
    scope = doc.split("SCOPE:", 1)[1].split("FAIL-CLOSED CONTRACT", 1)[0]
    spec = LANGUAGE_REGISTRY["php"]

    assert spec.file_imports_symbol_from_definition is not None
    assert spec.import_update_target is None
    assert spec.prime_repo_context is None
    assert "file_imports_symbol_from_definition`` field is implemented" in scope
    assert "``import_update_target``\nand ``prime_repo_context`` stay DEFERRED" in scope
    assert "all stay ``None``" not in doc
