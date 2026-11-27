from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap
from tensor_grep.cli import main as cli_main
from tensor_grep.cli.bootstrap import _KNOWN_COMMANDS
from tensor_grep.cli.commands import KNOWN_COMMANDS
from tensor_grep.cli.commands import RESERVED_TOP_LEVEL_COMMANDS as _RESERVED_COMMANDS
from tensor_grep.cli.main import app


def test_bootstrap_commands_match_source_of_truth() -> None:
    assert _KNOWN_COMMANDS == set(KNOWN_COMMANDS), (
        "Bootstrap commands must exactly match KNOWN_COMMANDS"
    )


# Task #272 -- the CLASS invariant, not the instance.
#
# Every entry in `_TG_ONLY_SEARCH_FLAG_PREFIXES` ends in "=", i.e. it is a tg-only flag that
# TAKES A VALUE. The attached spelling (`--lang=py`) is self-delimiting and always parsed
# correctly. The SEPARATED spelling (`--lang py`) is only handled if the flag's base name is
# ALSO in `_SEARCH_FLAGS_WITH_VALUES` -- that set is what tells the argv walk to consume the
# NEXT token as a value rather than treat it as a PATH positional.
#
# When the two registries drift, the argv walk mis-reads the flag's VALUE as a PATH positional,
# corrupting `_search_path_args_raw`, `_search_args_paths_defaulted` and
# `_regex_patterns_from_search_args`. `--format` and `--lang` were both missing.
#
# THIS IS A LATENT PARSE DEFECT, NOT A LIVE GUARD BYPASS -- state it precisely, because the
# imprecise version was written here first and an independent gate falsified it by running the
# control arm. Today the mis-parse reaches no user-visible outcome: every member of
# `_TG_ONLY_SEARCH_FLAG_PREFIXES` is ALSO in `_TG_ONLY_SEARCH_FLAGS`, so all nine unconditionally
# force the full CLI, where Typer parses `--format`/`--lang` as `str` options (main.py:7199,7210)
# and derives `paths_defaulted` from its OWN positionals (main.py:7268-7295). `--format` is
# additionally stripped before any consumer by `_strip_noop_rg_format` (bootstrap.py:435).
# Measured end-to-end at a workspace root, treatment vs `origin/main`: the broad-scan refusal
# fires IDENTICALLY in both arms. A helper measured in isolation showing `workspace_root_guard=False`
# is NOT evidence of a user-visible failure -- that is the repo's own "a check that passes in both
# arms proves nothing" rule, applied to a prose claim.
#
# The invariant is therefore a RATCHET, and that is a better justification than the false one:
# it ensures a future tg-only value flag that is NOT full-CLI-forced -- or any future relaxation
# of that routing -- cannot silently inherit the mis-parse. It is an INVARIANT over the whole
# registry rather than two named regression cases because this gap has now been introduced
# repeatedly by adding a flag to one registry and not the other (`--generate` was the previous
# instance, fixed in #745; `--format`/`--lang` predate v1.95.0). Enumerating today's offenders
# would pass again the moment someone adds a tenth prefix.
#
# The rg-argv differential fuzz gate (`scripts/rg_argv_differential_fuzz.py`) can NEVER cover this
# class by construction -- it models *ripgrep's* grammar, and ripgrep has no `--format`/`--lang`.
# tg-only flags need a tg-side invariant, which is this test.
def test_tg_only_value_flag_prefixes_are_registered_as_value_taking_flags() -> None:
    missing = sorted(
        prefix.rstrip("=")
        for prefix in bootstrap._TG_ONLY_SEARCH_FLAG_PREFIXES
        if prefix.rstrip("=") not in bootstrap._SEARCH_FLAGS_WITH_VALUES
    )
    assert not missing, (
        "Every tg-only value-taking flag must appear in _SEARCH_FLAGS_WITH_VALUES so the "
        "SEPARATED spelling (`--flag value`) consumes its value instead of reading it as a "
        f"PATH positional. Missing: {missing}. Add each to _SEARCH_FLAGS_WITH_VALUES."
    )


# Task #272 -- the parse-level behaviour the invariant above protects. `_search_path_args_raw`
# returning [] means "no explicit PATH positional", the signal the caller's `paths or ["."]`
# fallback keys on. Scope note, per the comment above: this asserts the WALK, not a user-visible
# outcome -- the full-CLI routing currently masks the defect end-to-end.
#
# The ATTACHED rows are pinned deliberately and pass in BOTH arms. That is the correct signature
# for a "don't break the other spelling" pin, not inert filler: on `origin/main` the attached
# form survives only BY ACCIDENT -- it misses the `startswith(f"{flag}=")` arm and falls through
# to the generic `arg.startswith("-")` skip (bootstrap.py:801). After the fix it is handled by the
# value-set arm instead. Adding a flag to `_SEARCH_FLAGS_WITH_VALUES` is exactly the change that
# could break the attached spelling by consuming a following token, so it needs pinning -- but do
# NOT cite these two rows as evidence the fix works; only the separated rows discriminate.
@pytest.mark.parametrize(
    ("search_args", "expected_paths"),
    [
        (["--format", "json", "needle"], []),
        (["--lang", "py", "needle"], []),
        (["--format=json", "needle"], []),
        (["--lang=py", "needle"], []),
        (["--format", "json", "needle", "sub"], ["sub"]),
        (["--lang", "py", "needle", "sub"], ["sub"]),
    ],
    ids=[
        "--format-separated",
        "--lang-separated",
        "--format-attached",
        "--lang-attached",
        "--format-separated-with-real-path",
        "--lang-separated-with-real-path",
    ],
)
def test_search_path_args_raw_does_not_read_a_tg_only_flag_value_as_a_path(
    search_args: list[str], expected_paths: list[str]
) -> None:
    assert bootstrap._search_path_args_raw(search_args) == expected_paths


def test_codemap_argv_does_not_forward_to_search() -> None:
    """Registration site 1 (commands.py KNOWN_COMMANDS): a miss here would silently misroute
    `tg codemap` into a ripgrep search for the literal pattern "codemap" instead of the real
    command -- `_normalize_search_invocation` returning non-None is exactly that misrouting."""
    assert bootstrap._normalize_search_invocation(["codemap"]) is None
    assert bootstrap._normalize_search_invocation(["codemap", "--json"]) is None
    assert bootstrap._normalize_search_invocation(["codemap", "--check"]) is None


def test_reserved_unknown_flag_bearing_command_is_refused_not_searched() -> None:
    """A90: `tg edit-ready --json` / `--help` are roadmap commands that do not exist yet and
    must exit non-zero with unknown_command -- never fall through to a search for "edit-ready"
    (which would fake a command's existence at exit 0). nearest[] is thresholded: edit-ready is
    not typo-near ANY known command, so [] is the HONEST suggestion set -- the refusal itself is
    what matters."""
    refusal = bootstrap._top_level_command_refusal(["edit-ready", "--json"])
    assert refusal is not None, "reserved+flag must be a refusal"
    first, nearest = refusal
    assert first == "edit-ready"
    assert isinstance(nearest, list)
    # Honest thresholded nearest: edit-ready is distance > 3 from every registered command.
    assert bootstrap._nearest_commands("edit-ready") == []


