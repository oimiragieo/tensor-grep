"""Every argv builder that appends a positional must end its options with `--`. Enumerated.

CWE-88 / the MCP-276 class. A list-argv `subprocess` call blocks a SHELL injection and does
NOTHING about flag injection into the CALLEE's own parser: a path or pattern beginning with `-`
is read by clap/Click as an option. The native binary's `pattern` and `path` positionals
(`rust_core/src/main.rs:690-695`) carry no `allow_hyphen_values`, so the failure mode is not a
crash -- it is a search that runs against a scope the caller never chose and still exits 0.

WHY THIS FILE EXISTS: `AGENTS.md` tracked this sweep BY NAME, in prose, as "the remaining tg
sweep". Prose did not hold it. #860 fixed `cli/main.py::_build_native_tg_search_command` and the
sweep was recorded as done; an independent plan review on 2026-07-31 then found
`agent_capsule.py::_agent_gpu_evidence` still appending a CALLER-SUPPLIED `evidence_path` as a bare
positional -- a live hole, months after the class was "closed". That is the documented signature of
a rule that needs a mechanism rather than a restatement: the second violation is the signal.

THE PROPERTY, stated so it is checkable without judgement: every builder listed below emits the
literal `"--"` before its trailing positional. Not "every builder whose input is untrusted" -- that
phrasing requires a per-site risk assessment, and a sweep whose members each carry their own
argument is a sweep nobody can verify. Uniformity IS the security property here; the doctor GPU
probe carries the sentinel despite tg generating both of its positionals, precisely so that no
future reader has to re-derive whether this one was exempt.

DELIBERATELY SOURCE-ENUMERATED, not behavioural. Several of these builders shell out to a real
binary (ast-grep, ripgrep, the native `tg`) that CI does not always have, so a behavioural test
would skip itself exactly where coverage matters -- the no-op-gate failure mode this repo has been
bitten by before. Reading the source is what makes the coverage total rather than sampled.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

# THE POPULATION: (file, enclosing symbol) for every argv builder that appends a positional to a
# child process's command line. A new builder that is not added here is the failure this file
# exists to catch, which is why the "does this symbol still resolve" arm below is load-bearing.
_BUILDERS: tuple[tuple[Path, str], ...] = (
    (_SRC / "cli" / "main.py", "_build_native_tg_search_command"),
    (_SRC / "cli" / "agent_capsule.py", "_agent_gpu_evidence"),
    (_SRC / "backends" / "ripgrep_backend.py", "_append_search_paths"),
    (_SRC / "backends" / "ast_wrapper_backend.py", "_build_command"),
    (_SRC / "backends" / "ast_wrapper_backend.py", "search_project"),
)

_SENTINEL = '"--"'


def _symbol_body(source: str, name: str) -> str:
    """The source of `def <name>`, to the next top-level-or-method `def` / end of file.

    Indentation-agnostic on purpose: these builders live at module level and as methods, and a
    matcher that only understood one of the two would silently cover half the population.
    """
    match = re.search(rf"^\s*def {re.escape(name)}\b", source, re.M)
    assert match is not None, (
        f"argv builder `{name}` no longer resolves. This census is now BLIND to it -- it cannot "
        "check a symbol it cannot find, and a blind census reports green. Either the function was "
        "renamed (update _BUILDERS) or removed (confirm nothing else builds that argv)."
    )
    indent = len(match.group(0)) - len(match.group(0).lstrip())
    rest = source[match.end() :]
    nxt = re.search(rf"^\s{{0,{indent}}}(def|class) ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_every_argv_builder_emits_the_end_of_options_sentinel() -> None:
    """THE POPULATION CHECK. This is the arm that would have caught the agent_capsule hole."""
    missing = []
    for path, name in _BUILDERS:
        body = _symbol_body(path.read_text(encoding="utf-8"), name)
        if _SENTINEL not in body:
            missing.append(f"{path.name}::{name}")

    assert not missing, (
        f"these argv builders never emit the {_SENTINEL} end-of-options sentinel: {missing}. "
        "A list-argv subprocess call blocks a SHELL injection and does nothing about flag "
        "injection into the callee's parser -- a dash-leading path is read as an option, so the "
        "search runs against a scope nobody chose AND still exits 0. Add the sentinel before the "
        "trailing positional."
    )


def test_the_sentinel_is_unconditional_at_every_builder() -> None:
    """CONTROL ARM: a CONDITIONAL sentinel satisfies the check above and leaves the hole open.

    "Emit `--` only when the value starts with `-`" reads as equivalent and is not: it depends on
    inspecting a value that may be built, normalised or translated (the WSL `wslpath` branch in
    `_agent_gpu_evidence` rewrites the path AFTER the caller supplies it). #860 made the same call
    for the same reason. This arm fails if any sentinel gains a guard.
    """
    for path, name in _BUILDERS:
        body = _symbol_body(path.read_text(encoding="utf-8"), name)
        for line in body.splitlines():
            if _SENTINEL not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not re.match(r"^if\b.*:.*" + re.escape(_SENTINEL), stripped), (
                f"{path.name}::{name} emits the sentinel conditionally: {stripped!r}. It must be "
                "unconditional -- the conditional form leaves the silent path-promotion case open."
            )


def test_the_census_fires_on_a_synthetic_unguarded_builder() -> None:
    """PROVE THE MECHANISM on the arm that matters. An untested gate is untested code."""
    synthetic = (
        "def _build_something(path):\n"
        '    cmd = ["tg", "search", "-F", pattern]\n'
        "    cmd.append(path)\n"
        "    return cmd\n"
        "def _next(): pass\n"
    )
    assert _SENTINEL not in _symbol_body(synthetic, "_build_something"), (
        "the matcher found a sentinel in a body that has none -- the window logic is wrong and "
        "this census would certify anything"
    )


def test_the_census_does_not_fire_on_a_guarded_builder() -> None:
    """CONTROL ARM on the mechanism: it must discriminate, or it gets deleted within a week."""
    synthetic = (
        "def _build_something(path):\n"
        '    cmd = ["tg", "search", "-F", pattern]\n'
        '    cmd.append("--")\n'
        "    cmd.append(path)\n"
        "    return cmd\n"
        "def _next(): pass\n"
    )
    assert _SENTINEL in _symbol_body(synthetic, "_build_something"), (
        "the matcher flags a correctly-guarded builder; it cannot discriminate"
    )
