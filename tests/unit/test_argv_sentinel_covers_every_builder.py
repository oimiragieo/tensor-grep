"""Every argv builder handing a CALLER-INFLUENCED positional to a flag-parsing child must
place `--` before it. Behavioural, not source-scanned.

SCOPE, stated because the unqualified version of that sentence swept in a dozen harmless
tg-generated argvs (`lsp_provider_setup.py`, the pip package specs in `main.py`, `session_daemon.py`,
`dogfood.py`). The property is not "every `subprocess` list ends its options" -- it is that a value
the CALLER can influence never reaches a child parser in flag position.

CWE-88 / the MCP-276 class. A list-argv `subprocess` call blocks a SHELL injection and does NOTHING
about flag injection into the CALLEE's own parser. The native binary's `pattern` and `path`
positionals (`rust_core/src/main.rs:690-695`) carry no `allow_hyphen_values`, so the failure is not
a crash: a directory literally named `-i` is swallowed as `--ignore-case`, the scope silently falls
back, and tg reports a clean successful empty scan at exit 0. Measured on the shipped 1.101.22:

    -e NEEDLE "-i"          ->  "path":"",   total_files:0, total_matches:0, exit 0
    -e NEEDLE -- "-i"       ->  "path":"-i", total_files:1

## WHY THIS FILE IS BEHAVIOURAL, AND WHY IT WAS NOT

The first version of this census read the SOURCE of each builder and asserted the literal `"--"`
appeared somewhere in the function body. An independent adversarial review took it apart, and every
one of its findings was correct:

* **A COMMENT satisfied it.** Three of five members could have their real `command.append("--")`
  deleted and stay GREEN, because a comment *explaining* the sentinel still contained the string.
  The better the guard was documented, the less it was checked.
* **It could not see a sentinel in the WRONG PLACE** — and the same PR shipped one, in the doctor
  GPU probe, sitting *between* the two positionals so the pattern was still unguarded. Presence is
  a proxy; POSITION is the property.
* **It missed builders**, including one the same PR had just added, and a second inside the very
  function being edited whose bare positional was covered by its neighbour's sentinel 86 lines away.
* **Its "unconditional" control arm could never fire** — the regex only matched a single-line
  `if cond: cmd.append("--")`, a form this repo's mandatory `ruff format` immediately expands to two
  lines.

The justification for going source-based was also false: it claimed a behavioural test "would skip
itself" because these builders shell out. Every one below is either a list-returning function or is
capturable at its runner, and `tests/unit/test_native_argv_end_of_options.py` (#860) has been
calling one of them directly since it was written.

(Stated precisely, because a careless version of that sentence was already wrong once: the ast
builder is pure in what it RETURNS, but resolving `argv[0]` goes through `_get_binary_name`, which
`shutil.which`-es four candidates and PROBE-RUNS each. It cannot FAIL to produce an argv -- the last
branch falls back to the literal `"ast-grep"` -- and `test_the_ast_builder_is_environment_independent`
pins that fallback so nothing here is silently conditional on the authoring machine.)

A SECOND round of the same review found two more, and both were POPULATION defects again:

* **`_build_command` was assumed covered by `search_project`. It is not** -- `search_project`
  builds its argv inline and never calls it. Deleting BOTH of `_build_command`'s sentinels left the
  suite fully green. It is also the one member whose regression is DESTRUCTIVE rather than merely
  wrong: a path of `-U` / `--update-all` reaching ast-grep's `run` subcommand is its AUTO-FIX
  switch, so a read-only scan becomes a file rewrite on disk.
* **A count is blind to an ORDER SWAP.** Two members used a bare `len(tail) == 2` because their
  positionals are tg-generated. Exchanging a probe's pattern and path keeps the count and passed --
  in production that searches a directory NAMED like the pattern, for a pattern that is the temp
  path, so the probe reports a status from a scan that never touched the probe file. The
  justification was half wrong too: both PATTERN positionals are hardcoded literals, so only the
  PATH ever needed shape treatment (`_ANY`).

So this file calls each builder and asserts the property that actually matters:
**everything after `--` is exactly the positionals, in order, and nothing precedes it.**

A THIRD round found a member that could not FAIL for the reason it existed. The `[run,stdin]` entry
declared that branch covered while asserting only "no dangling trailing `--`" -- so adding
`cmd.extend(paths)` to the `if stdin_enabled:` branch, putting a caller-supplied value in the argv
bare in exactly that branch, left the suite green. A member that names a branch and cannot fail on
it is worse than no member: it reports the branch as checked.

The fix is the reason the main assertion is now DERIVED rather than curated per entry: **if a
caller-supplied value appears in the argv at all, it must follow `--`**. That holds for every
configuration without anyone deciding in advance which ones have positionals.

THE RECURRING DEFECT IS THE POPULATION, NOT THE ASSERTION. It has been wrong FOUR times -- 5, then
8, then 10, now 13 -- and every miss was the same JUDGEMENT that one builder transitively covered
another. The fourth was surfaced by a mechanical sweep after a careful human pass dismissed the two
`session daemon` call sites as "internal tg commands" without opening them. Each entry below is a
builder that was CALLED and observed. Do not add one by reasoning that it is already covered; call
it -- and treat this list as CURATED, to be re-derived by a sweep each release, not as proven
complete.

## WHAT THIS CANNOT COVER, stated rather than glossed

* `rust_core/src/rg_passthrough.rs::ripgrep_operand_args` builds an argv on the Rust side and is
  structurally out of reach of a Python test. NOT covered here; its guard belongs in a Rust test.
* `cli/dogfood.py::_build_release_docs_worktree_status` is a deliberate EXCLUSION, not an omission:
  it puts `--` before a hardcoded governance path list and invokes `git`, with no caller-supplied
  positional. Recorded here so the next reviewer does not re-derive it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli.bootstrap_native_argv import bootstrap_native_tg_search_argv
from tensor_grep.cli.main import _build_native_tg_search_command
from tensor_grep.cli.mcp_server import _build_index_search_command, _build_rewrite_command
from tensor_grep.core.config import SearchConfig

# Values that MUST end up as positionals, chosen to be unmistakable in an argv and to be exactly
# the shape that gets promoted to a flag when the sentinel is missing or misplaced.
_PATTERN = "-i"
_PATH = "-r"

# The two tg-GENERATED pattern positionals. Hardcoded literals in the product, so they are pinnable
# by value -- which is what catches an order swap that a count alone cannot see.
_PROBE_PATTERN = "tg agent gpu probe sentinel"
_DOCTOR_PATTERN = "tg doctor gpu runtime probe"


def _native_search() -> list[str]:
    return _build_native_tg_search_command(
        Path("tg.exe"),
        pattern=_PATTERN,
        paths=[_PATH],
        config=SearchConfig(),  # type: ignore[arg-type]
        ndjson=False,
    )


def _bootstrap_native_search() -> list[str]:
    return [
        "tg.exe",
        "search",
        *bootstrap_native_tg_search_argv(["--json", _PATTERN, _PATH]),
    ]


def _mcp_index_search() -> list[str]:
    return _build_index_search_command(pattern=_PATTERN, path=_PATH)


def _mcp_rewrite() -> list[str]:
    return _build_rewrite_command(
        pattern=_PATTERN, replacement="x", lang="python", path=_PATH, mode="plan"
    )


def _rg_paths() -> list[str]:
    cmd = ["rg", "--json"]
    RipgrepBackend._append_search_paths(cmd, [_PATH])
    return cmd


def _ast_scan_project() -> list[str]:
    """CAPTURE, not purity: `search_project` builds its argv inline and hands it straight to the
    runner. Intercepting the runner is still behavioural -- the argv under test is the real one the
    function constructs -- and needs no ast-grep binary, which is what makes this coverable at all.
    """
    from tensor_grep.backends import ast_wrapper_backend as mod

    captured: list[list[str]] = []

    class _Captured(mod.AstGrepWrapperBackend):  # type: ignore[misc,name-defined]
        def is_available(self) -> bool:
            return True

        def _run_ast_grep_command(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(list(cmd))
            raise _StopBuild

    try:
        _Captured().search_project(_PATH, "rules.yml")
    except _StopBuild:
        pass
    assert captured, "search_project never reached the runner; the capture is inert"
    return captured[0]


def _ast_build_command(pattern: str, **config_kwargs: object) -> list[str]:
    """`_build_command`'s argv, called directly. It returns `(cmd, context_manager)`.

    THE MEMBER A JUDGEMENT CALL DROPPED, and the only one in this sweep whose regression is
    DESTRUCTIVE rather than merely wrong: a path of `-U` / `--update-all` reaching ast-grep's `run`
    subcommand is its AUTO-FIX switch, so a read-only scan becomes a file rewrite on disk. The
    product's own comment at `ast_wrapper_backend.py:158-162` says exactly that.

    It was assumed covered by `search_project`. It is not -- `search_project` builds its argv inline
    and calls the runner directly, never touching `_build_command`. Proven by deleting both of this
    function's sentinels and watching the suite stay green.
    """
    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.core.config import SearchConfig as _Config

    backend = AstGrepWrapperBackend()
    # Pin argv[0] instead of resolving it. `_get_binary_name` `shutil.which`-es four candidates and
    # PROBE-RUNS each (measured: 3 real `ast-grep --version` spawns for 3 calls here), which is
    # wasted subprocess cost in a test that never asserts on argv[0]. The sibling `_ast_scan_project`
    # already stubs `is_available` for the same reason.
    backend._resolved_binary_name = "ast-grep"
    cmd, context = backend._build_command(
        pattern,
        [_PATH],
        _Config(**config_kwargs),  # type: ignore[arg-type]
    )
    with context:
        return list(cmd)


def _ast_run_argv() -> list[str]:
    return _ast_build_command("needle")


def test_the_ast_builder_is_environment_independent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The ast argv must build IDENTICALLY on a runner with no ast-grep installed.

    Precision that matters, because a careless version of this claim has already been wrong once in
    this file's history: `_build_command` is pure in what it RETURNS, but resolving `argv[0]` goes
    through `_get_binary_name`, which `shutil.which`-es four candidates and PROBE-RUNS each one
    (`ast_wrapper_backend.py:98-128`). So it can spawn subprocesses -- it just cannot fail to
    produce an argv, because the last branch falls back to the literal `"ast-grep"`.

    This arm forces that fallback. Without it, every assertion above would be silently
    conditional on the authoring machine having ast-grep on PATH -- the shape of environment
    dependence that makes a test pass locally and behave differently in CI.
    """
    import shutil

    from tensor_grep.backends import ast_wrapper_backend as mod

    monkeypatch.setattr(shutil, "which", lambda name: None)
    backend = mod.AstGrepWrapperBackend()
    cmd, context = backend._build_command("needle", [_PATH], None)
    with context:
        argv = list(cmd)

    assert argv[0] == "ast-grep", f"the no-ast-grep fallback changed shape: {argv}"
    tail = argv[argv.index("--") + 1 :]
    assert tail == [_PATH], f"the sentinel/positional shape differs when ast-grep is absent: {argv}"


