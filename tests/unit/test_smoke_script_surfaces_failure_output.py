"""The PyPI artifact smoke test must print WHY the artifact failed, not just THAT it did.

`subprocess.run(..., capture_output=True, check=True)` raises a `CalledProcessError` whose string
form carries the argv and the exit status and nothing else. The captured streams hang off the
exception object and are never printed. A smoke test built that way reports that the artifact is
broken while withholding the artifact's own error message -- the single thing the test exists to
obtain.

Receipt: `validate-pypi-artifacts` failed on the v1.101.10 release run (30363114542). The entire
diagnostic content of that log is::

    subprocess.CalledProcessError: Command '[... 'run', '--lang', 'python', '--rewrite', ...]'
    returned non-zero exit status 1.

`tg`'s stderr was captured and discarded, so the failure was neither diagnosable from the log nor
reproducible off it, and `publish-pypi` (which `needs:` this job) skipped -- v1.101.10 was tagged
but never published. Three separate hypotheses were built and falsified against that log before
anyone noticed the cause had simply been thrown away.

These tests pin the helpers rather than the probe bodies: the probes change whenever a smoke step is
added, but "a failure explains itself" is the invariant.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_script_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "smoke_test_pypi_artifacts.py"
    spec = importlib.util.spec_from_file_location("smoke_test_pypi_artifacts_script", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failing_command_prints_both_streams(capsys: pytest.CaptureFixture[str]) -> None:
    """THE DEFECT: the streams were captured and dropped.

    Both markers must appear. stdout matters as much as stderr here -- `tg` reports a refusal or an
    incompleteness envelope on stdout, so a stderr-only report would still hide the common case.
    """
    module = _load_script_module()

    with pytest.raises(SystemExit) as excinfo:
        module._run_checked(
            [
                sys.executable,
                "-c",
                "import sys; print('OUT-MARKER'); print('ERR-MARKER', file=sys.stderr); "
                "sys.exit(3)",
            ],
            what="a command that fails",
        )

    assert excinfo.value.code == 1, "a failed smoke step must exit non-zero"
    captured = capsys.readouterr().err
    assert "OUT-MARKER" in captured, "the failing command's stdout was discarded"
    assert "ERR-MARKER" in captured, "the failing command's stderr was discarded"
    assert "a command that fails" in captured, "the report does not say which step failed"
    assert "3" in captured, "the report does not carry the exit status"


def test_a_successful_command_is_silent_and_returns_its_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CONTROL ARM: without it, a helper that printed unconditionally -- or that treated every
    command as a failure -- would satisfy the test above while making the smoke log useless.

    Also pins that the completed process is returned: the plan probe asserts against `.stdout`, so
    a helper that returned `None` on success would break the caller.
    """
    module = _load_script_module()

    result = module._run_checked(
        [sys.executable, "-c", "print('QUIET-MARKER')"],
        what="a command that succeeds",
    )

    assert result.returncode == 0
    assert "QUIET-MARKER" in result.stdout, "the completed process must carry stdout for callers"
    assert capsys.readouterr().err == "", "a passing step must not write to the failure channel"


def test_a_wrong_output_failure_reports_the_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The second blindness: a bare `assert X in result.stdout` fails with no context either.

    `_fail` is what the plan/apply probes use when the command SUCCEEDS but produced the wrong
    thing -- the case a `CalledProcessError` cannot represent at all.
    """
    module = _load_script_module()

    with pytest.raises(SystemExit) as excinfo:
        module._fail("tg produced the wrong thing", detail="  actual: 'NOTHING-USEFUL'")

    assert excinfo.value.code == 1
    captured = capsys.readouterr().err
    assert "tg produced the wrong thing" in captured
    assert "NOTHING-USEFUL" in captured, "the wrong output itself must reach the log"


def test_no_probe_drives_tg_through_a_nested_interpreter() -> None:
    """The class fix, pinned: no smoke step may run `tg` from inside a `python -c` payload.

    This is the shape the v1.101.10 failure actually had, and getting here took a wrong turn worth
    recording. The first version of this test asserted that no `subprocess.run` pairs
    `capture_output=True` with `check=True` -- true of the fixed file, and it reported PASS. Run
    against the pre-fix file it also reported pass: **zero** violations. The old script's offending
    call was a string handed to `python -c`, so no call node in the file ever carried those
    keywords. The ratchet was guarding a shape this file has never contained.

    A ratchet that cannot fail on the code that caused the incident is decoration. The property
    that actually matters is the nesting: an inner interpreter turns the child's real error into a
    `CalledProcessError` inside the child, which the outer `check=True` re-wraps into a second
    `CalledProcessError` naming only `python -c`. Two layers of wrapping, and the artifact's own
    message is discarded at the first.

    Verified to bite: run against `HEAD~` it flags 2 payloads.
    """
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "smoke_test_pypi_artifacts.py").read_text(encoding="utf-8")

    tree = ast.parse(source)

    # Docstrings are string constants too, and the script's docstrings QUOTE the forbidden pattern
    # in order to explain it. Excluded structurally -- by identity of each scope's leading
    # expression -- rather than by pattern-matching the prose, which would leave a checker that any
    # future wording can silently re-break.
    docstrings = set()
    for scope in ast.walk(tree):
        if not isinstance(
            scope, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            continue
        body = getattr(scope, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            docstrings.add(id(body[0].value))

    payloads = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

    # PREMISE: the matcher really does see this file's string constants. Without it, an AST change
    # that stopped yielding any payload would make the loop below vacuously true.
    assert payloads, "found no string constants to check; the matcher has stopped working"

    for payload in payloads:
        assert "subprocess" not in payload, (
            "a smoke step drives a subprocess from inside a nested interpreter payload. The "
            "child's error becomes a CalledProcessError inside the child, and the outer call "
            "re-wraps it -- the real message is lost. Run the command from this process via "
            f"_run_checked() instead. Offending payload: {payload[:90]!r}"
        )
