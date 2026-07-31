"""Every argv builder must place `--` BEFORE its first positional. Behavioural, not source-scanned.

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
itself" because these builders shell out. They do not. Every one below is a pure list-returning
function; `tests/unit/test_native_argv_end_of_options.py` (#860) has been calling one of them
directly since it was written.

So this file calls each builder and asserts the ONE property that matters:
**`argv.index("--")` is less than the index of the first positional.**

## WHAT THIS CANNOT COVER, stated rather than glossed

`rust_core/src/rg_passthrough.rs::ripgrep_operand_args` builds an argv on the Rust side and is
structurally out of reach of a Python test. It is NOT covered here and must not be assumed covered
by this file's name. Its guard belongs in a Rust unit test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli.main import _build_native_tg_search_command
from tensor_grep.cli.mcp_server import _build_index_search_command, _build_rewrite_command
from tensor_grep.core.config import SearchConfig

# Values that MUST end up as positionals, chosen to be unmistakable in an argv and to be exactly
# the shape that gets promoted to a flag when the sentinel is missing or misplaced.
_PATTERN = "-i"
_PATH = "-r"


def _native_search() -> list[str]:
    return _build_native_tg_search_command(
        Path("tg.exe"),
        pattern=_PATTERN,
        paths=[_PATH],
        config=SearchConfig(),  # type: ignore[arg-type]
        ndjson=False,
    )


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


class _StopBuild(BaseException):
    """Unwinds once the argv is captured. A sentinel, not an error path under test.

    `BaseException`, deliberately: `search_project` wraps anything deriving from `Exception` into a
    `BackendExecutionError`, which would swallow the unwind and hide whether the capture ever
    happened. Inheriting from `BaseException` walks straight out through that handler. (The
    `assert captured` below is the arm that catches it if this ever stops being true — a capture
    list that stays empty must FAIL, not quietly return an empty argv that passes every check.)
    """


# THE POPULATION: (label, callable returning the built argv, the positionals it must protect).
#
# A builder that is not here is the failure this file exists to catch — and the FIRST version of
# this file listed five members while the plan that specified it had enumerated ten. Incomplete
# enumeration is the same defect as no enumeration, wearing a number.
_BUILDERS: tuple[tuple[str, object, list[str] | int], ...] = (
    ("cli/main.py::_build_native_tg_search_command", _native_search, [_PATTERN, _PATH]),
    ("cli/mcp_server.py::_build_index_search_command", _mcp_index_search, [_PATTERN, _PATH]),
    ("cli/mcp_server.py::_build_rewrite_command", _mcp_rewrite, [_PATTERN, _PATH]),
    ("backends/ripgrep_backend.py::_append_search_paths", _rg_paths, [_PATH]),
    ("backends/ast_wrapper_backend.py::search_project", _ast_scan_project, [_PATH]),
    # The probe argv's positionals are tg-GENERATED (a fixed sentinel string and a temp file), so
    # the expectation is its SHAPE -- 2 trailing items -- rather than fixed values. Expressed as an
    # int so the check stays real: a builder that emitted zero positionals after `--`, or that let
    # one slide in front of it, still fails.
    ("cli/agent_capsule.py::_agent_gpu_evidence[probe]", lambda: _agent_argv()[0], 2),
    ("cli/agent_capsule.py::_agent_gpu_evidence[evidence]", lambda: _agent_argv()[1], [_PATH]),
    ("cli/main.py::_doctor_gpu_search_runtime_probe", lambda: _doctor_probe_argv(), 2),
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

    assert "--" in argv, f"{label} emits no end-of-options sentinel: {argv}"
    sentinel_at = argv.index("--")
    tail = argv[sentinel_at + 1 :]

    if isinstance(positionals, int):
        # Shape-only: this builder's positionals are tg-generated, so their VALUES are not
        # meaningful to pin. The count still is -- a builder that emitted none, or that let one
        # slide in front of the sentinel, fails here.
        assert len(tail) == positionals, (
            f"{label} expected {positionals} positional(s) after `--`, got {tail!r}. A positional "
            f"missing from the tail is one that slid IN FRONT of the sentinel: {argv}"
        )
        return

    for value in positionals:
        assert value in argv, f"{label} dropped the positional {value!r}: {argv}"
        assert argv.index(value) > sentinel_at, (
            f"{label} places the sentinel AFTER the positional {value!r} -- that value is still "
            f"parsed as a flag by the callee. Position is the property, not presence: {argv}"
        )


@pytest.mark.parametrize(("label", "build", "positionals"), _BUILDERS, ids=lambda v: str(v)[:60])
def test_the_positionals_are_the_trailing_run(label, build, positionals) -> None:  # type: ignore[no-untyped-def]
    """CONTROL ARM: nothing may sit between the sentinel and the positionals.

    Without this, a builder could satisfy the test above while appending another option after the
    sentinel — where the callee reads it as a positional, quietly adding a search root nobody asked
    for. This is the sibling of the misplacement bug and would otherwise be invisible in the same
    way.
    """
    argv = build()
    tail = argv[argv.index("--") + 1 :]

    if isinstance(positionals, int):
        assert len(tail) == positionals, (
            f"{label}: expected exactly {positionals} item(s) after `--`, got {tail!r}. Anything "
            "extra there is read as another positional by the callee."
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