def _ast_run_stdin_argv() -> list[str]:
    """The `ast_stdin=True` arm.

    The run form's sentinel lives in the `else:` of `if stdin_enabled`, so this configuration takes
    a DIFFERENT branch. A builder whose guard is config-conditional has as many members as it has
    configurations, and checking only the default arm is sampling.
    """
    return _ast_build_command("needle", ast_stdin=True)


def _ast_multiline_argv() -> list[str]:
    """The inline-rule form: a multiline pattern routes to `scan --rule <tmpfile> -- <paths>`."""
    return _ast_build_command("def a():\n    pass")


def _wslpath_argv() -> list[str]:
    """`translate_path_for_windows_binary`'s argv (round 3, F11).

    `-w` takes no value, so the path is a bare positional -- and `agent_capsule.py` hands this the
    USER's search root. Measured on the real binary rather than assumed: `wslpath -w "-m"` prints
    USAGE (the path was consumed as a flag) while `wslpath -w -- "-m"` prints `-m`. It fails closed
    (a swallowed path yields `None` -> `path_domain_mismatch`), which is why it is lower severity --
    but a sweep whose members each carry a private severity argument is a sweep nobody can check.
    """
    from tensor_grep.cli import runtime_paths as mod

    captured: list[list[str]] = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = ""

    def _capture(argv, **_kwargs):  # type: ignore[no-untyped-def]
        captured.append([str(a) for a in argv])
        return _Result()

    import shutil

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(shutil, "which", lambda name: "wslpath")
        monkeypatch.setattr(mod.subprocess, "run", _capture)
        mod.translate_path_for_windows_binary(_PATH)
    finally:
        monkeypatch.undo()

    assert captured, "translate_path_for_windows_binary never reached subprocess.run"
    return captured[0]


