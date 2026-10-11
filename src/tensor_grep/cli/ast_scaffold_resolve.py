from __future__ import annotations

from pathlib import Path
from typing import cast

from tensor_grep.cli.ast_scaffold import (
    _ast_project_default_language,
    _ast_test_scaffold_contents,
    _linked_ast_rule_language,
)


def resolve_ast_test_scaffold(
    base_dir: Path, project_cfg: dict[str, object] | None, name: str, lang: str | None
) -> tuple[Path, str]:
    target_dir = (
        cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["test_dirs"])[0]
        if project_cfg is not None
        else base_dir / "tests"
    )
    target_path = target_dir / f"{name}.yml"
    rule_dir = (
        cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["rule_dirs"])[0]
        if project_cfg is not None
        else base_dir / "rules"
    )

    def default_language() -> str:
        return (
            cast(str, project_cfg["language"])
            if project_cfg is not None
            else _ast_project_default_language(base_dir)
        )

    rule_language = _linked_ast_rule_language(rule_dir / f"{name}.yml", default_language, lang)
    return target_path, _ast_test_scaffold_contents(name, rule_language)