def test_reserved_unknown_help_is_refused_not_searched() -> None:
    """A90 help shape: `tg edit-ready --help` is refused with unknown_command (a nonexistent
    command has no help), never routed to search help."""
    refusal = bootstrap._top_level_command_refusal(["edit-ready", "--help"])
    assert refusal is not None, "reserved + --help must be a refusal"


def test_unreserved_pattern_with_flag_still_searches() -> None:
    """A90 regression pin: `tg hello --json` (unreserved pattern + flag) is a LEGAL search and
    must not be a refusal -- flag-bearing unreserved unknowns are pattern+flag searches."""
    assert bootstrap._top_level_command_refusal(["hello", "--json"]) is None
    assert bootstrap._top_level_command_refusal(["hello"]) is None
    assert bootstrap._top_level_command_refusal(["helloworlddocs", "--json"]) is None


def test_nearest_commands_bounded_and_deterministic() -> None:
    """A90 nearest[] contract: normalized, max distance 3, no internal __ names, capped,
    deterministic, empty when nothing close."""
    near = bootstrap._nearest_commands("searhc")
    assert "search" in near
    assert len(near) <= 5
    assert bootstrap._nearest_commands("qqqqzzzz") == []
    assert near == bootstrap._nearest_commands("searhc")  # deterministic


def test_reserved_and_known_commands_are_disjoint() -> None:
    """A90 lifecycle invariant: a roadmap-reserved name must never also be a registered
    KNOWN_COMMAND (and vice versa). The native door parses BOTH sets scoped from commands.py;
    if a name lands in both, the not-known gate could never refuse it (reserved reads known)."""
    assert _RESERVED_COMMANDS, "reserved set must be non-empty (edit-ready/verify-edit/workspace)"
    assert _RESERVED_COMMANDS.isdisjoint(_KNOWN_COMMANDS), (
        f"reserved ∩ known must be empty: {_RESERVED_COMMANDS & _KNOWN_COMMANDS}"
    )
    assert {"edit-ready", "verify-edit", "workspace"}.issubset(_RESERVED_COMMANDS)
    assert "edit-ready" not in _KNOWN_COMMANDS


def test_vendored_root_dir_names_match_source_of_truth() -> None:
    """Review finding L1 (PR #400): cli/bootstrap.py's front-door vendored-root mirror and
    cli/main.py's `_should_refuse_unbounded_vendored_root_scan` guard must trigger on
    exactly the same set of heavy top-level dir names, or the two front doors (native/rg
    fast path vs full CLI) can disagree about whether a root is unbounded.

    perf/#48: bootstrap.py no longer binds `_UNBOUNDED_VENDORED_ROOT_DIR_NAMES` as a
    persistent module attribute -- `_search_paths_include_vendored_root` now does its own
    function-local `from tensor_grep.io.scan_limits import UNBOUNDED_VENDORED_ROOT_DIR_NAMES`
    (campaign #6 / F2.4: moved from `tensor_grep.io.directory_scanner`, which transitively pulls
    in `tensor_grep.core.config` and stdlib `dataclasses`/`inspect` -- `scan_limits` is
    zero-dependency) so that heavy import is not paid by the `tg --version` fast path NOR by a
    real search invocation whose guard check never needs `SearchConfig` at all
    (`tests/unit/test_bootstrap_fast_path_imports.py` pins both). Structurally this makes
    bootstrap.py's copy DRIFT-PROOF -- it always re-reads the canonical set fresh, it can no
    longer hold a stale independent copy -- so this test now (a) checks cli/main.py's own
    still-module-level copy against the canonical source directly, and (b) behaviorally proves
    bootstrap's guard function actually consults that same canonical set."""
    from tensor_grep.io.scan_limits import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

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
    from tensor_grep.io.scan_limits import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

    for name in UNBOUNDED_VENDORED_ROOT_DIR_NAMES:
        root = tmp_path / f"root-{name}"
        (root / name).mkdir(parents=True)
        assert bootstrap._search_paths_include_vendored_root([str(root)]) is True, (
            f"guard must trigger on canonical vendored dir name {name!r}"
        )

    unrelated_root = tmp_path / "root-unrelated"
    (unrelated_root / "not_a_vendored_dir").mkdir(parents=True)
    assert bootstrap._search_paths_include_vendored_root([str(unrelated_root)]) is False


def test_implicit_search_walk_file_ceiling_matches_source_of_truth() -> None:
    """Item #105-parity: cli/main.py's `_LARGE_ROOT_SCAN_FILE_CEILING` and cli/bootstrap.py's
    front-door mirror `_search_paths_include_oversized_implicit_root` must both consult the
    SAME ceiling value (`io/scan_limits.IMPLICIT_SEARCH_WALK_FILE_CEILING` -- campaign #6 / F2.4:
    moved from `io/directory_scanner.py`, which still re-exports it), or the two front doors
    (native/rg fast path vs full CLI) can disagree about whether an implicit-path root is
    oversized -- exactly the class of drift the vendored/workspace constants above were
    centralized to prevent."""
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    assert cli_main._LARGE_ROOT_SCAN_FILE_CEILING == IMPLICIT_SEARCH_WALK_FILE_CEILING
    assert IMPLICIT_SEARCH_WALK_FILE_CEILING > 0


