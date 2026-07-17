from __future__ import annotations

import importlib.metadata as importlib_metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer._completion_shared import get_completion_script
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap
from tensor_grep.cli import main as cli_main
from tensor_grep.cli.bootstrap import _KNOWN_COMMANDS
from tensor_grep.cli.commands import KNOWN_COMMANDS
from tensor_grep.cli.main import app


def test_bootstrap_commands_match_source_of_truth() -> None:
    assert _KNOWN_COMMANDS == set(KNOWN_COMMANDS), (
        "Bootstrap commands must exactly match KNOWN_COMMANDS"
    )


def test_codemap_argv_does_not_forward_to_search() -> None:
    """Registration site 1 (commands.py KNOWN_COMMANDS): a miss here would silently misroute
    `tg codemap` into a ripgrep search for the literal pattern "codemap" instead of the real
    command -- `_normalize_search_invocation` returning non-None is exactly that misrouting."""
    assert bootstrap._normalize_search_invocation(["codemap"]) is None
    assert bootstrap._normalize_search_invocation(["codemap", "--json"]) is None
    assert bootstrap._normalize_search_invocation(["codemap", "--check"]) is None


def test_vendored_root_dir_names_match_source_of_truth() -> None:
    """Review finding L1 (PR #400): cli/bootstrap.py's front-door vendored-root mirror and
    cli/main.py's `_should_refuse_unbounded_vendored_root_scan` guard must trigger on
    exactly the same set of heavy top-level dir names, or the two front doors (native/rg
    fast path vs full CLI) can disagree about whether a root is unbounded.

    perf/#48: bootstrap.py no longer binds `_UNBOUNDED_VENDORED_ROOT_DIR_NAMES` as a
    persistent module attribute -- `_search_paths_include_vendored_root` now does its own
    function-local `from tensor_grep.io.directory_scanner import UNBOUNDED_VENDORED_ROOT_DIR_NAMES`
    so that heavy import (which transitively pulls in `tensor_grep.core.config` and stdlib
    `dataclasses`/`inspect`) is not paid by the `tg --version` fast path
    (`tests/unit/test_bootstrap_fast_path_imports.py` pins that). Structurally this makes
    bootstrap.py's copy DRIFT-PROOF -- it always re-reads the canonical set fresh, it can no
    longer hold a stale independent copy -- so this test now (a) checks cli/main.py's own
    still-module-level copy against the canonical source directly, and (b) behaviorally proves
    bootstrap's guard function actually consults that same canonical set."""
    from tensor_grep.io.directory_scanner import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

    assert cli_main._UNBOUNDED_VENDORED_ROOT_DIR_NAMES == UNBOUNDED_VENDORED_ROOT_DIR_NAMES, (
        "cli/main.py's vendored-root trigger set must match the canonical source of truth exactly"
    )
    assert UNBOUNDED_VENDORED_ROOT_DIR_NAMES, (
        "canonical set must be non-empty for this test to mean anything"
    )


def test_vendored_root_guard_triggers_on_every_canonical_name(tmp_path: Path) -> None:
    """Companion behavioral check to the drift test above: bootstrap's front-door guard must
    fire for a root whose top-level child is ANY name in the canonical
    `UNBOUNDED_VENDORED_ROOT_DIR_NAMES` set, and must NOT fire for an unrelated child name --
    proving the perf/#48 function-local import actually wires the guard to the real set rather
    than silently going stale/empty."""
    from tensor_grep.io.directory_scanner import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

    for name in UNBOUNDED_VENDORED_ROOT_DIR_NAMES:
        root = tmp_path / f"root-{name}"
        (root / name).mkdir(parents=True)
        assert bootstrap._search_paths_include_vendored_root([str(root)]) is True, (
            f"guard must trigger on canonical vendored dir name {name!r}"
        )

    unrelated_root = tmp_path / "root-unrelated"
    (unrelated_root / "not_a_vendored_dir").mkdir(parents=True)
    assert bootstrap._search_paths_include_vendored_root([str(unrelated_root)]) is False


def test_typer_app_commands_match_source_of_truth() -> None:
    typer_commands = set()
    for cmd in app.registered_commands:
        typer_commands.add(cmd.name or cmd.callback.__name__)  # type: ignore
    for group in app.registered_groups:
        typer_commands.add(group.name)  # type: ignore

    expected_typer_cmds = {cmd for cmd in KNOWN_COMMANDS if not cmd.startswith("__")}
    assert typer_commands == expected_typer_cmds, (
        "Typer commands must exactly match public KNOWN_COMMANDS"
    )


def test_rust_core_uses_source_of_truth() -> None:
    rust_main = Path(__file__).resolve().parents[2] / "rust_core" / "src" / "main.rs"
    content = rust_main.read_text(encoding="utf-8")
    assert 'include_str!("../../src/tensor_grep/cli/commands.py")' in content, (
        "Rust core must include commands.py as source of truth"
    )


def test_main_entry_run_with_semantic_options_uses_ast_workflow_not_native(monkeypatch):
    # Regression: `tg run` with ast-grep semantic options must be served by the
    # in-process Python AST workflow. Delegating to the native binary causes an
    # infinite native<->python delegation loop (the historical
    # `tg run --strictness/--selector/--stdin/--globs` hang), because the native
    # handler bounces these options back to `python -m tensor_grep run ...`.
    for option in (
        ["--selector", "function_definition"],
        ["--selector=call"],
        ["--strictness", "ast"],
        ["--strictness=ast"],
        ["--stdin"],
        ["--globs", "*.py"],
        ["--globs=*.py"],
    ):
        seen: dict[str, object] = {}

        def record_workflow(argv: list[str], *, current_seen: dict[str, object] = seen) -> None:
            current_seen["argv"] = list(argv)

        monkeypatch.setattr(sys, "argv", ["tg", "run", "--pattern", "def $N($$$A): $$$B", *option])
        monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
        monkeypatch.setattr(
            bootstrap,
            "_run_native_tg_command",
            lambda *_a, **_k: pytest.fail("semantic run must not delegate to native"),
        )
        monkeypatch.setattr(bootstrap, "_run_ast_workflow_cli", record_workflow)
        bootstrap.main_entry()
        assert seen.get("argv", [None])[0] == "run", option


