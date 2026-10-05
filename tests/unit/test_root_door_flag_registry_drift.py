"""Drift guard: the native ROOT door must recognise every flag `tg search` accepts (J-03).

``tg PATTERN -e X FILE`` is rewritten to ``tg search ...`` only when a token matches the flag
registry in ``rust_core/src/search_flag_registry.rs``; otherwise the root clap parser
(``PositionalCli``) rejects the flag with exit 2 even though ``tg search`` accepts it. The two
doors must agree, so this test compares the registry to the Python front door's own
``_TG_ONLY_SEARCH_FLAGS``.

The second half of the drift guard (every flag ``rg --help`` documents) needs the real ``rg`` and
fails closed when CI demands it, so it lives in ``tests/e2e/test_native_exit_and_door_parity.py``,
the suite ``native-build-smoke`` runs with ``rg`` installed.

The flags ``PositionalCli`` itself declares are derived from its source (never hand-listed), so a
new clap flag cannot silently widen or narrow the exemption.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_RS = REPO_ROOT / "rust_core" / "src" / "search_flag_registry.rs"
MAIN_RS = REPO_ROOT / "rust_core" / "src" / "main.rs"


def _registry_flags() -> set[str]:
    text = REGISTRY_RS.read_text(encoding="utf-8")
    body = text.split("pub(crate) fn raw_args_contain_any_flag")[0]
    flags = set(re.findall(r'^\s*"(-{1,2}[\w-]+)",', body, re.M))
    assert len(flags) > 100, f"registry parse looks wrong ({len(flags)} flags)"
    return flags


def _root_clap_flags() -> set[str]:
    """Flags the root `PositionalCli` struct declares itself (short, long, alias, implicit long)."""
    text = MAIN_RS.read_text(encoding="utf-8")
    match = re.search(r"pub struct PositionalCli \{(.*?)\n\}\n", text, re.S)
    assert match is not None, "PositionalCli struct not found in rust_core/src/main.rs"
    flags: set[str] = set()
    for fm in re.finditer(r"#\[arg\(([^\]]*)\)\]\s*\n\s*pub (\w+):", match.group(1)):
        attrs, field = fm.groups()
        flags.update("-" + s for s in re.findall(r"short\s*=\s*'(\w)'", attrs))
        flags.update("--" + n for n in re.findall(r'(?:long|alias)\s*=\s*"([\w-]+)"', attrs))
        if re.search(r"\blong\b(?!\s*=)", attrs):
            flags.add("--" + field.replace("_", "-"))
    assert {"--json", "--ndjson", "-r", "--replace", "--cpu"} <= flags, flags
    return flags


def test_python_only_search_flags_are_recognised_by_the_native_root_door() -> None:
    from tensor_grep.cli import bootstrap

    registry = _registry_flags()
    root_clap = _root_clap_flags()
    missing = sorted(
        flag
        for flag in bootstrap._TG_ONLY_SEARCH_FLAGS
        if flag not in registry and flag not in root_clap
    )
    assert not missing, (
        f"bootstrap._TG_ONLY_SEARCH_FLAGS entries the native root door does not recognise: {missing}. "
        "Add them to SEARCH_OPTION_FIRST_FLAGS in rust_core/src/search_flag_registry.rs."
    )


def test_drift_parser_positive_controls() -> None:
    # The parsers must see flags we know exist today; a silent empty parse would make the drift
    # test vacuous.
    registry = _registry_flags()
    assert {"--glob", "-g", "--hidden", "--count-matches"} <= registry
    assert "--definitely-not-a-flag" not in registry
    assert {"--json", "--count", "-c"} <= _root_clap_flags()