def test_scan_limits_single_source_of_truth_across_every_re_export() -> None:
    """Campaign #6 / F2.4 sync-pin (TDD requirement b): all 5 broad-scan-guard constants now
    DEFINED in the zero-dependency `tensor_grep.io.scan_limits` module must be IDENTICAL --
    same object, via `is`, not just `==` -- to every module that re-exports or copies them:
    `io/directory_scanner.py`'s compatibility re-export (`import ... as ...`), `cli/main.py`'s
    module-level broad-scan literals (`_UNBOUNDED_VENDORED_ROOT_DIR_NAMES`,
    `_BROAD_WORKSPACE_PROJECT_MARKERS`, `_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD`,
    `_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD`, `_LARGE_ROOT_SCAN_FILE_CEILING`), and
    `cli/scan_guardrails.py`'s own private aliases (`_BROAD_WORKSPACE_PROJECT_MARKERS`,
    `_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD`, `_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD`).
    `is` (not `==`) makes the single-source-of-truth contract a BUILD-TIME fact for the
    frozenset-valued constants: two independently-constructed-but-equal frozensets would pass an
    `==` check even after a future edit accidentally re-literals one copy instead of importing
    it -- `is` can only pass when every site imports the exact same object. int constants have no
    separate identity to lose (Python interns small ints), so `==` is the meaningful check for
    those two; the assertion below uses `==` uniformly since it is the STRICTER check for
    frozensets too (`is` implies `==`) and the weakest link (an accidental re-literal) is what
    this test exists to catch."""
    from tensor_grep.cli import scan_guardrails
    from tensor_grep.io import directory_scanner, scan_limits

    # scan_limits (the canonical definition) vs directory_scanner (the compatibility re-export):
    # `is` -- a straight `import ... as ...` re-export must be the identical object, never a copy.
    for name in (
        "UNBOUNDED_VENDORED_ROOT_DIR_NAMES",
        "BROAD_WORKSPACE_PROJECT_MARKERS",
        "BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD",
        "BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD",
        "IMPLICIT_SEARCH_WALK_FILE_CEILING",
    ):
        canonical = getattr(scan_limits, name)
        reexported = getattr(directory_scanner, name)
        assert reexported is canonical, (
            f"directory_scanner.{name} must be the SAME object as scan_limits.{name} "
            "(a straight re-export, not a copy)"
        )

    # cli/main.py's module-level broad-scan literals ("bootstrap's view" of the constants --
    # main.py:46-52 imports directly from scan_limits, the same path bootstrap.py's 3 guard
    # helpers use function-locally):
    assert (
        cli_main._UNBOUNDED_VENDORED_ROOT_DIR_NAMES is scan_limits.UNBOUNDED_VENDORED_ROOT_DIR_NAMES
    )
    assert cli_main._BROAD_WORKSPACE_PROJECT_MARKERS is scan_limits.BROAD_WORKSPACE_PROJECT_MARKERS
    assert (
        cli_main._BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
        == scan_limits.BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
    )
    assert (
        cli_main._BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
        == scan_limits.BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
    )
    assert cli_main._LARGE_ROOT_SCAN_FILE_CEILING == scan_limits.IMPLICIT_SEARCH_WALK_FILE_CEILING

    # cli/scan_guardrails.py's own private aliases (the `tg scan` guard's view):
    assert (
        scan_guardrails._BROAD_WORKSPACE_PROJECT_MARKERS
        is scan_limits.BROAD_WORKSPACE_PROJECT_MARKERS
    )
    assert (
        scan_guardrails._BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
        == scan_limits.BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
    )
    assert (
        scan_guardrails._BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
        == scan_limits.BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
    )


def test_oversized_implicit_root_probe_triggers_over_ceiling_real_walk(tmp_path: Path) -> None:
    """Item #105: bootstrap's front-door ceiling probe must fire on a REAL walk exceeding the
    canonical ceiling -- mirrors cli/main.py's own
    `test_implicit_glob_walk_probe_exceeds_ceiling_even_with_zero_matching_files` fixture style
    (real files on disk, not a monkeypatched-down ceiling), so this proves the SAME real-scale
    behavior at the bootstrap layer."""
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = tmp_path / "bigrepo"
    root.mkdir()
    for index in range(IMPLICIT_SEARCH_WALK_FILE_CEILING + 100):
        (root / f"mod_{index}.py").write_text("x\n", encoding="utf-8")

    assert bootstrap._search_paths_include_oversized_implicit_root([str(root)], ["pat"]) is True


def test_oversized_implicit_root_probe_allows_small_tree(tmp_path: Path) -> None:
    """Non-regression: a small tree well under the ceiling must not be flagged."""
    root = tmp_path / "smallrepo"
    root.mkdir()
    for index in range(20):
        (root / f"mod_{index}.py").write_text("x\n", encoding="utf-8")

    assert bootstrap._search_paths_include_oversized_implicit_root([str(root)], ["pat"]) is False


def test_oversized_implicit_root_probe_widens_for_no_ignore_flag(tmp_path: Path) -> None:
    """The probe must not UNDER-count relative to the real invocation: files that only exist
    because `--no-ignore` was requested must still be counted toward the ceiling, or a huge
    `--no-ignore` walk could slip through as "under ceiling" while the real search walks far
    more than the probe measured."""
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored_dir = root / "ignored"
    ignored_dir.mkdir()
    for index in range(IMPLICIT_SEARCH_WALK_FILE_CEILING + 100):
        (ignored_dir / f"mod_{index}.py").write_text("x\n", encoding="utf-8")

    # Default config respects .gitignore -- the probe must NOT see the ignored files.
    assert bootstrap._search_paths_include_oversized_implicit_root([str(root)], ["pat"]) is False
    # `--no-ignore` widens the walk to include them -- the probe must now catch it.
    assert (
        bootstrap._search_paths_include_oversized_implicit_root([str(root)], ["pat", "--no-ignore"])
        is True
    )


@pytest.fixture(scope="module")
def _gitignored_heavy_tree(tmp_path_factory):
    """Shared fixture for the no-ignore-family parity tests below: a repo root whose
    `.gitignore` hides a subdirectory containing more than `IMPLICIT_SEARCH_WALK_FILE_CEILING`
    files. Built ONCE (module-scoped) and only ever READ by the parametrized cases below -- 7
    independent 1500+-file trees would multiply this suite's I/O for no benefit."""
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = tmp_path_factory.mktemp("gitignored_heavy_tree")
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored_dir = root / "ignored"
    ignored_dir.mkdir()
    for index in range(IMPLICIT_SEARCH_WALK_FILE_CEILING + 10):
        (ignored_dir / f"mod_{index}.py").write_text("x\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("flag", "field_name", "expected_widens"),
    [
        ("--no-ignore", "no_ignore", True),
        ("--no-ignore-vcs", "no_ignore_vcs", True),
        ("--no-ignore-files", "no_ignore_files", True),
        ("--no-ignore-dot", "no_ignore_dot", False),
        ("--no-ignore-exclude", "no_ignore_exclude", False),
        ("--no-ignore-global", "no_ignore_global", False),
        ("--no-ignore-parent", "no_ignore_parent", False),
    ],
)
def test_oversized_implicit_root_probe_matches_sibling_per_no_ignore_field(
    _gitignored_heavy_tree: Path, flag: str, field_name: str, expected_widens: bool
) -> None:
    """#702 gate NIT-1: bootstrap's front-door probe must mirror cli/main.py's
    `_implicit_glob_search_walk_exceeds_ceiling` field-for-field, not collapse every
    `--no-ignore*` flag onto a single `no_ignore=True`. `DirectoryScanner._load_ignore_spec`
    (`io/directory_scanner.py:154`) only disables `.gitignore` for `no_ignore`/`no_ignore_vcs`/
    `no_ignore_files` -- `--no-ignore-dot`/`-exclude`/`-global`/`-parent` are no-ops for this
    scanner's single ignore mechanism, so they must NOT flip the probe, exactly like they don't
    flip the sibling (which gets its `SearchConfig` for free from real Typer parsing rather than
    hand-building one from raw argv)."""
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = _gitignored_heavy_tree
    bootstrap_result = bootstrap._search_paths_include_oversized_implicit_root(
        [str(root)], ["pat", flag]
    )
    sibling_config = SearchConfig(**{field_name: True})
    sibling_result = cli_main._implicit_glob_search_walk_exceeds_ceiling(
        [str(root)], sibling_config, IMPLICIT_SEARCH_WALK_FILE_CEILING
    )

    assert bootstrap_result is expected_widens, (
        f"{flag} widened={bootstrap_result}, expected {expected_widens}"
    )
    assert bootstrap_result == sibling_result, (
        f"{flag}: bootstrap probe ({bootstrap_result}) diverged from cli/main.py's sibling "
        f"probe ({sibling_result}) -- the two front doors disagree"
    )