def test_main_entry_plain_run_still_uses_native_fast_path(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(sys, "argv", ["tg", "run", "def $N($$$A): $$$B", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_ast_workflow_cli",
        lambda *_a, **_k: pytest.fail("plain run should use the native fast path"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_command",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()
    assert excinfo.value.code == 0
    assert seen["argv"] == ["run", "def $N($$$A): $$$B", "."]


def test_main_entry_should_passthrough_search_subcommand_to_rg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["-i", "ERROR", "."]}


def test_main_entry_should_not_passthrough_unbounded_generated_root_search(
    monkeypatch, tmp_path: Path
) -> None:
    called = {"full_cli": False}
    root = tmp_path / "home"
    root.mkdir()
    (root / "AppData").mkdir()

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "-q", "foo", str(root), "--hidden", "--no-ignore"],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native passthrough should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_passthrough_unbounded_workspace_root_search(
    monkeypatch, tmp_path: Path
) -> None:
    called = {"full_cli": False}
    root = tmp_path / "projects"
    for name in ("one", "two", "three"):
        child = root / name
        child.mkdir(parents=True)
        (child / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "search", "foo", str(root)])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native passthrough should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_passthrough_marked_workspace_root_with_many_marked_children(
    monkeypatch, tmp_path: Path
) -> None:
    """Item #154: reported repro is an unscoped `tg search "def main" <root> --json` from a
    multi-root workspace parent that ALSO carries its own top-level project marker (a real
    example: a workspace dir with a top-level `package.json`, like `C:/dev/projects`) --
    `_search_paths_include_workspace_root` used to skip any root with its own marker
    unconditionally, so this exact shape always fell through to an unbounded native/rg walk
    (the reported 60s timeout) instead of refusing fast. A marked root must still refuse once
    it has enough independently-marked children (the higher marked-root threshold)."""
    called = {"full_cli": False}
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    for index in range(8):
        child = root / f"project-{index}"
        child.mkdir()
        (child / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "search", "def main", str(root), "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native passthrough should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_passthrough_single_project_root_with_top_level_vendored_dir(
    monkeypatch, tmp_path: Path
) -> None:
    """Critical unscoped-search-hang fix C, bootstrap front-door half: a root that is
    itself a single project (so `_search_paths_include_workspace_root` never flags it) but
    has a heavy vendored dir (e.g. a committed Go `vendor/`) at its own top level must not
    be fast-pathed straight into the native binary or rg passthrough -- both bypass
    cli/main.py's Python guards and backends/cpu_backend.py's wall-clock deadline
    entirely. It must fall through to the full CLI, which owns the actual refusal.

    Uses `vendor/` (not `node_modules/`, review finding H1): `node_modules` is already
    walker-skipped by `DirectoryScanner`, so it no longer forces this fallthrough -- see
    `test_main_entry_should_fast_path_repo_root_with_node_modules` below."""
    called = {"full_cli": False}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (root / "vendor").mkdir()

    monkeypatch.setattr(sys, "argv", ["tg", "search", "foo", str(root), "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native passthrough should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_native_delegate_bare_type_filter_with_json_trigger_from_vendored_root(
    monkeypatch, tmp_path: Path
) -> None:
    """#88-parity fix: `_search_args_include_generated_scan_bound` used to treat
    `-t`/`-g`/`--type`/`--glob` as an UNCONDITIONAL scan bound, unlike cli/main.py's
    already-fixed `_has_walk_scope_bound` (~4734, the original #88 fix), which only
    counts them as a bound when an explicit PATH was also given. Without that
    distinction, a bare `tg search PAT -t py --json` (no PATH, from a vendored/workspace
    root) slipped past `_search_args_include_unbounded_broad_scan`'s refusal straight
    into native delegation with a "supported trigger" flag (`--json`/`--cpu`/`--ndjson`/
    `--gpu-device-ids`) riding along -- `_can_delegate_to_native_tg_search` does not
    itself re-check walk scope, so this was a real unbounded-native-walk resurrection of
    #88, not merely a theoretical gap.

    NOTE: a bare `tg search PAT -t py` with NO trigger flag never reaches this branch --
    it is (accidentally) still caught by `_requires_full_cli`'s `_TG_ONLY_SEARCH_FLAGS`
    membership check, which forces it to the full CLI by a wholly separate mechanism.
    That incidental protection does not apply once a trigger flag routes execution into
    `_can_delegate_to_native_tg_search`'s OR-branch, which is why this test rides `--json`
    alongside `-t py` -- exactly the shape a JSON-emitting agent caller would send.
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (root / "vendor").mkdir()

    called = {"full_cli": False}
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "pat", "-t", "py", "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native delegation should not run (#88-parity)"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run (#88-parity)"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_native_delegate_explicit_dot_path_with_type_filter_and_json_trigger(
    monkeypatch, tmp_path: Path
) -> None:
    """Companion to the #88-parity fix above, proving it does not over-refuse: an
    EXPLICIT `.` path is a deliberate, scoped root, so `-t py` alongside it IS a
    legitimate walk-scope bound -- mirrors cli/main.py's `_has_walk_scope_bound`, which
    only exempts glob/type from counting as a bound when `paths_defaulted` is True (no
    explicit path). Same vendored-root fixture as the refusal test above; the only
    difference is the explicit `.` positional."""
    seen: dict[str, object] = {}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (root / "vendor").mkdir()

    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "pat", ".", "-t", "py", "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen["argv"] == ["pat", ".", "-t", "py", "--json"]


def test_main_entry_should_native_delegate_bare_max_depth_with_json_trigger(
    monkeypatch, tmp_path: Path
) -> None:
    """Second companion to the #88-parity fix: `-d`/`--max-depth` genuinely bounds HOW
    FAR the walk descends, so it stays an UNCONDITIONAL scan bound (mirrors
    cli/main.py's `_has_walk_scope_bound`, which returns True for
    `config.max_depth is not None` regardless of `paths_defaulted`) -- a bare `-d 3`
    with no explicit path must remain on the fast native path, unlike `-t`/`-g` without
    one. Same vendored-root fixture; only the flag changes."""
    seen: dict[str, object] = {}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/repo\n", encoding="utf-8")
    (root / "vendor").mkdir()

    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "pat", "-d", "3", "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen["argv"] == ["pat", "-d", "3", "--json"]


def test_main_entry_should_fast_path_repo_root_with_node_modules(
    monkeypatch, tmp_path: Path
) -> None:
    """Non-regression for review finding H1 (PR #400): `node_modules` is already
    walker-skipped by `DirectoryScanner` (and normally `.gitignore`d + bounded by Fix B's
    per-file deadline even if walked), so its mere presence at a repo root must not force
    the front door to fall through to the full CLI -- the native fast path may still be
    taken for an ordinary Node/React repo."""
    seen: dict[str, object] = {}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / "node_modules").mkdir()

    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root), "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen["argv"] == ["needle", str(root), "--json"]


def test_main_entry_still_uses_native_fast_path_for_normal_small_repo_root(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root), "--json"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen["argv"] == ["needle", str(root), "--json"]


def test_main_entry_should_passthrough_raw_rg_style_invocation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["-i", "ERROR", "."]}


# NOTE: `-t js` (and the other walk-scope filters -g/-T/--type/--glob/--iglob) used to be
# listed here as an rg-passthrough case, but they now route to the full CLI so the unbounded
# implicit-path walk guard can fire on a bare (no-PATH) filter (bug #88 walk-DoS). `-g`/`--glob`
# already routed to the full CLI on main; `-t`/`-T`/`--type`/`--type-not` were made consistent
# with them. The routing is now pinned directly at test_requires_full_cli_routes_every_walk_scope_filter_form.
# This test keeps a NON-walk-scope option-first shortcut (`--count-matches`) that still passes through.
@pytest.mark.parametrize(
    ("argv", "expected_search_args"),
    [
        (
            ["tg", "--count-matches", "ERROR", "."],
            ["--count-matches", "ERROR", "."],
        ),
    ],
)
def test_main_entry_should_passthrough_option_first_root_search_flags(
    monkeypatch, argv: list[str], expected_search_args: list[str]
):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": expected_search_args}


def test_main_entry_should_strip_noop_rg_format_for_rg_passthrough(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "--format", "rg", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["ERROR", "."]}


def test_main_entry_should_preserve_explicit_rg_json_for_rg_passthrough(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--format", "rg", "--json", "ERROR", "."],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["--json", "ERROR", "."]}


def test_main_entry_should_route_tg_only_flag_with_explicit_rg_json_to_full_cli(monkeypatch):
    # Audit #8: `--format rg --json` is a fast-path signal meaning "give me raw ripgrep
    # JSON Lines", but when a TG-only flag like --cpu rides along, the real `rg` binary
    # does not understand it and dies outright ("unrecognized flag --cpu"). The combo must
    # route to the full CLI, not be blindly forwarded to rg passthrough (or to the native
    # tg binary, which would silently ignore the explicit `--format rg` request).
    called = {"full_cli": False}

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--cpu", "--format", "rg", "--json", "ERROR", "."],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native tg should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_route_rank_flag_with_explicit_rg_json_to_full_cli(monkeypatch):
    # Same failure class as above but for --rank (audit #8's other named example).
    called = {"full_cli": False}

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--rank", "--format", "rg", "--json", "ERROR", "."],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_requires_full_cli_ignoring_rg_json_only_exempts_json() -> None:
    # Unit-level pin for the audit #8 helper: bare --json is exempt (rg understands it
    # natively), but any OTHER TG-only flag riding along still forces the full CLI.
    assert not bootstrap._requires_full_cli_ignoring_rg_json(["--json", "ERROR", "."])
    assert bootstrap._requires_full_cli_ignoring_rg_json(["--json", "--cpu", "ERROR", "."])
    assert bootstrap._requires_full_cli_ignoring_rg_json(["--json", "--force-cpu", "ERROR", "."])
    assert bootstrap._requires_full_cli_ignoring_rg_json(["--json", "--rank", "ERROR", "."])
    assert bootstrap._requires_full_cli_ignoring_rg_json([
        "--json",
        "--gpu-device-ids=0",
        "ERROR",
        ".",
    ])


def test_requires_full_cli_routes_every_walk_scope_filter_form() -> None:
    """DIRECT bootstrap-routing guard for the bug #88 walk-DoS class (re-gate BLOCK #2/#3).

    Every form of a walk-scope filter (-g/--glob/--iglob/-t/--type/-T/--type-not) that narrows
    WHICH files match but not the WALK must route to the full CLI, where the unbounded-walk
    guard fires. This exercises ``_requires_full_cli`` DIRECTLY -- the parametrized cases in
    test_cli_modes use ``CliRunner().invoke(app, ...)``, which enters the Typer app past the
    bootstrap front door (the CliRunner trap in AGENTS.md) and would stay green even if this
    routing were reverted; they do NOT guard the fix. This does.

    Audit #100: the ``-e``-combined cases below pin that ``-e``/``--regexp`` riding alongside a
    walk-scope filter is caught identically to the positional-pattern form -- ``_requires_full_cli``
    scans every token for a walk-scope flag regardless of how the pattern itself was supplied, so
    pip installs were never exposed to the native-frontdoor ``-e`` bypass audit #100 found on the
    standalone binary (that bypass was native-binary-direct only; see
    ``docs/plans/design-tensor-grep-100-walk-ceiling-hoist-2026-07-10.md``). These cases close the
    test-matrix gap so that fact is pinned, not just asserted in a design doc.
    """
    must_route = [
        ["-t", "py"],
        ["--type", "py"],
        ["-T", "py"],
        ["--type-not", "py"],
        ["-g", "*.py"],
        ["--glob", "*.py"],
        ["--iglob", "*.py"],
        ["--type=py"],
        ["--type-not=py"],
        ["--glob=*.py"],
        ["--iglob=*.py"],
        ["-tpy"],  # bundled attached-value short forms (rg idiom)
        ["-Tpy"],
        ["-g*.py"],
        ["-gsrc/**/*.py"],
        ["-itpy"],  # mid-bundle: -i then -t py
        ["-ig*.py"],  # mid-bundle: -i then -g *.py
        # -e/--regexp-combined forms (audit #100 test-matrix gap):
        ["-e", "TODO", "-t", "py"],
        ["-e", "TODO", "--type", "py"],
        ["-e", "TODO", "-g", "*.py"],
        ["-e", "TODO", "--glob", "*.py"],
        ["-e", "TODO", "--glob=*.py"],
        ["--regexp", "TODO", "--iglob", "*.py"],
        ["-e", "TODO", "-tpy"],  # bundled attached-value short form + -e
    ]
    for args in must_route:
        assert bootstrap._requires_full_cli(args), f"walk-scope form not routed to full CLI: {args}"

    # NON-walk-scope value-consuming short flags must NOT be over-routed: their leading
    # value-consumer swallows the remainder, so a g/t inside the value is data, not a flag.
    must_not_route = [
        ["-C3"],
        ["-m5"],
        ["-A2"],
        ["-fpat.txt"],
        ["-jtpy"],  # -j (threads) consumes "tpy" -- not a type filter
        ["-ftpy"],  # -f (file) consumes "tpy"
        ["-in"],  # pure boolean cluster
        ["TODO", "src"],  # plain pattern + path
    ]
    for args in must_not_route:
        assert not bootstrap._requires_full_cli(args), f"non-scope search over-routed: {args}"


def test_search_args_paths_defaulted_distinguishes_explicit_dot_from_no_path() -> None:
    """RAW-arg positional predicate (#88-parity fix) mirroring cli/main.py's
    `paths_defaulted = not args[1:]` (~7262). Must NOT be derived from
    `_search_path_args`, whose `paths or ["."]` fallback collapses "no path given" and
    an explicit "." into the identical `["."]` -- exactly the distinction this predicate
    exists to preserve."""
    assert bootstrap._search_args_paths_defaulted(["pat", "-t", "py"]) is True
    assert bootstrap._search_args_paths_defaulted(["pat", ".", "-t", "py"]) is False
    assert bootstrap._search_args_paths_defaulted(["pat", "-d", "3"]) is True
    assert bootstrap._search_args_paths_defaulted(["pat", "src", "-t", "py"]) is False
    # -e/--regexp-supplied pattern: the first positional after it is a real PATH, not
    # the pattern (the pattern was already consumed by -e's value).
    assert bootstrap._search_args_paths_defaulted(["-e", "pat", "src"]) is False
    assert bootstrap._search_args_paths_defaulted(["-e", "pat"]) is True
    assert bootstrap._search_args_paths_defaulted([]) is True


def test_search_args_include_generated_scan_bound_splits_unconditional_from_path_conditional() -> (
    None
):
    """Council fix (#88-parity): `-d`/`--max-depth`/`--maxdepth` stay an UNCONDITIONAL
    bound regardless of `paths_defaulted` (mirrors cli/main.py's `_has_walk_scope_bound`
    returning True for `config.max_depth is not None` unconditionally); `-g`/`-t`/`-T`/
    `--glob`/`--iglob`/`--type`/`--type-not` become PATH-CONDITIONAL -- a bound only when
    `paths_defaulted=False` (an explicit PATH was also supplied)."""
    # max-depth forms: a bound with or without an explicit path.
    for args in (["-d", "3"], ["--max-depth", "3"], ["--maxdepth", "3"], ["-d3"]):
        assert bootstrap._search_args_include_generated_scan_bound(args, paths_defaulted=True), (
            f"{args} must be an unconditional bound (no path)"
        )
        assert bootstrap._search_args_include_generated_scan_bound(args, paths_defaulted=False), (
            f"{args} must be an unconditional bound (explicit path)"
        )

    # type/glob forms: a bound ONLY when an explicit path was given.
    for args in (
        ["-t", "py"],
        ["--type", "py"],
        ["-T", "py"],
        ["--type-not", "py"],
        ["-g", "*.py"],
        ["--glob", "*.py"],
        ["--iglob", "*.py"],
        ["--type=py"],
        ["--glob=*.py"],
        ["-tpy"],
        ["-g*.py"],
    ):
        assert not bootstrap._search_args_include_generated_scan_bound(
            args, paths_defaulted=True
        ), f"{args} must NOT be a bound with no explicit path (#88-parity)"
        assert bootstrap._search_args_include_generated_scan_bound(args, paths_defaulted=False), (
            f"{args} must be a bound once an explicit path is given"
        )


def test_search_args_include_unbounded_broad_scan_refuses_bare_type_filter_from_vendored_root(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end (within bootstrap.py, no main_entry monkeypatching) proof that the
    paths_defaulted split above actually changes
    `_search_args_include_unbounded_broad_scan`'s verdict against a real pathological
    root: a bare `-t py` (no path) must now be flagged as an unbounded broad scan from a
    vendored root, an explicit "." must not, and a bare max-depth must not."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "vendor").mkdir()
    monkeypatch.chdir(root)

    assert bootstrap._search_args_include_unbounded_broad_scan(["pat", "-t", "py"]) is True
    assert bootstrap._search_args_include_unbounded_broad_scan(["pat", ".", "-t", "py"]) is False
    assert bootstrap._search_args_include_unbounded_broad_scan(["pat", "-d", "3"]) is False


def test_main_entry_should_strip_noop_rg_format_and_keep_sort_for_rg_passthrough(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--format=rg", "--sort", "path", "ERROR", "."],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "rg", "search_args": ["--sort", "path", "ERROR", "."]}


def test_main_entry_should_keep_non_rg_format_on_full_cli(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "--format=json", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_tg_specific_flags(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--debug"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_generate(monkeypatch) -> None:
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "--generate", "complete-bash"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_scan_inline_rules(monkeypatch) -> None:
    called = {"full_cli": False}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tg",
            "scan",
            "--inline-rules",
            "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)",
            "--path",
            ".",
        ],
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_ast_workflow_cli",
        lambda _argv: pytest.fail("ast workflow fast path should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


@pytest.mark.parametrize(
    "rule_args",
    [
        ["--rule", "rules/no-print.yml"],
        ["--rule=rules/no-print.yml"],
        ["-r", "rules/no-print.yml"],
    ],
)
def test_main_entry_should_fallback_to_full_cli_for_scan_rule_file(
    monkeypatch, rule_args: list[str]
) -> None:
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "scan", *rule_args, "src"])
    monkeypatch.setattr(
        bootstrap,
        "_run_ast_workflow_cli",
        lambda _argv: pytest.fail("ast workflow fast path should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_preserves_files_mode_without_pattern(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("print(1)\n", encoding="utf-8")
    (project / "b.py").write_text("print(2)\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "--files", str(project)])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    captured = capsys.readouterr()

    assert excinfo.value.code == 0
    assert sorted(captured.out.strip().splitlines()) == sorted([
        str(project / "a.py"),
        str(project / "b.py"),
    ])


def test_main_entry_should_fallback_to_full_cli_for_glob_flag(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--glob", "dir/*.txt"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_delegate_tg_specific_flags_even_when_rust_first_env_is_enabled(
    monkeypatch,
):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--debug"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("native tg should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_delegate_cpu_flag_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--cpu"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--cpu"]}


def test_main_entry_should_delegate_force_cpu_alias_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--force-cpu"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--force-cpu"]}


def test_main_entry_explicit_gpu_device_ids_without_gpu_backend_exits_cleanly(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Task #166 finding A (dogfood-caught on the v1.74.1 wheel): `tg PATTERN PATH
    --gpu-device-ids 0` on a machine with no GPU backend available (no CuDF/Torch) must exit
    with a clean, single-line `Error: ...` message and exit code 2 -- never a raw Python
    traceback. `Pipeline.__init__` deliberately raises `ConfigurationError` as its fail-closed
    explicit-GPU-routing contract (core/pipeline.py's
    `_raise_explicit_gpu_configuration_error`), but nothing at the CLI boundary
    (`search_command` in cli/main.py) caught it, so it propagated straight through Typer's
    `app()` call in `main_entry` as an unhandled exception -- confirmed live via the real
    console script before this fix: exit code 1 and a raw
    `tensor_grep.core.pipeline.ConfigurationError` traceback on stderr.

    Explicitly forces the "chunk plan found, but neither CuDF nor Torch is available" branch
    (the exact shape from the dogfood report) so this test is deterministic regardless of
    whether the host machine happens to have real GPU hardware/drivers.
    """
    target = tmp_path / "f.txt"
    target.write_text("hello world\n", encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv", ["tg", "search", "hello", str(target), "--gpu-device-ids", "0"]
    )
    # Two independent native-delegation gates exist -- bootstrap.py's fast argv-based
    # pre-check AND cli/main.py's OWN second check inside search_command (cli/main.py:6905,
    # imported separately at cli/main.py:36) -- so both must be forced off, or a real in-tree
    # `rust_core/target/{debug,release}/tg[.exe]` (e.g. left over from a local `maturin
    # develop`/`cargo build`) lets the second gate silently delegate to the native binary and
    # this test would exercise the Rust CLI's own GPU-fallback behavior instead of the Python
    # ConfigurationError path this fix targets.
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.core.hardware.memory_manager.MemoryManager.get_device_chunk_plan_mb",
        lambda self, preferred_ids=None: [(0, 512)],
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.CuDFBackend.is_available", lambda self: False)
    monkeypatch.setattr(
        "tensor_grep.backends.torch_backend.TorchBackend.is_available", lambda self: False
    )

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    captured = capsys.readouterr()
    assert excinfo.value.code == 2, (captured.out, captured.err)
    assert "Traceback" not in captured.err, captured.err
    assert "Traceback" not in captured.out, captured.out
    assert "error" in captured.err.lower(), captured.err
    assert "GPU" in captured.err, captured.err


def test_main_entry_should_delegate_force_cpu_env_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", "."])
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--cpu"]}


def test_main_entry_should_insert_forced_cpu_before_user_sentinel_for_native_tg(monkeypatch):
    # Audit #11: TG_FORCE_CPU=1 with a user `--` sentinel (tg's own recommended hardening
    # for a pattern that looks like a flag, e.g. `tg search -- '-pattern'`) must not append
    # the forced --cpu AFTER the sentinel -- that would both silently defeat force-CPU (the
    # token is no longer parsed as a flag) and inject a bogus `--cpu` positional path arg
    # alongside the user's own pattern/paths.
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "--", "-pattern", "src"])
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "binary_name": "tg.exe",
        "search_args": ["--cpu", "--", "-pattern", "src"],
    }


def test_effective_native_tg_search_args_inserts_before_sentinel(monkeypatch) -> None:
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    assert bootstrap._effective_native_tg_search_args(["--", "-pattern"]) == [
        "--cpu",
        "--",
        "-pattern",
    ]
    # No sentinel present: preserve the pre-existing append-at-end behavior.
    assert bootstrap._effective_native_tg_search_args(["ERROR", "."]) == [
        "ERROR",
        ".",
        "--cpu",
    ]
    # Already-explicit --cpu/--force-cpu short-circuits before the sentinel is even
    # considered (unchanged pre-existing behavior).
    assert bootstrap._effective_native_tg_search_args(["--cpu", "--", "-pattern"]) == [
        "--cpu",
        "--",
        "-pattern",
    ]


def test_main_entry_should_delegate_plain_search_to_native_tg_when_rust_first_env_is_enabled(
    monkeypatch,
):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "-i", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["-i", "ERROR", "."]}


def test_main_entry_should_not_rust_first_delegate_broad_claude_root(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "safeParseJSON", ".claude"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("broad .claude search needs Python guardrails"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("broad .claude search needs Python guardrails"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_rust_first_delegate_invalid_regex(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "(", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("invalid regex needs CLI diagnostics"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_delegate_path_first_invalid_regexp(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "src", "--regexp", "("])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("flagged invalid regex needs CLI diagnostics"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("flagged invalid regex needs CLI diagnostics"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_not_delegate_invalid_regex_after_sentinel(monkeypatch):
    # Audit #24: `_regex_patterns_from_search_args` must honor the `--` sentinel the same
    # way `_search_path_args` already does. Before this fix, a pattern passed after `--`
    # that looks like a flag (an unbalanced-paren regex starting with `-`) fell through the
    # `arg.startswith("-")` branch and was silently dropped as an "unrecognized option", so
    # the invalid-regex guard never saw it and the combo slipped past to rg passthrough
    # instead of getting tg's structured invalid-regex/PCRE2-fallback diagnostics.
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "--", "-(unbalanced", "src"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail("flagged invalid regex needs CLI diagnostics"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("flagged invalid regex needs CLI diagnostics"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_regex_patterns_from_search_args_respects_double_dash_sentinel() -> None:
    # Content after a user `--` sentinel is positional -- the first token is the bare
    # pattern even when it looks like a flag.
    assert bootstrap._regex_patterns_from_search_args(["--", "-(unbalanced"]) == ["-(unbalanced"]
    assert bootstrap._regex_patterns_from_search_args(["--", "-(unbalanced", "src"]) == [
        "-(unbalanced"
    ]
    # -e/--regexp before the sentinel still takes precedence over the positional pattern.
    assert bootstrap._regex_patterns_from_search_args(["-e", "foo", "--", "-(bad"]) == ["foo"]
    # No sentinel present: unchanged pre-existing behavior.
    assert bootstrap._regex_patterns_from_search_args(["ERROR", "src"]) == ["ERROR"]


def test_main_entry_should_delegate_cpu_flag_to_env_override_native_tg(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    native_binary = tmp_path / "tg.exe"
    native_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--cpu"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": str(native_binary), "search_args": ["ERROR", ".", "--cpu"]}


def test_main_entry_should_delegate_ndjson_flag_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", ".", "--ndjson"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {"binary_name": "tg.exe", "search_args": ["ERROR", ".", "--ndjson"]}


def test_main_entry_should_delegate_ndjson_multi_root_to_native_tg(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "ERROR", "src", "tests", "docs", "--ndjson"],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: (
            seen.update({"binary_name": binary_name, "search_args": list(search_args)}) or 0
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "binary_name": "tg.exe",
        "search_args": ["ERROR", "src", "tests", "docs", "--ndjson"],
    }


def test_root_cli_should_generate_powershell_completion_script(monkeypatch) -> None:
    monkeypatch.setenv("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", "1")
    result = CliRunner().invoke(app, ["--show-completion", "powershell"], prog_name="tg")

    assert result.exit_code == 0
    assert result.stdout.strip() == get_completion_script(
        prog_name="tg",
        complete_var="_TG_COMPLETE",
        shell="powershell",
    )


def test_root_help_should_surface_current_agent_gpu_launcher_and_validation_contracts() -> None:
    result = CliRunner().invoke(app, ["--help"], prog_name="tg")

    assert result.exit_code == 0
    help_text = result.stdout
    for expected in [
        'tg agent PATH "change invoice tax"',
        "alternative targets",
        "validation_commands",
        "$file",
        "--format rg --sort path",
        "--allow-broad-generated-scan",
        "--gpu-device-ids",
        "gpu_acceleration",
        "sidecar-routed GPU results",
        "GPU",
        "remains experimental",
        "TENSOR_GREP_CLASSIFY_PROVIDER=cybert",
        "--smart-case",
        "--hidden",
        "--max-depth",
        "--text",
        "native GPU falls back",
        "TG_NATIVE_TG_BINARY",
        "TG_SIDECAR_PYTHON",
        "TG_RG_PATH",
        "tg doctor --json",
        "path_tg_first_launcher_kind",
        "fresh_shell_path_tg_first_launcher_kind",
    ]:
        assert expected in help_text


def test_main_entry_should_fallback_to_full_cli_for_show_completion(monkeypatch) -> None:
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "--show-completion", "powershell"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda *_args, **_kwargs: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_route_multi_pattern_gpu_search_to_full_cli_not_native(monkeypatch):
    # audit #69 (re-do of #441): this test used to pin the BUG -- multi-pattern (-e x3) +
    # --gpu-device-ids delegating straight to the separately-compiled native tg binary,
    # which has its OWN independent -e/-f bugs (verified via direct invocation: multiple -e
    # patterns are not deduplicated when a single line matches more than one). The full CLI
    # now combines multi-pattern correctly (cli/main.py's `_combine_multi_patterns`) and
    # already refuses this exact case in its OWN inner native-delegation gate (`regexp`/
    # `file_patterns` are both in `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`) -- this
    # outer bootstrap.py fast path must route it to the full CLI too, never to native.
    # --gpu-device-ids is documented as experimental/opt-in (main.py's own `search`
    # docstring); correctness beats speed for this already-rare combo.
    called = {"full_cli": False}

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tg",
            "search",
            "--gpu-device-ids",
            "0",
            "-e",
            "error",
            "-e",
            "warn",
            "-e",
            "fatal",
            "bench_data",
        ],
    )
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda binary_name, search_args: pytest.fail("native tg should not run for -e/-f"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_when_rg_is_unavailable(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "ERROR", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_exit_cleanly_for_help(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "--help"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )

    def _fake_full_cli() -> None:
        called["full_cli"] = True
        raise SystemExit(0)

    monkeypatch.setattr(bootstrap, "_run_full_cli", _fake_full_cli)

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_calibrate_subcommand(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "calibrate"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_update_subcommand(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "update"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_fallback_to_full_cli_for_lsp_setup_subcommand(monkeypatch):
    called = {"full_cli": False}

    monkeypatch.setattr(sys, "argv", ["tg", "lsp-setup", "--help"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, search_args: pytest.fail("rg passthrough should not run"),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_route_scan_to_ast_workflow_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "scan", "--config", "sgconfig.yml"])
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: seen.update({"argv": list(argv)})
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    bootstrap.main_entry()

    assert seen == {"argv": ["scan", "--config", "sgconfig.yml"]}


def test_main_entry_should_route_run_to_full_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "run", "ERROR", ".", "--lang", "python"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: seen.update({"full_cli": True}))
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: pytest.fail("workflow cli should not run")
    )

    bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_main_entry_should_delegate_run_to_managed_native_when_available(monkeypatch, tmp_path):
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native tg", encoding="utf-8")
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "run", "--help"])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: pytest.fail("workflow cli should not run")
    )

    def _fake_run(command, check=False):
        seen["command"] = [str(part) for part in command]
        seen["check"] = check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap, "run_subprocess", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "command": [str(native_tg), "run", "--help"],
        "check": False,
    }


def test_main_entry_should_route_test_to_full_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "test"])
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: seen.update({"full_cli": True}))
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: pytest.fail("workflow cli should not run")
    )

    bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_main_entry_should_route_route_test_to_full_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "route-test"])
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: seen.update({"full_cli": True}))
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: pytest.fail("workflow cli should not run")
    )

    bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_main_entry_should_route_ast_info_to_full_cli(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "ast-info"])
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: seen.update({"full_cli": True}))
    monkeypatch.setattr(
        bootstrap, "_run_ast_workflow_cli", lambda argv: pytest.fail("workflow cli should not run")
    )

    bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_main_entry_should_print_version_without_loading_full_cli(monkeypatch, capsys):
    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(bootstrap, "_read_project_version_fallback", lambda: "9.9.9")
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "tensor-grep 9.9.9\n"


