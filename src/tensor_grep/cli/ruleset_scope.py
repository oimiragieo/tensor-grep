"""Prepare a bounded, language-aware scope for built-in ruleset scans."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.backends.ast_backend import normalize_ast_language
from tensor_grep.cli.ast_scan import (
    _AST_GREP_LANGUAGE_NAME_ENDINGS,
    _AST_GREP_LANGUAGE_SUFFIXES,
    _ast_grep_language_applies,
    _regex_rule_targets_file,
)
from tensor_grep.cli.rule_packs import list_rule_packs, resolve_rule_pack
from tensor_grep.core.config import SearchConfig

if TYPE_CHECKING:
    from tensor_grep.io.directory_scanner import DirectoryScanner


def _detected_language(path: str) -> str | None:
    name = Path(path).name.lower()
    suffix = Path(path).suffix.lower()
    for language, suffixes in _AST_GREP_LANGUAGE_SUFFIXES.items():
        if (
            suffix in suffixes
            or name in suffixes
            or name.endswith(_AST_GREP_LANGUAGE_NAME_ENDINGS.get(language, ()))
        ):
            return language
    from tensor_grep.cli.repo_map import _target_language_for_path

    return _target_language_for_path(path)


def _rule_applies(rule: dict[str, str], path: str) -> bool:
    if rule.get("engine") == "regex":
        return _regex_rule_targets_file(rule["language"], path)
    return _ast_grep_language_applies({rule["language"]}, path)


def _prepare_builtin_ruleset_scan(
    name: str,
    language: str | None,
    *,
    scan_paths: list[str],
    scan_globs: list[str] | None = None,
    scan_types: list[str] | None = None,
    scan_max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
    filter_regex: str | None = None,
) -> tuple[dict[str, object], list[dict[str, str]], tuple[list[str], DirectoryScanner]]:
    from tensor_grep.cli.main import _collect_candidate_files, _filter_ast_rule_specs
    from tensor_grep.cli.repo_map import _target_language_for_path
    from tensor_grep.cli.scan_guardrails import ensure_scan_not_broad
    from tensor_grep.io.directory_scanner import DirectoryScanner

    roots = [str(Path(path).expanduser().resolve()) for path in scan_paths]
    ensure_scan_not_broad(
        roots,
        globs=scan_globs or [],
        file_types=scan_types or [],
        max_depth=scan_max_depth,
        allow_broad_generated_scan=allow_broad_generated_scan,
    )
    scanner = DirectoryScanner(
        SearchConfig(
            ast=True,
            ast_prefer_native=True,
            lang=None,
            glob=scan_globs or None,
            file_type=scan_types or None,
            max_depth=scan_max_depth,
        )
    )
    candidates, _ = _collect_candidate_files(scanner, roots)
    detected = sorted({current for path in candidates if (current := _detected_language(path))})
    # Regex language eligibility can differ from parser dialects such as TSX.
    regex_languages = {
        current for path in candidates if (current := _target_language_for_path(path))
    }
    metadata_seed, _ = resolve_rule_pack(name)
    metadata = next(pack for pack in list_rule_packs() if pack["name"] == metadata_seed["name"])
    name = str(metadata_seed["name"])
    requested = normalize_ast_language(language) if language is not None else None
    supported = cast_list(metadata["languages"])
    selected_languages = (
        [requested]
        if requested
        else sorted((set(detected) | regex_languages).intersection(supported))
    )
    selection = "explicit" if requested else "inferred"
    if not requested and not selected_languages:
        unknown_regex = [path for path in candidates if _target_language_for_path(path) is None]
        default_language = str(metadata["default_language"])
        _default_meta, default_rules = resolve_rule_pack(name, default_language)
        if unknown_regex and any(rule.get("engine") == "regex" for rule in default_rules):
            selected_languages = [default_language]
            selection = "default-regex-unknown"
    selected: list[dict[str, str]] = []
    for selected_language in selected_languages:
        _pack_meta, language_rules = resolve_rule_pack(name, selected_language)
        selected.extend(
            rule
            for rule in language_rules
            if selection != "default-regex-unknown" or rule.get("engine") == "regex"
        )
    had_rules_before_filter = bool(selected)
    selected = _filter_ast_rule_specs(selected, filter_regex)
    applicable: list[dict[str, str]] = []
    applicable_paths: set[str] = set()
    for rule in selected:
        matching = [path for path in candidates if _rule_applies(rule, path)]
        if matching:
            applicable.append(rule)
            applicable_paths.update(matching)
    metadata_out: dict[str, object] = {
        "name": name,
        "config_path": f"builtin:{name}",
        "root_dir": Path(roots[0]),
        "rule_dirs": [],
        "test_dirs": [],
        "description": metadata["description"],
        "category": metadata["category"],
        "status": metadata["status"],
        "language": requested
        or (selected_languages[0] if selected_languages else str(metadata["default_language"])),
        "public_language": (
            "mixed"
            if len({rule["language"] for rule in applicable}) > 1
            else next(
                iter({rule["language"] for rule in applicable}),
                requested or str(metadata["default_language"]),
            )
        ),
        "languages": sorted({rule["language"] for rule in applicable}),
        "language_selection": selection,
        "rule_count": len(selected),
        "applicable_rule_count": len(applicable),
        "scope": {
            "paths": roots,
            "globs": scan_globs or [],
            "types": scan_types or [],
            "max_depth": scan_max_depth,
            "files_considered": len(candidates),
            "files_applicable": len(applicable_paths),
            "detected_languages": detected,
        },
        "refusal_reason": _refusal_reason(
            candidates,
            selected,
            applicable,
            detected,
            requested,
            filter_regex is not None and had_rules_before_filter,
        ),
    }
    return metadata_out, applicable, (candidates, scanner)


def cast_list(value: Any) -> list[str]:
    return [str(item) for item in value]


def _refusal_reason(
    candidates: list[str],
    selected: list[dict[str, str]],
    applicable: list[dict[str, str]],
    detected: list[str],
    requested: str | None,
    filter_applied: bool,
) -> str | None:
    if not candidates:
        return "empty_scope"
    if filter_applied and not selected:
        return "filter_selected_no_rules"
    if requested and detected and requested not in detected and not applicable:
        return "explicit_language_mismatch"
    if not applicable:
        return "no_applicable_files"
    return None