def test_the_generated_upgrade_script_guards_its_daemon_root() -> None:
    """The scheduled-upgrade helper's two `session daemon` argvs (round 3, F12).

    CHECKED AS TEXT, and this is the one place in this file where that is the RIGHT tool rather
    than the lazy one: the argv lives inside `helper_code = textwrap.dedent(...)` (main.py:15399),
    a standalone script written to disk and run by the Windows scheduler. It cannot be imported
    and cannot be called -- the artifact under test IS source text, so reading the text is reading
    the artifact.

    A first cut extracted a shared `_session_daemon_argv` helper to make this callable. That would
    have raised `NameError` inside the generated script, which cannot import tg;
    `test_upgrade_scheduled_windows_helper_restarts_preexisting_session_daemon` caught it by
    pinning the generated script's text. **Refactoring code that lives inside a generated string is
    not the same as refactoring code.**

    THE DEFECT: `daemon_root` is caller-influenced -- the PATH the user gave
    `tg session daemon start <PATH>`, persisted in daemon state and round-tripped back into this
    argv. The callee is a real Typer/Click command whose `path` argument DEFAULTS TO `"."`, so a
    dash-leading root consumed as an option does NOT error: the daemon is started or queried at the
    CWD instead of the intended root, and the status check then reports on the wrong daemon.
    """
    import inspect
    import re

    from tensor_grep.cli import main as cli_main

    source = inspect.getsource(cli_main)
    occurrences = re.findall(
        r'"(status|start)",\s+"--json",\s+"--",\s+daemon_root,',
        source,
    )
    assert sorted(occurrences) == ["start", "status"], (
        "the generated upgrade script's `session daemon` argvs no longer place `--json` and the "
        f"`--` sentinel before `daemon_root`. Found: {occurrences!r}. A dash-leading daemon root "
        "would be consumed as an option and the Typer default of '.' would silently substitute "
        "the CWD."
    )


