from __future__ import annotations

from tensor_grep.cli import bootstrap
from tensor_grep.cli.bootstrap_native_argv import bootstrap_native_tg_search_argv


def test_bootstrap_native_tg_search_argv_inserts_sentinel_for_dash_led_injection() -> None:
    argv = bootstrap_native_tg_search_argv(["--json", "-i", "-r"])
    assert argv == ["--json", "--", "-i", "-r"]


def test_bootstrap_native_tg_search_argv_inserts_before_dash_led_pattern() -> None:
    argv = bootstrap_native_tg_search_argv(["--cpu", "-pattern", "src"])
    assert argv == ["--cpu", "--", "-pattern", "src"]


def test_bootstrap_native_tg_search_argv_skips_plain_pattern_with_flags() -> None:
    argv = bootstrap_native_tg_search_argv(["-i", "ERROR", "."])
    assert argv == ["-i", "ERROR", "."]


def test_bootstrap_native_tg_search_argv_preserves_trailing_search_flags() -> None:
    argv = bootstrap_native_tg_search_argv(["foo", "sample.txt", "--count-matches"])
    assert argv == ["foo", "sample.txt", "--count-matches"]


def test_bootstrap_native_tg_search_argv_respects_existing_sentinel() -> None:
    given = ["--json", "--", "-i", "-r"]
    assert bootstrap_native_tg_search_argv(given) == given


def test_run_native_tg_search_emits_sentinel_before_positionals(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(
        bootstrap,
        "_streaming_passthrough_returncode",
        lambda argv, **_kw: captured.append(list(argv)) or 0,
    )

    assert bootstrap._run_native_tg_search("tg.exe", ["--json", "-i", "-r"]) == 0
    assert captured == [["tg.exe", "search", "--json", "--", "-i", "-r"]]