def test_main_entry_should_keep_verbose_version_details_without_loading_full_cli(
    monkeypatch,
    capsys,
):
    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version", "--verbose"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(bootstrap, "_read_project_version_fallback", lambda: "9.9.9")
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    output = capsys.readouterr().out
    assert excinfo.value.code == 0
    assert output.startswith("tensor-grep 9.9.9\n\n")
    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in output
    assert "Arrow Zero-Copy IPC is available" in output


def test_python_module_help_should_use_public_tg_program_name() -> None:
    env = dict(os.environ)
    env["TYPER_USE_RICH"] = "0"

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "--help"],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage: tg " in result.stdout
    assert "python -m tensor_grep" not in result.stdout


def test_rank_flags_route_to_full_cli_not_ripgrep() -> None:
    # Regression (dogfood): `tg search --rank PATTERN PATH` (plain text) must route to the full
    # Python CLI, which owns the BM25 re-rank. If --rank/--bm25 are not treated as tg-only flags,
    # bootstrap forwards them to ripgrep, which dies with "rg: unrecognized flag --rank".
    assert bootstrap._requires_full_cli(["--rank", "invoice", "src"])
    assert bootstrap._requires_full_cli(["--bm25", "invoice", "src"])
    # A plain rg-compatible search (no tg-only flags) still passes through to ripgrep.
    assert not bootstrap._requires_full_cli(["invoice", "src"])