class _StopBuild(BaseException):
    """Unwinds once the argv is captured. A sentinel, not an error path under test.

    `BaseException`, deliberately: `search_project` wraps anything deriving from `Exception` into a
    `BackendExecutionError`, which would swallow the unwind and hide whether the capture ever
    happened. Inheriting from `BaseException` walks straight out through that handler. (The
    `assert captured` below is the arm that catches it if this ever stops being true — a capture
    list that stays empty must FAIL, not quietly return an empty argv that passes every check.)
    """


# `_ANY` marks a positional whose VALUE is generated at runtime (a temp path) and so cannot be
# pinned. It still occupies a slot, so ORDER and COUNT stay checked.
#
# It replaces a bare `len(tail) == N` count, which a second review proved blind to a positional
# ORDER SWAP: exchanging the probe's pattern and path keeps the count and passes, while in
# production it searches a directory NAMED "tg agent gpu probe sentinel" for a pattern that is the
# temp path -- the probe reports a status from a scan that never touched the probe file. And the
# justification for the count form was half wrong: both PATTERN positionals here are hardcoded
# literals (`agent_capsule.py:1652`, `main.py:2949`), so only the PATH ever needed shape treatment.
_ANY = object()

# THE POPULATION: (label, callable returning the built argv, the positionals it must protect).
#
# A builder that is not here is the failure this file exists to catch, and the count has now been
# wrong TWICE: five members in the first cut (the plan had enumerated ten), then eight -- still
# missing `_build_command`'s two shapes, whose regression is the only one in this sweep that
# REWRITES FILES rather than merely searching the wrong scope. Both misses were judgement calls
# about transitive coverage. Judgement is what keeps failing here; each entry below is a builder
# that was CALLED and observed, not one reasoned about.
_BUILDERS: tuple[tuple[str, object, list[object]], ...] = (
    ("cli/main.py::_build_native_tg_search_command", _native_search, [_PATTERN, _PATH]),
    (
        "cli/bootstrap_native_argv.py::bootstrap_native_tg_search_argv",
        _bootstrap_native_search,
        [_PATTERN, _PATH],
    ),
    ("cli/mcp_server.py::_build_index_search_command", _mcp_index_search, [_PATTERN, _PATH]),
    ("cli/mcp_server.py::_build_rewrite_command", _mcp_rewrite, [_PATTERN, _PATH]),
    ("backends/ripgrep_backend.py::_append_search_paths", _rg_paths, [_PATH]),
    ("backends/ast_wrapper_backend.py::search_project", _ast_scan_project, [_PATH]),
    # `_build_command` builds TWO distinct argvs and `search_project` calls NEITHER -- it builds its
    # own inline. Treating one as covering the other is the transitive-coverage judgement that a
    # second review disproved by deleting both sentinels here and watching the suite stay green.
    # The run form's sentinel additionally sits in the `else:` of `if stdin_enabled`, so it is
    # CONFIG-CONDITIONAL and needs the stdin arm too -- a builder whose guard depends on config has
    # as many members as it has configurations.
    ("backends/ast_wrapper_backend.py::_build_command[run]", _ast_run_argv, [_PATH]),
    ("backends/ast_wrapper_backend.py::_build_command[run,stdin]", _ast_run_stdin_argv, []),
    ("backends/ast_wrapper_backend.py::_build_command[multiline]", _ast_multiline_argv, [_PATH]),
    (
        "cli/agent_capsule.py::_agent_gpu_evidence[probe]",
        lambda: _agent_argv()[0],
        [_PROBE_PATTERN, _ANY],
    ),
    ("cli/agent_capsule.py::_agent_gpu_evidence[evidence]", lambda: _agent_argv()[1], [_PATH]),
    (
        "cli/main.py::_doctor_gpu_search_runtime_probe",
        lambda: _doctor_probe_argv(),
        [_DOCTOR_PATTERN, _ANY],
    ),
    # ROUND 3. Both found only AFTER the population had been "derived" twice already -- and the
    # second was surfaced by a mechanical sweep after a human pass dismissed the same two call
    # sites as "internal tg session commands" without opening them. Four judgement failures on one
    # population is why the assertion above is now derived from the argv rather than curated here.
    ("cli/runtime_paths.py::translate_path_for_windows_binary", _wslpath_argv, [_PATH]),
)


