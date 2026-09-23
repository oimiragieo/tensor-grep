"""`tg new` project scaffold writer (relocated from ``main.py`` for the file-size ratchet)."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli._index_lock import atomic_write_bytes_anchored


def _write_ast_project_scaffold(base_dir: Path, lang: str) -> Path:
    # `lang` is interpolated into a hand-formatted YAML rule below; a newline would inject
    # sibling keys. Sibling `name` is already validated -- this closes the asymmetry without
    # rejecting real language spellings like `c++`/`c#`.
    if not lang.strip() or any(c in lang for c in "\r\n"):
        raise ValueError(f"Invalid --lang {lang!r}; use a bare language name.")
    import yaml

    config_path = base_dir / "sgconfig.yml"
    if config_path.exists():
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