def test_oversized_implicit_root_probe_stops_short_of_full_walk(
    tmp_path: Path, monkeypatch
) -> None:
    """#702 gate NIT-2: pin the short-circuit's COST, not just its boolean outcome -- the probe
    must stop consuming `DirectoryScanner.walk()` the moment its own running count exceeds
    `IMPLICIT_SEARCH_WALK_FILE_CEILING`, rather than draining a full walk and counting after the
    fact. Fakes `os.walk` (instead of writing a real multi-thousand-entry tree to disk) so this
    is a deterministic structural assertion, not a wall-clock floor: a mutable counter records
    how many synthetic directories the walk actually produced before the probe returned, and the
    assertion is that it stayed at exactly ceiling+1 -- far short of the fabricated tree's full
    (10x-ceiling) size."""
    from tensor_grep.io import directory_scanner as ds_module
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = tmp_path / "hugerepo"
    root.mkdir()

    total_synthetic_dirs = IMPLICIT_SEARCH_WALK_FILE_CEILING * 10
    produced = {"count": 0}

    def _fake_os_walk(_top, *_args, **_kwargs):
        for index in range(total_synthetic_dirs):
            produced["count"] += 1
            yield (str(root / f"d{index}"), [], [f"f{index}.py"])

    monkeypatch.setattr(ds_module.os, "walk", _fake_os_walk)

    assert bootstrap._search_paths_include_oversized_implicit_root([str(root)], ["pat"]) is True
    # The probe returns True the instant its running count exceeds the ceiling -- it must not
    # have pulled anywhere near the full fabricated tree (10x the ceiling).
    expected_pulled = IMPLICIT_SEARCH_WALK_FILE_CEILING + 1
    assert produced["count"] == expected_pulled, (
        f"probe pulled {produced['count']} entries from the walk before short-circuiting; "
        f"expected exactly ceiling+1 ({expected_pulled}), proving the bound is real, not a "
        "post-hoc count-then-truncate"
    )


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


def test_main_entry_bare_plain_text_search_bypasses_native_delegation_even_when_native_binary_present(
    monkeypatch,
) -> None:
    """Task #269 map finding, independently confirmed (also root-caused by the sibling #744
    fix on PR c597b85): `_can_delegate_to_native_tg_search` only
    forwards a search to the compiled native binary when argv contains one of
    `{--cpu, --force-cpu, --json, --ndjson, --gpu-device-ids}`, or `TG_RUST_FIRST_SEARCH=1`
    (default off). A bare `tg search PATTERN .` has none of those triggers, so bootstrap ALWAYS
    falls through to its own `_run_rg_passthrough` -- REGARDLESS of whether a native `tg`
    binary is discoverable. This is the actual mechanism behind the #264/#269 "an output-format
    flag changes the file set" bug family: the flag doesn't touch ignore-handling directly, it
    changes WHICH ENGINE runs (native, which already honored root ignore files correctly via
    #127, vs. Python's own naive passthrough). Unlike the sibling test above (which patches
    `resolve_native_tg_binary` to `None`), this test proves the SAME routing decision holds when
    a native binary IS resolvable -- the trigger-set gate is what refuses delegation, not binary
    absence."""
    seen: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", "."])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: "tg.exe")
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_native_tg_search",
        lambda *_args, **_kwargs: pytest.fail(
            "a bare plain-text search must never delegate to the native binary"
        ),
    )
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
    assert seen == {"binary_name": "rg", "search_args": ["needle", "."]}


# --- Task #269: bootstrap._run_rg_passthrough root-ignore-file injection ----------------------
# Mirrors rust_core/src/rg_passthrough.rs's own `root_ignore_file_args` unit tests (task #264 /
# PR #744): this is the Python-only rg-passthrough path reached whenever no compiled native `tg`
# binary is discoverable, which shares the identical pre-#264-fix defect the Rust side already
# closed. See `tensor_grep.cli.rg_root_ignore.root_ignore_file_args` for the shared helper both
# this module and `RipgrepBackend._build_cmd` call.


def _capture_streaming_passthrough_argv(monkeypatch) -> dict[str, object]:
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        bootstrap,
        "_streaming_passthrough_returncode",
        lambda argv, timeout_env_var=None: (
            seen.update({"argv": list(argv), "timeout_env_var": timeout_env_var}) or 0
        ),
    )
    return seen


def test_run_rg_passthrough_injects_root_gitignore_outside_git_repo(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    rc = bootstrap._run_rg_passthrough("rg", ["needle", str(tmp_path)])

    assert rc == 0
    argv = seen["argv"]
    assert argv[0] == "rg"
    flag_index = argv.index("--ignore-file")
    assert argv[flag_index + 1] == str(tmp_path / ".gitignore")
    # The injected operands must precede the user's own search_args so a `--` sentinel the user
    # supplied (if any) still applies only to their own positionals.
    assert argv.index("needle") > flag_index + 1


def test_run_rg_passthrough_defaults_root_to_dot_when_path_omitted(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["needle"])

    argv = seen["argv"]
    assert "--ignore-file" in argv
    value = argv[argv.index("--ignore-file") + 1]
    assert Path(value).resolve() == (tmp_path / ".gitignore").resolve()


def test_run_rg_passthrough_empty_when_no_ignore_files_present(monkeypatch, tmp_path: Path) -> None:
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["needle", str(tmp_path)])

    assert seen["argv"] == ["rg", "needle", str(tmp_path)]