def _doctor_probe_argv() -> list[str]:
    """The doctor GPU probe's argv, captured at `subprocess.run`.

    In the population because the PR that added its sentinel did NOT add it here -- the census
    shipped without covering the builder the same change had just touched. A census that omits the
    code its own PR edited is the clearest possible demonstration that the population was assembled
    from memory rather than derived.
    """
    import json as _json
    import subprocess
    import tempfile

    import pytest as _pytest

    from tensor_grep.cli import main as cli_main

    captured: list[list[str]] = []

    def _fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        captured.append([str(a) for a in command])
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "routing_gpu_device_ids": [],
            "path": str(command[-1]),
            "matches": [{"file": str(command[-1]), "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, _json.dumps(payload), "")

    monkeypatch = _pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp:
        native = Path(tmp) / "tg.exe"
        native.write_text("native", encoding="utf-8")
        try:
            monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)
            cli_main._doctor_gpu_search_runtime_probe(native)
        finally:
            monkeypatch.undo()

    assert captured, "the doctor probe never reached subprocess.run; this capture is inert"
    return captured[0]


def _agent_argv() -> list[list[str]]:
    """Both argvs `_agent_gpu_evidence` builds, in construction order.

    TWO builders live in this one function, 86 lines apart. The first version of this census read
    the whole 226-line function body as a single string, so the SECOND builder's bare positional was
    "covered" by the FIRST builder's sentinel — a member satisfied by a check it does not meet.
    Splitting them into two population entries is the point: a function is not the unit, an argv is.
    """
    from tensor_grep.cli import agent_capsule as mod

    captured: list[list[str]] = []

    def _capture(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured.append([str(a) for a in argv])
        if len(captured) < 2:
            # Let the probe "succeed" so construction continues to the evidence argv.
            return {"status": "ok", "payload": {"matches": [], "total_matches": 0}}
        raise _StopBuild

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(mod, "_run_agent_gpu_json_command", _capture)
        monkeypatch.setattr(mod, "resolve_native_tg_binary", lambda: Path("tg.exe"))
        # Stub ONLY the route-classification gate between the two builders. This test is about
        # ARGV SHAPE; making the probe payload realistic enough to pass a real GPU-route
        # classification would couple it to routing semantics it does not check, and the first
        # thing to rot when those change.
        monkeypatch.setattr(mod, "_native_gpu_route_rejection", lambda payload: None)
        monkeypatch.setattr(mod, "_gpu_route_fields", lambda payload: {})
        try:
            mod._agent_gpu_evidence("needle", _PATH, gpu_device_ids=[0], max_files=1, timeout_s=1.0)
        except _StopBuild:
            pass
    finally:
        monkeypatch.undo()

    assert len(captured) == 2, (
        f"expected BOTH argvs from _agent_gpu_evidence, captured {len(captured)}. A capture that "
        "returns fewer than it should silently shrinks the population this file checks."
    )
    return captured


@pytest.mark.parametrize(("label", "build", "positionals"), _BUILDERS, ids=lambda v: str(v)[:60])
def test_the_sentinel_precedes_every_positional(label, build, positionals) -> None:  # type: ignore[no-untyped-def]
    """THE PROPERTY. Position, not presence.

    A `--` that appears AFTER any positional protects nothing for the value that preceded it, and
    a source-scanning check reports both as identical.
    """
    argv = build()

    # THE DERIVED PROPERTY, and the one that makes an empty-positional member able to FAIL.
    #
    # An earlier cut declared the `--stdin` configuration "covered" while asserting only that no
    # dangling `--` was emitted. That entry could not fail for the reason it existed: adding
    # `cmd.extend(paths)` to the `if stdin_enabled:` branch -- putting a caller-supplied value in
    # the argv, bare, in exactly that branch -- left the whole suite green. A member that names a
    # branch and cannot fail on it is worse than no member: it reports the branch as checked.
    #
    # So the property is DERIVED from the argv rather than hardcoded per entry: **if a
    # caller-supplied value appears in the argv at all, it must follow `--`.** That holds for every
    # configuration without anyone deciding in advance which ones have positionals.
    for supplied in (_PATTERN, _PATH):
        if supplied not in argv:
            continue
        assert "--" in argv, (
            f"{label} places the caller-supplied value {supplied!r} in the argv with NO "
            f"end-of-options sentinel anywhere: {argv}"
        )
        assert argv.index(supplied) > argv.index("--"), (
            f"{label} places the caller-supplied value {supplied!r} BEFORE the sentinel -- it is "
            f"still parsed as a flag by the callee: {argv}"
        )

    if not positionals:
        # Declared-empty configurations have nothing further to check; the derived loop above is
        # what actually guards them.
        assert "--" not in argv or argv[-1] != "--", (
            f"{label} emits a trailing `--` with no positionals after it: {argv}"
        )
        return

    assert "--" in argv, f"{label} emits no end-of-options sentinel: {argv}"
    sentinel_at = argv.index("--")
    tail = argv[sentinel_at + 1 :]

    assert len(tail) == len(positionals), (
        f"{label} expected {len(positionals)} positional(s) after `--`, got {tail!r}. A positional "
        f"missing from the tail is one that slid IN FRONT of the sentinel: {argv}"
    )
    for index, expected in enumerate(positionals):
        if expected is _ANY:
            continue
        assert tail[index] == expected, (
            f"{label} positional {index} is {tail[index]!r}, expected {expected!r}. A SWAP keeps "
            f"the count and changes the meaning -- here it would search a directory named like "
            f"the pattern, for a pattern that is the path: {argv}"
        )


@pytest.mark.parametrize(("label", "build", "positionals"), _BUILDERS, ids=lambda v: str(v)[:60])
def test_the_positionals_are_the_trailing_run(label, build, positionals) -> None:  # type: ignore[no-untyped-def]
    """CONTROL ARM: nothing may sit between the sentinel and the positionals.

    Without this, a builder could satisfy the test above while appending another option after the
    sentinel — where the callee reads it as a positional, quietly adding a search root nobody asked
    for. This is the sibling of the misplacement bug and would otherwise be invisible in the same
    way.
    """
    if not positionals:
        return
    argv = build()
    tail = argv[argv.index("--") + 1 :]

    if any(p is _ANY for p in positionals):
        assert len(tail) == len(positionals), (
            f"{label}: expected exactly {len(positionals)} item(s) after `--`, got {tail!r}. "
            "Anything extra there is read as another positional by the callee."
        )
        return

    assert tail == positionals, (
        f"{label}: everything after `--` must be exactly the user positionals, in order. "
        f"Anything else there is read as an extra positional by the callee. Got {tail!r}, "
        f"expected {positionals!r}"
    )


def test_a_dash_leading_value_survives_as_a_positional() -> None:
    """The injection case end-to-end on the main delegation builder.

    `-i` and `-r` are real ripgrep/clap flags. If the sentinel were absent or misplaced, the callee
    would consume them and the search would run against the cwd — reporting success over the wrong
    scope, which is the silent half of this bug class.
    """
    argv = _native_search()
    tail = argv[argv.index("--") + 1 :]

    assert tail == [_PATTERN, _PATH], (
        f"a dash-leading pattern/path did not survive as positionals: {argv}"
    )


def test_the_check_fires_on_a_misplaced_sentinel() -> None:
    """PROVE THE MECHANISM on the arm the previous version could not see.

    This is the exact defect that shipped and was missed: the sentinel present, but positioned
    between two positionals. A presence-only check passes it. This one must not.
    """
    misplaced = ["tg", "search", "-F", "PATTERN", "--", "PATH"]
    sentinel_at = misplaced.index("--")

    assert misplaced.index("PATTERN") < sentinel_at, "fixture is not the misplaced shape"
    # The property under test, applied by hand to the synthetic argv: PATTERN precedes the
    # sentinel, so the assertion in the population test above would fail for it.
    assert not all(misplaced.index(v) > sentinel_at for v in ("PATTERN", "PATH")), (
        "the position check accepts a sentinel sitting between two positionals -- it is blind to "
        "the very defect that motivated rewriting this file"
    )


def test_the_check_does_not_fire_on_a_correct_builder() -> None:
    """CONTROL ARM on the mechanism: it must discriminate, or it gets deleted within a week."""
    correct = ["tg", "search", "-F", "--", "PATTERN", "PATH"]
    sentinel_at = correct.index("--")

    assert all(correct.index(v) > sentinel_at for v in ("PATTERN", "PATH"))
    assert correct[sentinel_at + 1 :] == ["PATTERN", "PATH"]
