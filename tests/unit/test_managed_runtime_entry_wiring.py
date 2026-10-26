"""Keep the native executable connected to the managed-runtime selector."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_MAIN = REPO_ROOT / "rust_core" / "src" / "main.rs"
_STARTUP_CALL = re.compile(
    r"(?m)^\s*#\[cfg\(windows\)\]\s*"
    r"let\s+_managed_runtime\s*=\s*"
    r"tensor_grep_rs::prepare_main_startup\(&raw_args\[1\.\.\]\)\?;"
)


def _main_inner(source: str) -> str:
    start = source.find("fn main_inner() -> anyhow::Result<()> {")
    end = source.find("\nfn print_native_top_level_help()", start)
    assert start >= 0 and end > start, "could not isolate native main_inner entrypoint"
    return source[start:end]


def test_source_fixture_contains_native_main_entrypoint() -> None:
    source = RUST_MAIN.read_text(encoding="utf-8")

    assert "let raw_args: Vec<OsString> = std::env::args_os().collect();" in source
    assert _main_inner(source).startswith("fn main_inner()")


def test_managed_runtime_preflight_precedes_native_command_routing() -> None:
    main = _main_inner(RUST_MAIN.read_text(encoding="utf-8"))
    startup = _STARTUP_CALL.search(main)
    first_route = main.find("if raw_args.len() <= 1")

    assert startup is not None, (
        "Windows main_inner must retain prepare_main_startup's runtime handle; "
        "otherwise tg.exe ignores the generation selected by the updater"
    )
    assert first_route > startup.start(), (
        "managed runtime selection must run before help, version, and command routing"
    )


def test_wiring_guard_rejects_missing_startup_call() -> None:
    main = _main_inner(RUST_MAIN.read_text(encoding="utf-8"))
    missing_call = _STARTUP_CALL.sub("", main)

    assert _STARTUP_CALL.search(main) is not None
    assert _STARTUP_CALL.search(missing_call) is None