def test_equals_form_tg_only_flags_route_to_full_cli() -> None:
    # The --flag=VALUE form must route exactly like the --flag VALUE form, or the equals form
    # silently leaks to ripgrep (e.g. `tg search --generate=bash` emits rg's completions, not
    # tg's). The space form is already covered by the exact-set membership check.
    assert bootstrap._requires_full_cli(["--generate=complete-bash"])
    assert bootstrap._requires_full_cli(["--glob=*.rs", "PATTERN"])
    assert bootstrap._requires_full_cli(["--generate", "complete-bash"])
    assert bootstrap._requires_full_cli(["--glob", "*.rs", "PATTERN"])


def test_rank_bm25_do_not_delegate_to_native_binary() -> None:
    # --rank/--bm25 are Python-only; the native-delegate gate must refuse them (symmetric with
    # --ltl) so a future SEARCH_PYTHON_PASSTHROUGH_FLAGS regression cannot strand them.
    assert not bootstrap._can_delegate_to_native_tg_search(["--json", "--rank", "PATTERN"])
    assert not bootstrap._can_delegate_to_native_tg_search(["--json", "--bm25", "PATTERN"])


def test_multi_pattern_e_f_do_not_delegate_to_native_binary() -> None:
    # audit #69 (re-do of #441): the separately-compiled native binary has its OWN,
    # independent -e/-f bugs (verified via direct invocation -- see cli/bootstrap.py's
    # `_can_delegate_to_native_tg_search` comment). This outer argv fast path must refuse
    # ANY -e/-f usage -- even a single one -- for parity with cli/main.py's OWN inner
    # native-delegation gate, which already refuses it via
    # `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (`regexp`/`file_patterns`).
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "-e", "foo", "-e", "bar", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--json", "-e", "foo", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "-f", "pats.txt", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "--file", "pats.txt", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "--regexp", "foo", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "-efoo", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "-fpats.txt", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "--regexp=foo", "."])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "--file=pats.txt", "."])
    # No -e/-f -> still delegates; -F (fixed-strings, uppercase) is not a -f prefix match.
    assert bootstrap._can_delegate_to_native_tg_search(["--cpu", "foo", "."])
    assert bootstrap._can_delegate_to_native_tg_search(["--cpu", "-F", "foo", "."])


