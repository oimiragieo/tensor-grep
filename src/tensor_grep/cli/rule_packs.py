from __future__ import annotations

from typing import Any

_RULE_PACKS: dict[str, dict[str, Any]] = {
    "crypto-safe": {
        "description": "Preview crypto hygiene checks for weak or obsolete hashing primitives.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-hashlib-md5",
                    "pattern": "hashlib.md5($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of hashlib.md5.",
                },
                {
                    "id": "python-hashlib-sha1",
                    "pattern": "hashlib.sha1($$$ARGS)",
                    "language": "python",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of hashlib.sha1.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-createhash-md5",
                    "pattern": "crypto.createHash('md5')",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                },
                {
                    "id": "javascript-createhash-sha1",
                    "pattern": "crypto.createHash('sha1')",
                    "language": "javascript",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of sha1.",
                },
            ],
            "typescript": [
                {
                    "id": "typescript-createhash-md5",
                    "pattern": "crypto.createHash('md5')",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                },
                {
                    "id": "typescript-createhash-sha1",
                    "pattern": "crypto.createHash('sha1')",
                    "language": "typescript",
                    "severity": "high",
                    "message": "Prefer a collision-resistant hash instead of sha1.",
                },
            ],
            "rust": [
                {
                    "id": "rust-md5-compute",
                    "pattern": "md5::compute($EXPR)",
                    "language": "rust",
                    "severity": "high",
                    "message": "Prefer a modern password or integrity primitive instead of md5.",
                }
            ],
        },
    },
    "secrets-basic": {
        "description": "Preview rules for obvious hardcoded secret assignments.",
        "category": "security",
        "status": "preview",
        "default_language": "python",
        "languages": {
            "python": [
                {
                    "id": "python-hardcoded-password",
                    "pattern": 'password = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                },
                {
                    "id": "python-hardcoded-api-key",
                    "pattern": 'api_key = "$SECRET"',
                    "language": "python",
                    "severity": "medium",
                    "message": "Avoid hardcoding API key literals in source files.",
                },
            ],
            "javascript": [
                {
                    "id": "javascript-hardcoded-password",
                    "pattern": 'const password = "$SECRET"',
                    "language": "javascript",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                }
            ],
            "typescript": [
                {
                    "id": "typescript-hardcoded-password",
                    "pattern": 'const password = "$SECRET"',
                    "language": "typescript",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                }
            ],
            "rust": [
                {
                    "id": "rust-hardcoded-password",
                    "pattern": 'let password = "$SECRET";',
                    "language": "rust",
                    "severity": "medium",
                    "message": "Avoid hardcoding password literals in source files.",
                }
            ],
        },
    },
}


def list_rule_packs() -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for name, spec in sorted(_RULE_PACKS.items()):
        languages = sorted(spec["languages"].keys())
        rules = sum(len(entries) for entries in spec["languages"].values())
        packs.append(
            {
                "name": name,
                "description": spec["description"],
                "category": spec["category"],
                "status": spec["status"],
                "default_language": spec["default_language"],
                "languages": languages,
                "rule_count": rules,
            }
        )
    return packs


def resolve_rule_pack(name: str, language: str | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    normalized_name = name.strip().lower()
    if normalized_name not in _RULE_PACKS:
        available = ", ".join(pack["name"] for pack in list_rule_packs())
        raise ValueError(f"Unknown built-in ruleset '{name}'. Available rulesets: {available}.")

    spec = _RULE_PACKS[normalized_name]
    selected_language = (language or spec["default_language"]).strip().lower()
    raw_rules = spec["languages"].get(selected_language)
    if not raw_rules:
        supported = ", ".join(sorted(spec["languages"].keys()))
        raise ValueError(
            f"Ruleset '{normalized_name}' does not support language '{selected_language}'. "
            f"Supported languages: {supported}."
        )

    rules = [
        {
            "id": str(rule["id"]),
            "pattern": str(rule["pattern"]),
            "language": str(rule["language"]),
        }
        for rule in raw_rules
    ]
    metadata = {
        "name": normalized_name,
        "description": spec["description"],
        "category": spec["category"],
        "status": spec["status"],
        "language": selected_language,
        "rule_count": len(rules),
    }
    return metadata, rules
