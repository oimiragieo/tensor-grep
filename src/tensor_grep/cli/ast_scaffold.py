"""`tg new` project scaffold writer (relocated from ``main.py`` for the file-size ratchet)."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import cast

import yaml

from tensor_grep.cli._index_lock import atomic_write_bytes_anchored

_AST_RULE_SCAFFOLD_EXAMPLES = {
    **dict.fromkeys(
        (
            "bash",
            "c",
            "cpp",
            "css",
            "elixir",
            "go",
            "haskell",
            "hcl",
            "html",
            "java",
            "kotlin",
            "lua",
            "nix",
            "php",
            "ruby",
            "scala",
            "solidity",
            "swift",
            "tsx",
            "yaml",
        ),
        ("TODO_REPLACE_ME", "OTHER_IDENTIFIER", "TODO_REPLACE_ME"),
    ),
    "csharp": ("TODO_REPLACE_ME()", "OTHER_IDENTIFIER()", "TODO_REPLACE_ME()"),
    "javascript": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "json": ('"TODO_REPLACE_ME"', '"OTHER_IDENTIFIER"', '"TODO_REPLACE_ME"'),
    "python": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "rust": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
    "typescript": ("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"),
}


class _DuplicateYamlKeyError(yaml.YAMLError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._checked_mappings: set[yaml.MappingNode] = set()
        self._nesting_depth = 0

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node:
        self._nesting_depth += 1
        try:
            if self._nesting_depth > 128:
                raise yaml.YAMLError("maximum YAML nesting depth exceeded")
            return cast(yaml.Node, super().compose_node(parent, index))
        finally:
            self._nesting_depth -= 1

    def _construct_yaml_12_bool(self, node: yaml.ScalarNode) -> bool | str:
        value = self.construct_scalar(node)
        if value.lower() in {"on", "off", "yes", "no"}:
            return value
        return super().construct_yaml_bool(node)

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        if node in self._checked_mappings:
            return
        self._checked_mappings.add(node)
        keys: set[Hashable] = set()
        construct = cast(Callable[..., object], self.construct_object)
        for key_node, _value_node in node.value:
            key: object = (
                "<<"
                if key_node.tag == "tag:yaml.org,2002:merge"
                else construct(key_node, deep=True)
            )
            if not isinstance(key, Hashable):
                raise yaml.constructor.ConstructorError(
                    None, None, "found unhashable key", key_node.start_mark
                )
            if key in keys:
                raise _DuplicateYamlKeyError(f"duplicate key {key!r}")
            keys.add(key)
        super().flatten_mapping(node)


_UniqueKeyLoader.add_constructor("tag:yaml.org,2002:bool", _UniqueKeyLoader._construct_yaml_12_bool)


def _ast_rule_scaffold_examples(lang: str) -> tuple[str, str, str]:
    from tensor_grep.backends.ast_backend import normalize_ast_language

    try:
        language = normalize_ast_language(lang)
    except ValueError as exc:
        raise ValueError(
            f"Cannot scaffold AST rule for language {json.dumps(lang)}; run `tg ast-info` for supported languages."
        ) from exc
    if language not in _AST_RULE_SCAFFOLD_EXAMPLES:
        raise ValueError(
            f"Cannot scaffold AST rule for language {json.dumps(lang)}: no validated pattern example is available for `tg test` on both front doors."
        )
    return _AST_RULE_SCAFFOLD_EXAMPLES[language]


def _ast_rule_scaffold_contents(name: str, lang: str) -> str:
    from tensor_grep.backends.ast_backend import normalize_ast_language

    pattern, _valid, _invalid = _ast_rule_scaffold_examples(lang)
    language = normalize_ast_language(lang)
    return f"id: {json.dumps(name, ensure_ascii=False)}\nlanguage: {language}\nrule:\n  pattern: {json.dumps(pattern, ensure_ascii=False)}\n"


def _ast_test_scaffold_contents(name: str, lang: str) -> str:
    _pattern, valid, invalid = _ast_rule_scaffold_examples(lang)
    quoted_name = json.dumps(name, ensure_ascii=False)
    return f"id: {quoted_name}\nruleId: {quoted_name}\nvalid:\n  - {json.dumps(valid, ensure_ascii=False)}\ninvalid:\n  - {json.dumps(invalid, ensure_ascii=False)}\n"


def _linked_ast_rule_language(
    rule_path: Path, default_language: str | Callable[[], str], explicit_language: str | None = None
) -> str:
    if explicit_language is not None:
        return explicit_language.strip().lower()
    if not os.path.lexists(rule_path):
        return default_language() if callable(default_language) else default_language
    label = f"{rule_path.parent.name}/{rule_path.name}"
    if rule_path.is_symlink() or not rule_path.is_file():
        raise ValueError(f"linked rule must be a regular file: {label}")
    try:
        rule = yaml.load(rule_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except _DuplicateYamlKeyError as exc:
        raise ValueError(f"cannot parse linked rule {label}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"cannot parse linked rule {label}: invalid YAML syntax") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"cannot parse linked rule {label}: invalid UTF-8") from exc
    if not isinstance(rule, dict):
        raise ValueError(f"cannot parse linked rule {label}: expected a YAML mapping")
    language = rule.get("language")
    if isinstance(language, str) and language.strip():
        return language.strip().lower()
    return default_language() if callable(default_language) else default_language


def _ast_project_default_language(base_dir: Path) -> str:
    config_path = base_dir / "sgconfig.yml"
    try:
        config_mode = config_path.stat().st_mode
    except FileNotFoundError:
        return "python"
    if not stat.S_ISREG(config_mode):
        raise ValueError("sgconfig.yml must be a regular file")
    try:
        config = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except _DuplicateYamlKeyError as exc:
        raise ValueError(f"cannot parse sgconfig.yml: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError("cannot parse sgconfig.yml: invalid YAML syntax") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("cannot parse sgconfig.yml: invalid UTF-8") from exc
    if not isinstance(config, dict):
        return "python"
    language = config.get("language")
    return language.strip().lower() if isinstance(language, str) and language.strip() else "python"


def _write_ast_project_scaffold(base_dir: Path, lang: str) -> Path:
    # `lang` is interpolated into a hand-formatted YAML rule below; a newline would inject
    # sibling keys. Sibling `name` is already validated -- this closes the asymmetry without
    # rejecting real language spellings like `c++`/`c#`.
    if not lang.strip() or any(c in lang for c in "\r\n"):
        raise ValueError(f"Invalid --lang {json.dumps(lang)}; use a bare language name.")
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
        f"id: \"sample-rule\"\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n".encode(),
        mode=0o644,
        replace=False,
    )
    atomic_write_bytes_anchored(
        tests_dir / "sample-test.yml",
        b'id: "sample-test"\nruleId: "sample-rule"\nvalid:\n  - "pass"\ninvalid:\n'
        b'  - "print(\\"hello\\")"\n',
        mode=0o644,
        replace=False,
    )
    return config_path