def test_count_matches_does_not_delegate_to_native_binary() -> None:
    # task #121: `--count-matches` reports ripgrep's per-OCCURRENCE count, which the
    # separately-compiled native binary's fallback engine cannot produce (LINE-granular
    # only, same as the Python fallbacks -- see cli/bootstrap.py's
    # `_can_delegate_to_native_tg_search` comment). Before this fix, `--count-matches`
    # combined with a trigger flag (--json/--ndjson/--cpu/--force-cpu/--gpu-device-ids)
    # delegated straight to the native binary and silently returned a LINE count
    # mislabeled as an occurrence count -- this outer argv fast path must refuse it for
    # parity with cli/main.py's OWN inner native-delegation gate, which already refuses it
    # via `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` (`count_matches`).
    assert not bootstrap._can_delegate_to_native_tg_search([
        "--json",
        "--count-matches",
        "foo",
        ".",
    ])
    assert not bootstrap._can_delegate_to_native_tg_search(["--cpu", "--count-matches", "foo", "."])
    assert not bootstrap._can_delegate_to_native_tg_search([
        "--ndjson",
        "--count-matches",
        "foo",
        ".",
    ])
    # -c/--count is UNCHANGED: its line-count contract is exactly what the native binary's
    # fallback already provides correctly, so it keeps delegating.
    assert bootstrap._can_delegate_to_native_tg_search(["--json", "-c", "foo", "."])
    assert bootstrap._can_delegate_to_native_tg_search(["--json", "--count", "foo", "."])


