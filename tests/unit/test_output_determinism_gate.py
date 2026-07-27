"""#311 -- determinism as a NAMED, gated invariant rather than an implication of golden tests.

The repo has extensive byte-identity language in `docs/CONTRACTS.md` and many golden tests. None
of them check the property this file checks, and the distinction is the whole point:

    A golden test runs the command ONCE and compares it to a stored expectation. That catches
    drift away from the golden. It cannot catch run-to-run instability -- if a field were emitted
    in set-iteration order, the golden would pass whenever it happened to match and read as a
    FLAKE when it did not. "Flaky golden" is what unpinned nondeterminism looks like from the
    inside, which is exactly why it gets re-run instead of diagnosed.

Why an agent should care: `tg` is consumed by machines that diff outputs between runs, cache on
them, and build baseline/suppression fingerprints from them. A field that moves on its own makes
every diff show phantom changes and makes a fingerprint fail to match itself.

## What is measured, and how it is kept honest

Each machine-facing surface runs three times in SEPARATE PROCESSES under three different
`PYTHONHASHSEED` values, and the JSON must be identical apart from an explicitly declared set of
volatile fields.

1.  **Separate processes, not repeated in-process calls.** A second in-process call inherits
    `lru_cache`, module globals and warm session state, so it would be trivially identical -- a
    check that cannot fail. A subprocess cannot inherit what it never had.

2.  **`PYTHONHASHSEED` is varied deliberately.** Python randomises string hashing per process, so
    anything emitted in set/dict-iteration order changes bytes between seeds. Left to chance that
    is a coin flip this gate could miss for years; pinned to three known-different seeds it
    becomes a reliable detector of the most common nondeterminism source in Python. The
    difference between "we ran it three times" and "we perturbed the condition that would expose
    the defect".

3.  **The volatile set is an ALLOW-LIST, and it is asserted to be LIVE.** Normalising away
    whatever happens to differ is how this kind of gate quietly becomes decoration -- strip enough
    and nothing can ever fail (oracle Form 7). So the volatile fields are enumerated by name with
    a reason, anything else that moves fails with the JSON path named, and a separate test asserts
    every declared entry is actually present in real output. A stale entry is dead allow-list
    surface that would mask a future defect.

## Proof that this gate can actually fail (2026-07-26)

Green on the first run means nothing, so the detector was verified by INJECTING nondeterminism
into live emit sites and confirming a red with the field named:

* `codemap.py` -- `sorted(set(files) | set(tests))` -> `list(...)`: RED, 4 fields moved.
* `main.py:6688` -- `"backends": sorted(backend_names_used)` -> `list(...)`: RED, named
  `.backends[0]` / `.backends[1]`.

Three EARLIER injection attempts did NOT trip it, and every one was a bad injection rather than a
blind spot -- worth recording, because the same mistake is easy to repeat when extending this file:

1.  Un-sorting `for folder in sorted(folders)` -- `folders` is a **dict**, so `list()` preserves
    insertion order. That changes the order deterministically; it does not make it unstable.
2.  The same line also builds MARKDOWN, which this gate does not read.
3.  Un-sorting `backends` in `ast_workflows.py` -- that is the sidecar scan path. `--ruleset`
    is in bootstrap's `_SCAN_FULL_CLI_FLAGS`, so the tested invocation routes to `main.py`
    instead and the mutated line never executed.

The rule those three share: **verify the mutation site is on the path under test AND that it
genuinely varies, before concluding anything from a green.** For (3) the premise was checked
directly -- printing `backends` under each seed showed 0 and 1 agreeing while 2 differed, which is
also why three seeds are used rather than two.

## The gate's first RED was its own bug (2026-07-26) -- and it blamed the product

On windows-latest (py3.11 and py3.12) this file failed with *"codemap-json is NOT deterministic
-- 19 field(s) moved"*. The product was fine. Every one of those 19 fields was the per-run scratch
directory: `str(sandbox)` never appeared in the emitted text, so all three replaces missed, the
path survived into the comparison, and two runs with different temp directories duly "differed".
Linux and macOS passed, so it read like a genuine platform-specific defect.

Two fixes, and the second matters more than the first:

1.  Normalise the RESOLVED spelling as well as the handed one. The tool resolves the path it is
    given, and where the two differ -- 8.3-shortened or junctioned TEMP, casing, UNC prefix --
    replacing only the handed form matches nothing at all.
2.  **Assert that normalisation actually fired.** A gate that misreports its own gap as a product
    finding is worse than no gate: it is confidently wrong in the product's voice, and it costs a
    full CI cycle plus a wrong root-cause before anyone doubts the instrument. The check is the
    scratch prefix, which can reach stdout only via an unreplaced sandbox path.

Fix 1 is a hypothesis about which spelling CI emits -- it could not be reproduced on the author's
Windows box, where the two spellings are identical. Fix 2 is what makes that acceptable: if the
hypothesis is wrong, the next run says NORMALISATION GAP and prints the excerpt containing the
third spelling, instead of accusing the product again. Verified bidirectionally by making both
spellings non-matching (the CI condition): red with NORMALISATION GAP, not with "fields moved".

## Measured state at the time of writing (v1.98.25)

4 of the 7 surfaces are byte-identical with NO normalisation at all: `inventory`,
`docs-coverage`, `imports`, `scan`. The other three differ ONLY in provenance/runtime metadata --
`codemap.generated_at` (a wall-clock stamp) and `daemon_response_cache.{hits,status}` (whether a
warm daemon happened to serve the call). tg's actual RESULTS were already deterministic; what
this gate adds is that they cannot stop being so without CI saying which field moved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

import pytest


class _Outcome(NamedTuple):
    """One subprocess run: the normalised stdout plus the channels needed to explain a failure."""

    text: str
    returncode: int
    stderr: str


_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = "tests/fixtures/codemap_repo"

# ONE constant for the scratch prefix, used both to CREATE the sandbox and to detect a sandbox
# path that survived normalisation. They must not be able to drift: if the prefix is changed in
# one place only, the survival check silently stops matching and goes back to reporting a harness
# gap as a product defect. See `_run`.
_SCRATCH_PREFIX = "tg-determinism-"

# Fixed so any failure is reproducible. 0 disables hash randomisation; the others are arbitrary
# but different, which is all that is needed to shake out iteration-order leaks.
_HASH_SEEDS = ("0", "1", "2")

# THE DECLARED VOLATILE SET. Every entry needs a reason, and `test_every_declared_volatile_key_is
# _live` fails if one stops appearing -- an unused entry is a hole nothing is watching.
#
# Split by whether the field is ALWAYS emitted, because only an always-present field can have its
# liveness enforced -- and a liveness check that cannot fail is the very thing this file exists to
# avoid.
#
#   generated_at -- wall-clock provenance stamp on the codemap payload. Always present, so
#                   `test_every_always_present_volatile_key_is_live` holds it to account.
_VOLATILE_ALWAYS: tuple[str, ...] = ("generated_at",)

#   daemon_response_cache -- whether a WARM DAEMON served this call (`hits`/`status`). Genuinely
#                   runtime state: the same query is correct either way and the field exists to
#                   disclose which path answered. Its PRESENCE is itself conditional -- it appears
#                   only when the daemon path is involved, which this test cannot force without
#                   starting a daemon (expensive, and it would make the gate depend on daemon
#                   lifecycle rather than on determinism). So it is declared here and deliberately
#                   NOT liveness-checked; the honest tradeoff is written down rather than papered
#                   over by relaxing the assertion until it passed.
_VOLATILE_CONDITIONAL: tuple[str, ...] = ("daemon_response_cache",)

_VOLATILE_KEYS: tuple[str, ...] = _VOLATILE_ALWAYS + _VOLATILE_CONDITIONAL

_REDACTED = "<volatile>"

# (id, argv, minimum plausible output size). The floors are PREMISE checks set well below observed
# sizes: comparing two empty strings is trivially equal, so a surface that went silent would keep
# this file green forever while checking nothing.
_SURFACES: tuple[tuple[str, list[str], int], ...] = (
    ("codemap-json", ["codemap", _FIXTURE, "--json"], 500),
    ("inventory-json", ["inventory", _FIXTURE, "--json"], 300),
    ("docs-coverage-json", ["docs-coverage", _FIXTURE, "--json"], 100),
    ("orient-json", ["orient", _FIXTURE, "--json"], 500),
    ("callers-json", ["callers", "process", "--json", _FIXTURE], 500),
    ("imports-json", ["imports", f"{_FIXTURE}/pkg/core.py", "--json"], 100),
    (
        "scan-ruleset-json",
        ["scan", "--ruleset", "secrets-basic", "--path", _FIXTURE, "--json"],
        300,
    ),
)


def _run(argv: list[str], hash_seed: str) -> _Outcome:
    """One invocation, in a PRISTINE COPY of the fixture.

    The copy is not hygiene -- it is what makes the comparison valid. `tg codemap` WRITES its
    output (`docs/code-map/`) into the tree it scans, so without isolation run 1 mutates the input
    for runs 2 and 3: CI caught `.revision.dirty` flipping False->True and `scan_limit
    .scanned_files` going 7->13 between seeds. That is accumulated state, not a hash-seed effect,
    and it would have been reported as a product nondeterminism bug that does not exist.

    It also explains why this passed locally and failed on CI: my working copy already had the
    generated files from an earlier run, so every run saw the same steady state. CI starts clean,
    so run 1 was the only one that saw a pristine tree. A check whose result depends on whether
    you have run it before is not measuring the software.
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    # `src` first so the worktree under test wins over any installed distribution: a stale
    # site-packages copy shadowing `src` has produced false results in this repo before.
    env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    with tempfile.TemporaryDirectory(prefix=_SCRATCH_PREFIX) as scratch:
        sandbox = Path(scratch) / "repo"
        shutil.copytree(_REPO_ROOT / _FIXTURE, sandbox)
        # Rewrite the fixture path in argv to point at this run's private copy.
        localized = [
            str(sandbox) if arg == _FIXTURE else arg.replace(_FIXTURE, str(sandbox)) for arg in argv
        ]
        completed = subprocess.run(
            [sys.executable, "-m", "tensor_grep", *localized],
            cwd=scratch,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        # The sandbox path differs per run by construction, so it can never be compared. Map it
        # back to a stable token -- this normalises the PATH, never a RESULT.
        #
        # THREE forms per SPELLING, because all three really occur in the output. The
        # JSON-ESCAPED one is the trap: inside a JSON document a Windows path's separators are
        # doubled, so a single-backslash replace silently misses every absolute path. It then
        # surfaces not as a path mismatch but as absent-vs-present dict KEYS
        # (`per_page_token_estimates.<abs path>`), which reads like a product nondeterminism bug
        # rather than a normalisation gap. Longest-first, so the escaped form is consumed before
        # the plain one can partially match.
        #
        # TWO SPELLINGS, because the tool does not necessarily echo back the path it was handed:
        # it resolves it. Where the two differ -- an 8.3-shortened or junctioned TEMP, a casing
        # difference, a UNC prefix -- replacing only the handed form matches NOTHING, and the
        # whole normalisation silently no-ops. Resolved first: it is the longer/more-specific
        # spelling wherever they differ. `sandbox` still exists here, so `.resolve()` is real.
        native = str(sandbox)
        resolved = str(sandbox.resolve())
        text = completed.stdout
        for spelling in dict.fromkeys((resolved, native)):  # ordered, de-duplicated
            for form in (
                spelling.replace("\\", "\\\\"),
                spelling,
                spelling.replace("\\", "/"),
            ):
                text = text.replace(form, "<fixture>")

        # PREMISE: normalisation must actually have FIRED. This is the check whose absence cost a
        # full CI cycle and a wrong diagnosis on 2026-07-26 (windows-latest, py3.11 + py3.12):
        # every replace above missed, each seed's private scratch directory therefore survived
        # into the comparison, and the gate announced "codemap-json is NOT deterministic -- 19
        # field(s) moved". Nineteen. Every one of them the temp path. The product was fine; the
        # instrument was broken, and it accused the product in a voice indistinguishable from a
        # real finding.
        #
        # The marker is the scratch prefix, which reaches stdout by exactly one route: a sandbox
        # path that was not replaced. So this cannot fire on a genuine product defect, and it
        # cannot stay silent on a normalisation gap -- which is the whole point of putting it
        # here rather than trusting the replaces to have worked.
        assert _SCRATCH_PREFIX not in text, (
            f"NORMALISATION GAP (harness bug, NOT a product finding): the sandbox path survived "
            f"into {argv[0]}'s output, so the seed comparison would report every path-bearing "
            f"field as 'moved'.\n"
            f"  handed to the tool: {native}\n"
            f"  resolved form:      {resolved}\n"
            f"  Neither spelling matched the emitted text, so the tool is echoing a THIRD form. "
            f"Find it in the excerpt below and add its spelling above.\n"
            f"  excerpt: {text[:400]}"
        )
        # Carry the exit code and stderr, not just stdout. When the premise below fires, the ONLY
        # question is *why* the surface went quiet -- and stdout is empty by definition at that
        # point, so the answer lives entirely in the two channels this used to discard. Dropping
        # them turned a one-cycle diagnosis into guesswork on a platform I cannot reproduce
        # locally: this gate is the instrument, and an instrument that cannot say why it failed
        # is only half built.
        return _Outcome(text=text, returncode=completed.returncode, stderr=completed.stderr)


def _redact_volatile(value: Any) -> Any:
    """Replace declared-volatile fields, and ONLY those, wherever they appear."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if key in _VOLATILE_KEYS else _redact_volatile(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_redact_volatile(item) for item in value]
    return value


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """JSON paths -> leaf values, so a divergence can be reported as a path rather than a blob."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            out.update(_flatten(inner, f"{prefix}.{key}"))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


@pytest.mark.parametrize(
    ("surface_id", "argv", "min_bytes"), _SURFACES, ids=[s[0] for s in _SURFACES]
)
def test_machine_facing_output_is_stable_across_processes_and_hash_seeds(
    surface_id: str, argv: list[str], min_bytes: int
) -> None:
    outcomes = [_run(argv, seed) for seed in _HASH_SEEDS]
    raw = [outcome.text for outcome in outcomes]

    # OPTIONAL-DEPENDENCY SKIP, keyed to an OBSERVABLE predicate rather than a platform guess.
    #
    # `scan --ruleset secrets-basic` uses metavar patterns (`$SECRET`), which fail CLOSED to the
    # ast-grep wrapper by design -- the native tree-sitter fallback deliberately refuses them
    # (`backends/ast_backend.py:691`, and see its comment at :683 naming "CI without the ast-grep
    # binary" as a known condition). Ubuntu CI has no ast-grep, so the surface cannot produce
    # output there and the premise below fired on a real, expected environment fact.
    #
    # Skipped on the tool's OWN error string, not on `sys.platform` or a `which` probe: I already
    # got this wrong once by checking `command -v ast-grep` (the CLI) when the wrapper is resolved
    # differently, so a local control passed while CI failed. The command's own refusal is the
    # only predicate that cannot drift away from the behaviour it guards.
    #
    # NOT a silent skip: it is loud, names the surface, and the suite still covers the other six
    # surfaces on every platform -- so this cannot quietly hollow the gate out. On any machine
    # WITH ast-grep (developer boxes, the windows leg) the surface runs normally.
    if outcomes[0].returncode != 0 and "ast-grep wrapper" in outcomes[0].stderr:
        pytest.skip(
            f"{surface_id} needs the ast-grep wrapper, absent in this environment "
            f"(exit {outcomes[0].returncode}): {outcomes[0].stderr.strip()[:200]}"
        )

    # PREMISE: identical-but-empty is trivially true. If a surface legitimately goes quiet that is
    # a product change to acknowledge here, not something to wave through by lowering the floor.
    #
    # The exit code and stderr are in the message because a quiet surface says nothing about WHY
    # it went quiet, and this gate runs on platforms the author cannot reproduce locally. Without
    # them the next reader gets "0 bytes" and a guess; with them they get the reason on the first
    # read of the log.
    assert len(raw[0]) >= min_bytes, (
        f"premise failed: {surface_id} produced {len(raw[0])} bytes (< {min_bytes}). Either the "
        "command broke or its output moved; comparing empty strings proves nothing.\n"
        f"  exit code: {outcomes[0].returncode}\n"
        f"  stderr:    {outcomes[0].stderr.strip()[:2000] or '<empty>'}\n"
        f"  argv:      {argv}"
    )

    parsed = []
    for seed, text in zip(_HASH_SEEDS, raw, strict=True):
        try:
            parsed.append(_redact_volatile(json.loads(text)))
        except json.JSONDecodeError as exc:  # pragma: no cover - a broken surface, not drift
            pytest.fail(f"{surface_id} (seed={seed}) did not emit valid JSON: {exc}")

    baseline = _flatten(parsed[0])
    for seed, other in zip(_HASH_SEEDS[1:], parsed[1:], strict=True):
        candidate = _flatten(other)
        moved = sorted(
            path
            for path in set(baseline) | set(candidate)
            if baseline.get(path, "<absent>") != candidate.get(path, "<absent>")
        )
        if not moved:
            continue
        detail = "\n".join(
            f"  {path}\n    seed={_HASH_SEEDS[0]}: {baseline.get(path, '<absent>')!r}"
            f"\n    seed={seed}: {candidate.get(path, '<absent>')!r}"
            for path in moved[:5]
        )
        pytest.fail(
            f"{surface_id} is NOT deterministic across PYTHONHASHSEED {_HASH_SEEDS[0]} vs {seed} "
            f"-- {len(moved)} field(s) moved.\n"
            "An agent diffing this between runs sees phantom changes, and any fingerprint or "
            "baseline built on it will not match itself.\n"
            "Usual cause: emitted in set/dict iteration order; sort at the emit site. If the "
            "field is genuinely runtime state, add it to _VOLATILE_KEYS above WITH A REASON "
            "rather than widening this comparison.\n"
            f"{detail}"
        )


def test_every_always_present_volatile_key_is_live() -> None:
    """A stale allow-list entry is a hole nothing is watching.

    If `generated_at` were renamed, silently dropping the old name here would leave the gate
    normalising a field that no longer exists while the NEW one went unchecked -- the allow-list
    would look principled and protect nothing.

    Scoped to `_VOLATILE_ALWAYS` on purpose: `daemon_response_cache` is only emitted when the
    daemon path serves the call, so asserting its presence would be a flaky check on daemon
    lifecycle rather than a real guard. Enforcing the half that CAN be enforced beats an assertion
    loosened until it always passes.
    """
    seen: set[str] = set()
    for _surface_id, argv, _min_bytes in _SURFACES:
        try:
            payload = json.loads(_run(argv, _HASH_SEEDS[0]).text)
        except json.JSONDecodeError:
            continue
        seen |= _collect_keys(payload)

    for key in _VOLATILE_ALWAYS:
        assert key in seen, (
            f"declared volatile key {key!r} no longer appears in any surface's output. Either it "
            "was renamed (update _VOLATILE_KEYS, and check the new name is actually stable) or "
            "it became deterministic (delete the entry so the field is checked again)."
        )


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            keys.add(key)
            keys |= _collect_keys(inner)
    elif isinstance(value, list):
        for item in value:
            keys |= _collect_keys(item)
    return keys