def test_run_rg_passthrough_no_ignore_suppresses_injection(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--no-ignore", "needle", str(tmp_path)])

    assert "--ignore-file" not in seen["argv"]


def test_run_rg_passthrough_no_ignore_files_suppresses_injection(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--no-ignore-files", "needle", str(tmp_path)])

    assert "--ignore-file" not in seen["argv"]


def test_run_rg_passthrough_no_ignore_vcs_skips_only_gitignore(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--no-ignore-vcs", "needle", str(tmp_path)])

    argv = seen["argv"]
    values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--ignore-file"]
    assert not any(v.endswith(".gitignore") for v in values), values
    assert any(v.endswith(".ignore") for v in values), values


def test_run_rg_passthrough_no_ignore_dot_skips_ignore_and_rgignore_not_gitignore(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--no-ignore-dot", "needle", str(tmp_path)])

    argv = seen["argv"]
    values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--ignore-file"]
    assert any(v.endswith(".gitignore") for v in values), values
    assert not any(v.endswith(".ignore") and not v.endswith(".rgignore") for v in values), values
    assert not any(v.endswith(".rgignore") for v in values), values


# --- Task #269 independent-gate finding (BLOCKING on first pass): `-u`/`-uu`/`-uuu`/
# `--unrestricted` is rg's documented alias for `--no-ignore` (`-uu` additionally implies
# `--hidden`, `-uuu` additionally implies `--binary`), forwarded raw to real rg
# (`_streaming_passthrough_returncode` just hands argv straight through). Neither the original
# fix nor its tests observed it: only the four literal `--no-ignore*` long tokens were checked,
# so `-u` left the injected `--ignore-file` in place -- and since `--ignore-file` survives
# `--no-ignore` by design, `-u` came out STRICTER than passing no flag at all. Fixed via
# `_search_args_request_unrestricted`, shared with the pre-existing
# `_search_args_request_unrestricted_generated_scan` broad-scan guardrail so the two cannot
# silently drift out of parity with each other.


def test_run_rg_passthrough_unrestricted_suppresses_injection(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["-u", "needle", str(tmp_path)])

    assert "--ignore-file" not in seen["argv"]


def test_run_rg_passthrough_unrestricted_double_and_triple_suppress_injection(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    for flag in ("-uu", "-uuu"):
        seen = _capture_streaming_passthrough_argv(monkeypatch)
        bootstrap._run_rg_passthrough("rg", [flag, "needle", str(tmp_path)])
        assert "--ignore-file" not in seen["argv"], (flag, seen["argv"])


def test_run_rg_passthrough_unrestricted_long_form_suppresses_injection(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--unrestricted", "needle", str(tmp_path)])

    assert "--ignore-file" not in seen["argv"]


def test_run_rg_passthrough_clustered_unrestricted_short_flag_suppresses_injection(
    monkeypatch, tmp_path: Path
) -> None:
    """`-iu` (ignore-case + unrestricted bundled into one token) is what the naive
    `arg.startswith("-u")` predicate this replaced would have missed entirely -- `"-iu"` does
    not start with `"-u"`. rg's own clap-style parser accepts this cluster identically to
    `-i -u`."""
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["-iu", "needle", str(tmp_path)])

    assert "--ignore-file" not in seen["argv"]


def test_search_args_request_unrestricted_matches_bare_short_flags() -> None:
    assert bootstrap._search_args_request_unrestricted(["-u"]) is True
    assert bootstrap._search_args_request_unrestricted(["-uu"]) is True
    assert bootstrap._search_args_request_unrestricted(["-uuu"]) is True
    assert bootstrap._search_args_request_unrestricted(["--unrestricted"]) is True


def test_search_args_request_unrestricted_matches_clustered_short_flags() -> None:
    # -iu == -i -u; -nu == -n -u (n is not an attached-value flag, so u is still reachable).
    assert bootstrap._search_args_request_unrestricted(["-iu"]) is True
    assert bootstrap._search_args_request_unrestricted(["-nu"]) is True


def test_search_args_request_unrestricted_does_not_false_positive_on_attached_value_flags() -> None:
    # -t is an ATTACHED-VALUE short flag (file type): "-tu" means `--type u`, i.e. a file type
    # literally named "u" -- not `-t -u`. The `u` here is DATA, not a flag, and must not trip
    # the unrestricted gate (a false positive here would UNDER-emit --ignore-file, not
    # over-emit -- still a correctness bug, just the safer direction than the BLOCKING one).
    assert bootstrap._search_args_request_unrestricted(["-tu"]) is False
    # A bare, unrelated flag/pattern must not match.
    assert bootstrap._search_args_request_unrestricted(["-i", "needle", "."]) is False
    assert bootstrap._search_args_request_unrestricted(["needle", "."]) is False


def test_search_args_request_unrestricted_generated_scan_shares_the_same_helper() -> None:
    """The pre-existing broad-scan guardrail must widen identically to the new gate -- both
    call `_search_args_request_unrestricted` rather than maintaining separate predicates."""
    assert bootstrap._search_args_request_unrestricted_generated_scan(["-iu", "needle"]) is True
    assert bootstrap._search_args_request_unrestricted_generated_scan(["needle", "."]) is False


# --- Task #269 independent-gate re-gate FIX-2 (BLOCKING, introduced by the FIX-1 round's own
# extraction): `-e` was missing from `_SEARCH_ATTACHED_VALUE_SHORT_FLAGS`, so `-eunwrap`
# (== `-e unwrap`, a pattern that merely happens to contain the letter `u`) walked past `e` and
# matched the `u` in "unwrap", reporting a false "unrestricted requested". That forced
# `guarded_broad_root` and misrouted a valid rg-passthrough search into the full Python CLI,
# which then rejected the rg-only `--no-heading` flag with exit 2. `-e` is now in
# `_SEARCH_ATTACHED_VALUE_SHORT_FLAGS`, matching real rg's own attached-value semantics.


def test_search_args_request_unrestricted_does_not_false_positive_on_pattern_containing_u() -> None:
    # The exact regression: a pattern attached to -e that happens to contain the letter "u"
    # must not be misread as -u (unrestricted).
    assert bootstrap._search_args_request_unrestricted(["-eunwrap"]) is False
    assert bootstrap._search_args_request_unrestricted(["-eunicorn"]) is False
    # Control: the same pattern minus any "u" must also be False (was already False pre-fix;
    # pins that this fix did not change the no-match case).
    assert bootstrap._search_args_request_unrestricted(["-enicorn"]) is False
    # A GENUINE -u after a real -e-consumed value is still correctly detected as two separate
    # tokens (not attached) -- -e consumes its own token via skip_next elsewhere, this helper
    # only sees raw argv, so an actual "-e", "pattern", "-u" sequence must still trip True.
    assert bootstrap._search_args_request_unrestricted(["-e", "pattern", "-u"]) is True


def test_search_args_request_unrestricted_e_flag_does_not_regress_clustered_forms() -> None:
    """Parity check requested by the independent gate: `-iu`/`-nu`/`-au` must remain True after
    adding `-e` to the attached-value tuple (this fix only changes how `-e<val>` clusters are
    scanned, not `-i`/`-n`/`-a`-led ones)."""
    assert bootstrap._search_args_request_unrestricted(["-iu"]) is True
    assert bootstrap._search_args_request_unrestricted(["-nu"]) is True
    assert bootstrap._search_args_request_unrestricted(["-au"]) is True


def test_requires_full_cli_does_not_misread_dash_e_glob_looking_pattern_as_glob_flag() -> None:
    """The independent gate's cited symmetrical false positive: `-eg*.py` means `-e "g*.py"`
    (a literal pattern), not `-g *.py` (a glob walk-scope flag) -- `_requires_full_cli`'s own
    bundled-short-flag scan must not force full-CLI routing for it now that `-e` is
    a recognized attached-value flag (the scan stops at `e`, never reaching the `g`)."""
    assert bootstrap._requires_full_cli(["-eg*.py", "."]) is False


# --- Task #269 independent-gate re-gate FIX-1 (BLOCKING, and SILENT): `-f`/`--file`/`-fVAL`/
# `-eVAL` supply a PATTERN (from a file, or attached), but `_search_path_args_raw` only
# recognized the bare `-e`/`--regexp` forms as "a pattern is already supplied" -- `-f`/`--file`
# fell into the generic "flag with a value" bucket, which does NOT mark a pattern as seen. The
# consequence: the REAL positional PATH immediately after got silently misread as the bare
# pattern (the "first non-flag token is the pattern" rule), `_search_path_args_raw` returned
# `[]`, `_search_path_args` fell back to `["."]`, and `_run_rg_passthrough` injected the ROOT
# `.gitignore` for the WRONG directory (cwd, not the actual search target) -- a silent
# wrong-file-set, the exact class this whole PR family exists to close. Fixed via
# `_SEARCH_PATTERN_SOURCE_FLAGS` plus the bundled/clustered cluster walk further below (which
# also closes the mid-bundle attached form -- see the BLOCKING-3 test block after this one).


def test_search_path_args_raw_treats_file_flag_as_pattern_source_not_a_path() -> None:
    assert bootstrap._search_path_args_raw(["--file", "pats.txt", "otherdir"]) == ["otherdir"]
    assert bootstrap._search_path_args_raw(["-f", "pats.txt", "otherdir"]) == ["otherdir"]
    assert bootstrap._search_path_args_raw(["-fpats.txt", "otherdir"]) == ["otherdir"]
    assert bootstrap._search_path_args_raw(["--file=pats.txt", "otherdir"]) == ["otherdir"]


def test_search_path_args_raw_treats_attached_dash_e_as_pattern_source_not_a_path() -> None:
    assert bootstrap._search_path_args_raw(["-eneedle", "otherdir"]) == ["otherdir"]
    # Control: the un-attached form already worked before this fix.
    assert bootstrap._search_path_args_raw(["-e", "needle", "otherdir"]) == ["otherdir"]


# --- Task #269 independent-gate re-gate BLOCKING-3 (introduced by the FIX-1 round itself) +
# NB-1: `-e`/`-f` mid-BUNDLE with a leading boolean short flag (`-ieneedle` == `-i -e needle`,
# `-ie needle` == `-i -e` <value in next arg>) was NOT caught by the FIX-1 round's
# `arg.startswith(("-e", "-f"))` check (which only matches `-e`/`-f` as literally the first
# character), so the token fell through as opaque, the real PATH after it was misread as the
# bare pattern, and the WRONG root's `.gitignore` got injected -- same failure shape as FIX-1,
# and origin/main was correct, so this was a genuine regression introduced by this PR. Fixed by
# replacing the narrow `startswith` check with the general cluster walk `_requires_full_cli`
# and `_search_args_request_unrestricted` already use elsewhere in this file. NB-1 (`-im 5
# needle otherdir` misreading the PATTERN "needle" as a PATH) is the exact same underlying
# defect for a NON-pattern-source attached flag (`-m`) and is closed by the same walk.
#
# `-ie needle otherdir` / `-if pats.txt otherdir` are explicitly pinned here because they
# produced the RIGHT ANSWER BY ACCIDENT under the BLOCKING-3 bug (the un-consumed value token
# got swept up as the bare pattern instead of a path, which happened to leave the real PATH
# correctly identified) -- without a test pinning the CORRECT MECHANISM (regexp_pattern_seen
# set via the flag, not via an accidental bare-pattern misread), a future change could
# reintroduce BLOCKING-3 for the "value in a separate token" shape while this suite stayed
# green.


def test_search_path_args_raw_treats_mid_bundle_attached_dash_e_as_pattern_source() -> None:
    # -ieneedle == -i -e needle (attached, mid-bundle).
    assert bootstrap._search_path_args_raw(["-ieneedle", "otherdir"]) == ["otherdir"]
    # -ifpats.txt == -i -f pats.txt (attached, mid-bundle).
    assert bootstrap._search_path_args_raw(["-ifpats.txt", "otherdir"]) == ["otherdir"]


def test_search_path_args_raw_treats_mid_bundle_dash_e_with_separate_value_as_pattern_source() -> (
    None
):
    """Pins the CORRECT MECHANISM for the "value in the next token" bundled form, not just the
    right final answer -- see the module comment above for why the by-accident form is
    insufficient on its own."""
    raw = bootstrap._search_path_args_raw(["-ie", "needle", "otherdir"])
    assert raw == ["otherdir"], raw
    raw = bootstrap._search_path_args_raw(["-if", "pats.txt", "otherdir"])
    assert raw == ["otherdir"], raw


def test_search_path_args_paths_defaulted_is_false_for_mid_bundle_attached_forms() -> None:
    assert bootstrap._search_args_paths_defaulted(["-ieneedle", "otherdir"]) is False
    assert bootstrap._search_args_paths_defaulted(["-ie", "needle", "otherdir"]) is False


def test_search_path_args_raw_closes_nb1_non_pattern_source_mid_bundle_value() -> None:
    """NB-1: `-im 5 needle otherdir` (`-i -m 5`, i.e. ignore-case + max-count=5) is NOT a
    pattern-source flag -- "needle" is the real bare pattern, "otherdir" the real path. Before
    this fix, "5" (which should be consumed as `-m`'s value) was silently misread as the bare
    pattern instead, cascading "needle" into `paths` alongside "otherdir"."""
    assert bootstrap._search_path_args_raw(["-im", "5", "needle", "otherdir"]) == ["otherdir"]


def test_run_rg_passthrough_mid_bundle_attached_dash_e_injects_the_correct_roots_ignore_file(
    monkeypatch, tmp_path: Path
) -> None:
    cwd_root = tmp_path / "wrongroot3"
    other_dir = cwd_root / "otherdir"
    other_dir.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    (other_dir / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["-ieneedle", "otherdir"])

    argv = seen["argv"]
    flag_index = argv.index("--ignore-file")
    injected_path = (cwd_root / argv[flag_index + 1]).resolve()
    assert injected_path == (other_dir / ".gitignore").resolve(), (
        f"must inject otherdir's .gitignore, not cwd's: got {injected_path}"
    )


def test_run_rg_passthrough_mid_bundle_dash_e_separate_value_injects_the_correct_roots_ignore_file(
    monkeypatch, tmp_path: Path
) -> None:
    """Pins the `-ie needle otherdir` by-accident form's CORRECT MECHANISM through the real
    `_run_rg_passthrough` entry point -- not just `_search_path_args_raw` in isolation."""
    cwd_root = tmp_path / "wrongroot4"
    other_dir = cwd_root / "otherdir"
    other_dir.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    (other_dir / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["-ie", "needle", "otherdir"])

    argv = seen["argv"]
    flag_index = argv.index("--ignore-file")
    injected_path = (cwd_root / argv[flag_index + 1]).resolve()
    assert injected_path == (other_dir / ".gitignore").resolve(), (
        f"must inject otherdir's .gitignore, not cwd's: got {injected_path}"
    )


def test_search_path_args_paths_defaulted_is_false_for_file_flag_with_explicit_path() -> None:
    """The `_search_args_paths_defaulted` predicate (used to decide whether a walk-scope flag
    like `-g`/`-t` counts as a genuine bound, and by the broad-scan guardrails) derives from
    `_search_path_args_raw` and shares the exact same bug: pre-fix, `-f pats.txt otherdir` read
    as paths-defaulted (no explicit path) even though `otherdir` WAS an explicit path -- just
    misclassified as the bare pattern."""
    assert bootstrap._search_args_paths_defaulted(["--file", "pats.txt", "otherdir"]) is False
    assert bootstrap._search_args_paths_defaulted(["-eneedle", "otherdir"]) is False


def test_run_rg_passthrough_file_flag_injects_the_correct_roots_ignore_file_not_cwds(
    monkeypatch, tmp_path: Path
) -> None:
    """The measured regression table's shape: cwd holds a root `.gitignore` that must NOT be
    consulted; the real search target (`otherdir`, an explicit PATH positional) has its OWN
    `.gitignore` that MUST be. Pre-fix, `_run_rg_passthrough` silently injected cwd's file
    instead (or, depending on exact walk state, injected nothing useful) -- either way, the
    WRONG root's ignore file, not `otherdir`'s."""
    cwd_root = tmp_path / "wrongroot"
    other_dir = cwd_root / "otherdir"
    other_dir.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    (other_dir / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["--file", "pats.txt", "otherdir"])

    argv = seen["argv"]
    flag_index = argv.index("--ignore-file")
    # The injected path is emitted relative to the passed root ("otherdir"), matching real rg's
    # own relative-argv convention -- resolve against cwd (which this test pinned via
    # monkeypatch.chdir) before comparing, rather than expecting an absolute path verbatim.
    injected_path = (cwd_root / argv[flag_index + 1]).resolve()
    assert injected_path == (other_dir / ".gitignore").resolve(), (
        f"must inject otherdir's .gitignore, not cwd's: got {injected_path}"
    )


def test_run_rg_passthrough_attached_dash_e_injects_the_correct_roots_ignore_file(
    monkeypatch, tmp_path: Path
) -> None:
    cwd_root = tmp_path / "wrongroot2"
    other_dir = cwd_root / "otherdir"
    other_dir.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    (other_dir / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["-eneedle", "otherdir"])

    argv = seen["argv"]
    flag_index = argv.index("--ignore-file")
    injected_path = (cwd_root / argv[flag_index + 1]).resolve()
    assert injected_path == (other_dir / ".gitignore").resolve(), (
        f"must inject otherdir's .gitignore, not cwd's: got {injected_path}"
    )


# --- Task #269 independent-gate FINAL-GATE BLOCKING-1 (a different DIMENSION than rounds 1-3,
# each of which enumerated HOW a pattern-source flag is spelled): rg's grammar is
# ORDER-INDEPENDENT -- a pattern-source flag can appear BEFORE or AFTER the positional PATH
# (`rg sub -eneedle` produces the same output as `rg -eneedle sub`) -- but
# `_search_path_args_raw` was a single left-to-right pass whose `regexp_pattern_seen` state
# only flipped True at the MOMENT it encountered the flag. `tg search otherdir -eneedle` (PATH
# first) silently misread "otherdir" as the bare pattern, `_search_path_args_raw` returned an
# empty root list, and `_run_rg_passthrough` injected the WRONG root's `.gitignore` --
# reproducing the #264 signature (plain-text and `--json` disagreeing on the file set) a FOURTH
# time, inside this same PR, with zero prior test coverage (no test among the prior 205 put a
# positional before the pattern-source flag). Fixed via a genuine two-pass walk: a pre-pass
# (`_search_args_contains_pattern_source_flag`) determines whether ANY pattern-source flag
# appears anywhere before `--`, seeding `regexp_pattern_seen`'s STARTING value instead of
# letting the extraction walk discover it mid-stream.


@pytest.mark.parametrize(
    "search_args",
    [
        ["otherdir", "-eneedle"],
        ["otherdir", "-e", "needle"],
        ["otherdir", "-fpats.txt"],
        ["otherdir", "-f", "pats.txt"],
        ["otherdir", "--regexp=needle"],
        ["otherdir", "--file=pats.txt"],
    ],
    ids=["-eVAL", "-e VAL", "-fFILE", "-f FILE", "--regexp=VAL", "--file=FILE"],
)
def test_search_path_args_raw_path_before_pattern_source_flag_all_six_spellings(
    search_args: list[str],
) -> None:
    """All six rg-accepted pattern-source spellings, with the PATH positional appearing FIRST
    -- the exact dimension BLOCKING-1 found uncovered. Every one of these previously returned
    `[]` (the PATH silently misread as the bare pattern) instead of `["otherdir"]`."""
    assert bootstrap._search_path_args_raw(search_args) == ["otherdir"], search_args


def test_search_path_args_raw_path_before_mid_bundle_pattern_source_flag() -> None:
    """Mid-bundle spelling (BLOCKING-3's dimension) combined with PATH-first ordering
    (BLOCKING-1's dimension) -- both must compose correctly."""
    assert bootstrap._search_path_args_raw(["otherdir", "-ieneedle"]) == ["otherdir"]
    assert bootstrap._search_path_args_raw(["otherdir", "-ie", "needle"]) == ["otherdir"]


def test_search_path_args_paths_defaulted_is_false_when_path_precedes_pattern_source_flag() -> None:
    assert bootstrap._search_args_paths_defaulted(["otherdir", "-eneedle"]) is False
    assert bootstrap._search_args_paths_defaulted(["otherdir", "-e", "needle"]) is False


def test_search_args_contains_pattern_source_flag_stops_at_end_of_options_sentinel() -> None:
    """`-e needle -- sub` (pattern-source flag BEFORE `--`) already worked correctly before
    this fix and must stay that way; a hypothetical pattern-source-shaped token appearing AFTER
    `--` is a literal positional, not a flag, and must not be misdetected by the pre-pass."""
    assert bootstrap._search_args_contains_pattern_source_flag(["-e", "needle", "--", "sub"]) is (
        True
    )
    assert bootstrap._search_args_contains_pattern_source_flag(["sub", "--", "-eneedle"]) is False


# Independent-gate final-gate BLOCKING-2 (re-gate on the BLOCKING-1 round itself): the shared
# `_attached_cluster_value_offset` helper unifies which OFFSET a mid-bundle attached-value flag
# sits at, but the pre-pass previously decided `skip_next` INDEPENDENTLY of the extraction
# walk -- and decided it wrong, never setting it at all. A mid-bundle value flag ending a token
# (`-ir`, `-ig` -- `-i` plus `-r`/`-g`, whose value is the NEXT argv token) therefore failed to
# consume its own value in the pre-pass, so a value that happens to look like a pattern-source
# flag (`-e needle`, `-fpats.txt`) got misread as a SEPARATE, genuine flag instead of data.
# `["--replace", "-eattached", "otherdir"]` alone did not catch this: `--replace` is the LONG
# separated form, handled by the `_SEARCH_FLAGS_WITH_VALUES` arm, which already worked --
# a negative control that only exercises the working arm proves nothing about the broken
# mid-bundle short-flag arm. `-r` and `-g` are the only two value-taking short flags that
# actually accept a `-e`-shaped value in real rg (verified live: `-t`/`-T` reject it as an
# unrecognized file type, `-m`/`-A`/`-B`/`-C`/`-M`/`-d`/`-j`/`-E` reject it as a parse error) --
# `-r` (`--replace`) and `-g` (`--glob`) both happily accept an arbitrary string.
@pytest.mark.parametrize(
    "search_args",
    [
        ["--replace", "-eattached", "otherdir"],
        ["-ir", "-e", "needle"],
        ["-ig", "-fpats.txt", "sub"],
    ],
    ids=["--replace-long-separated", "-ir-mid-bundle-r", "-ig-mid-bundle-g"],
)
def test_search_args_contains_pattern_source_flag_does_not_count_a_consumed_flag_value(
    search_args: list[str],
) -> None:
    """A token that LOOKS like a pattern-source flag but is actually another flag's VALUE
    (e.g. `--replace -eattached`, where `-eattached` is `--replace`'s replacement string, or
    the mid-bundle `-ir -e needle`/`-ig -fpats.txt sub`, where `-e`/`-f` are `-r`'s/`-g`'s own
    value) must not be misdetected as a real pattern-source flag."""
    assert bootstrap._search_args_contains_pattern_source_flag(search_args) is False, search_args


def test_run_rg_passthrough_path_before_dash_e_injects_the_correct_roots_ignore_file(
    monkeypatch, tmp_path: Path
) -> None:
    """Outcome-level pin through the real `_run_rg_passthrough` entry point, mirroring the
    independent gate's own measured repro: `otherdir` (no ignore file of its own) must get
    NOTHING injected -- not cwd's `.gitignore` -- when the pattern-source flag comes after it
    in argv."""
    cwd_root = tmp_path / "wrongroot5"
    other_dir = cwd_root / "otherdir"
    other_dir.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["otherdir", "-eneedle"])

    argv = seen["argv"]
    assert "--ignore-file" not in argv, (
        f"otherdir has no ignore file of its own -- nothing must be injected: {argv}"
    )


def test_run_rg_passthrough_path_before_dash_e_still_finds_the_correct_roots_own_ignore_file(
    monkeypatch, tmp_path: Path
) -> None:
    """The positive-injection counterpart: when the PATH-first target DOES have its own
    ignore file, that file (not cwd's) must be injected."""
    cwd_root = tmp_path / "wrongroot6"
    sub = cwd_root / "sub"
    sub.mkdir(parents=True)
    (cwd_root / ".gitignore").write_text("should-not-apply.log\n", encoding="utf-8")
    (sub / ".gitignore").write_text("d.log\n", encoding="utf-8")
    monkeypatch.chdir(cwd_root)
    seen = _capture_streaming_passthrough_argv(monkeypatch)

    bootstrap._run_rg_passthrough("rg", ["sub", "-eneedle"])

    argv = seen["argv"]
    flag_index = argv.index("--ignore-file")
    injected_path = (cwd_root / argv[flag_index + 1]).resolve()
    assert injected_path == (sub / ".gitignore").resolve(), (
        f"must inject sub's .gitignore, not cwd's: got {injected_path}"
    )


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


def test_main_entry_should_not_passthrough_oversized_implicit_single_project_root(
    monkeypatch, tmp_path: Path
) -> None:
    """Item #105 : a bare, flag-less, unscoped `tg search
    PATTERN` on a large ORDINARY single-project root -- no top-level vendored dir name, no
    independently-marked sibling projects, so NEITHER `_search_paths_include_workspace_root`
    NOR `_search_paths_include_vendored_root` fires -- used to sail straight into
    `_run_rg_passthrough` (a raw `rg` subprocess spawn bounded only by a wall-clock timeout,
    `TG_RG_TIMEOUT_SECONDS`, no proactive refusal), because `_can_delegate_to_native_tg_search`
    requires a "supported trigger" flag (`--cpu`/`--json`/...) that a bare search never carries
    and `TG_RUST_FIRST_SEARCH` is off by default, so the native binary's own walk-ceiling gate
    never even ran. It must now fall through to the full CLI instead, which owns the actual
    fast (<1s, no full-tree walk to timeout) refusal via `_should_refuse_unbounded_large_root_scan`.
    """
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    called = {"full_cli": False}
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()  # single ordinary project marker, not a workspace parent
    for index in range(IMPLICIT_SEARCH_WALK_FILE_CEILING + 100):
        (root / f"mod_{index}.py").write_text("x\n", encoding="utf-8")

    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle"])
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
        lambda *_args, **_kwargs: pytest.fail(
            "rg passthrough should not run (raw rg has no walk-ceiling guard)"
        ),
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: called.__setitem__("full_cli", True))

    bootstrap.main_entry()

    assert called["full_cli"] is True


def test_main_entry_should_passthrough_oversized_EXPLICIT_root_search(
    monkeypatch, tmp_path: Path
) -> None:
    """Non-regression companion (Trap #3 parity): the SAME oversized single-project root, but
    with an EXPLICIT path positional, must still take the fast native/rg path uninhibited -- an
    explicit path is a deliberately-scoped root even when huge. Proves the new #105 guard does
    not regress the common scoped-search case (no added latency: the probe is gated on
    `paths_defaulted` and must never even run here)."""
    from tensor_grep.io.scan_limits import IMPLICIT_SEARCH_WALK_FILE_CEILING

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    for index in range(IMPLICIT_SEARCH_WALK_FILE_CEILING + 100):
        (root / f"mod_{index}.py").write_text("x\n", encoding="utf-8")

    seen: dict[str, object] = {}
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root)])
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda binary_name, argv: seen.update({"argv": list(argv)}) or 0,
    )
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert excinfo.value.code == 0
    assert seen["argv"] == ["needle", str(root)]


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