def _native_tg_binary_for_lock_test() -> str | None:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    for candidate in (
        Path(f"rust_core/target/release/{exe_name}"),
        Path(f"rust_core/target/debug/{exe_name}"),
    ):
        if candidate.exists():
            return str(candidate.resolve())
    return None


def test_rust_first_count_matches_refuses_via_native_self_guard(tmp_path: Path) -> None:
    """task #121 lock-test (dogfoods the REAL native binary).

    `--count-matches` is excluded from `_can_delegate_to_native_tg_search`, but the
    `_prefer_rust_first_search()` OR-branch in `main_entry` can still route a bare
    `--count-matches` search to the native binary when `TG_RUST_FIRST_SEARCH=1` (it is not a
    `_requires_full_cli` flag). That bypass is SAFE only because the native binary itself
    self-refuses count_matches via `require_ripgrep_or_exit` (rust_core/src/main.rs) when rg
    is unresolvable -- a clean exit-2, never a silent wrong count. This test locks that
    end-to-end invariant so a future routing change to the rust-first branch cannot silently
    reopen the silent-wrong-count. Uses `TG_DISABLE_RG=1` to force rg unresolvable
    deterministically and `TG_NATIVE_TG_BINARY` to pin the in-tree binary.
    """
    native_binary = _native_tg_binary_for_lock_test()
    if native_binary is None:
        pytest.skip("Native tg binary not built in this environment")

    target = tmp_path / "sample.txt"
    # Line 1 has THREE occurrences of foo on ONE line: a silent line-count fallback would
    # print 1 (wrong); the correct rg occurrence-count would be 3. The native self-refuse
    # must produce NEITHER -- it must refuse rather than emit any bare number.
    target.write_text("foo foo foo\nbar\n", encoding="utf-8")

    env = dict(os.environ)
    env["TG_RUST_FIRST_SEARCH"] = "1"
    env["TG_NATIVE_TG_BINARY"] = native_binary
    env["TG_DISABLE_RG"] = "1"
    env.pop("TG_DISABLE_NATIVE_TG", None)
    env.pop("TG_RG_PATH", None)

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "search", "foo", str(target), "--count-matches"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )

    # Clean refuse, never a silent wrong count.
    assert result.returncode == 2, (
        f"expected exit 2 refuse, got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert result.stdout.strip() not in {"1", "3"}, (
        f"native binary emitted a bare count instead of refusing: stdout={result.stdout!r}"
    )
    assert "rg" in result.stderr.lower() or "ripgrep" in result.stderr.lower(), (
        f"refuse message should name the missing rg backend: stderr={result.stderr!r}"
    )
