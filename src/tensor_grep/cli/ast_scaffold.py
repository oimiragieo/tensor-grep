"""`tg new` project scaffold writer (relocated from ``main.py`` for the file-size ratchet)."""

from __future__ import annotations

import os
from pathlib import Path

from tensor_grep.cli._index_lock import atomic_write_bytes_anchored

_AST_RULE_SCAFFOLD_EXAMPLES = {
    "javascript": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "python": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "rust": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "typescript": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
}


def _ast_rule_scaffold_examples(lang: str) -> tuple[str, str, str]:
    language = lang.strip().lower()
    from tensor_grep.backends.ast_backend import get_supported_languages

    if language not in get_supported_languages():
        raise ValueError(
            f"Cannot scaffold AST rule for language {lang!r}; run `tg ast-info` for supported languages."
        )
    if language not in _AST_RULE_SCAFFOLD_EXAMPLES:
        raise ValueError(
            f"Cannot scaffold AST rule for language {lang!r}: no validated pattern example is available for `tg test` on both front doors."
        )
    return _AST_RULE_SCAFFOLD_EXAMPLES[language]


def _ast_rule_scaffold_contents(name: str, lang: str) -> str:
    pattern, _valid, _invalid = _ast_rule_scaffold_examples(lang)
    return f"id: {name}\nlanguage: {lang}\nrule:\n  pattern: {pattern}\n"


def _ast_test_scaffold_contents(name: str, lang: str) -> str:
    _pattern, valid, invalid = _ast_rule_scaffold_examples(lang)
    return f"id: {name}\nruleId: {name}\nvalid:\n  - {valid}\ninvalid:\n  - {invalid}\n"


def _write_ast_project_scaffold(base_dir: Path, lang: str) -> Path:
    # `lang` is interpolated into a hand-formatted YAML rule below; a newline would inject
    # sibling keys. Sibling `name` is already validated -- this closes the asymmetry without
    # rejecting real language spellings like `c++`/`c#`.
    if not lang.strip() or any(c in lang for c in "\r\n"):
        raise ValueError(f"Invalid --lang {lang!r}; use a bare language name.")
    import yaml

    config_path = base_dir / "sgconfig.yml"
    if os.path.lexists(config_path):
        raise FileExistsError(f"Project already initialized ({config_path} exists).")

    config_data = {
        "ruleDirs": ["rules"],
        "testDirs": ["tests"],
        "utilsDir": "utils",
        "language": lang,
    }

    base_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes_anchored(
        config_path, yaml.dump(config_data).encode("utf-8"), mode=0o644, replace=False
    )

    rules_dir = base_dir / "rules"
    tests_dir = base_dir / "tests"
    rules_dir.mkdir(exist_ok=True)
    tests_dir.mkdir(exist_ok=True)
    atomic_write_bytes_anchored(
        rules_dir / "sample-rule.yml",
        f"id: sample-rule\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n".encode(),
        mode=0o644,
        replace=False,
    )
    atomic_write_bytes_anchored(
        tests_dir / "sample-test.yml",
        b'id: sample-test\nruleId: sample-rule\nvalid:\n  - "pass"\ninvalid:\n'
        b'  - "print(\\"hello\\")"\n',
        mode=0o644,
        replace=False,
    )
    return config_path
