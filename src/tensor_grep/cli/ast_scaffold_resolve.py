from __future__ import annotations

from pathlib import Path
from typing import cast

from tensor_grep.cli._main_binding import _self
from tensor_grep.cli.ast_scaffold import (
    _ast_project_config_data,
    _ast_project_default_language,
    _ast_test_scaffold_contents,
    _coerce_scaffold_config_language,
    _linked_ast_rule_language,
)
from tensor_grep.cli.ast_scan import _load_sg_project_config


def load_ast_scaffold_config(config: str, scaffold_kind: str) -> dict[str, object]:
    config_path = Path(config).resolve()
    raw_config = _ast_project_config_data(config_path)
    if not config_path.exists():
        return _load_sg_project_config(config)
    if raw_config is None:
        raise ValueError(f"YAML in {config_path} must be a mapping.")
    validated_language = _coerce_scaffold_config_language(raw_config.get("language"))
    return {
        "config_path": config_path,
        "root_dir": config_path.parent,
        "rule_dirs": _self._normalize_string_list(raw_config.get("ruleDirs"), ["rules"]),
        "test_dirs": _self._normalize_string_list(raw_config.get("testDirs"), ["tests"]),
        "utils_dir": str(raw_config.get("utilsDir") or "utils"),
        "language": validated_language,
    }


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
