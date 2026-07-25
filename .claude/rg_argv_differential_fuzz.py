#!/usr/bin/env python3
"""Differential fuzz: `bootstrap._search_path_args_raw` vs an independent model of rg's grammar.

WHY THIS EXISTS
---------------
Task #269 (root-`.gitignore` honoring on the Python-only rg-passthrough paths) went through FOUR
rounds in which every round found an argv form nobody had enumerated by hand:

  1. `-u`/`-uu`/`--unrestricted` ungated  -> `-u` became STRICTER than no flag
  2. `-f`/`--file`/`-e<attached>` not treated as pattern sources -> wrong root's ignore file
  3. `-ieneedle` (mid-bundle attached)    -> same, introduced by round 2's own fix
  4. `PATH -eneedle` (positional BEFORE the flag) -> same, introduced by round 3's own fix

Hand-written enumerations kept missing a dimension. This harness replaces the enumeration with a
whole-grammar model: it parses argv the way rg 15.1.0 documents itself to (via `rg --help`'s own
flag tables), computes the PATH positionals rg would search, and compares that against what
`_search_path_args_raw` computes. Every disagreement is a candidate wrong-root -> wrong
`--ignore-file` injection -> silently wrong file set.

The reference model here is deliberately the ONLY grammar model in the repo. Do not write a
second one: two independently-written models disagree with each other and neither is
authoritative. If rg's grammar changes, update `SHORT_VALUE` / `LONG_VALUE` below from
`rg --help` and re-run.

USAGE
-----
    python .claude/rg_argv_differential_fuzz.py
    python .claude/rg_argv_differential_fuzz.py --seed 20260725 --iterations 60000
    python .claude/rg_argv_differential_fuzz.py --src path/to/src   # test another checkout

Exit code 0 = zero disagreements (the closing gate). Exit code 1 = disagreements, printed with
a minimal reproducer per distinct shape so a failure is actionable, not just a count.

The run is deterministic for a given `--seed`, so a reported failure reproduces exactly.

REFRESHING THE FLAG TABLES (do this when bumping the pinned rg)
--------------------------------------------------------------
    rg --help | grep -oE "^\\s+-[a-zA-Z] [A-Z<]"          # -> SHORT_VALUE
    rg --help | grep -oE "^\\s+(-[a-zA-Z], )?--[a-z0-9-]+=" # -> LONG_VALUE
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import random
import sys
from pathlib import Path

# --------------------------------------------------------------------------------------
# rg 15.1.0 grammar tables, taken from `rg --help`'s own output (see module docstring).
# --------------------------------------------------------------------------------------

#: Short flags that take a value. `rg --help` prints these as `-X ARG, --long=ARG`.
#: A value-taking short flag inside a cluster SWALLOWS the remainder of the token as its value
#: (`-ite` == `-i -t e`, verified live: `rg -ite needle .` -> "unrecognized file type: e"), or,
#: when it is the token's LAST character, consumes the NEXT argv token (`-ie needle`).
SHORT_VALUE = set("ABCEMdefgjmrtT")

#: Long flags that take a value. `rg --help` prints these with an `=ARG` suffix.
LONG_VALUE = {
    "--after-context",
    "--before-context",
    "--color",
    "--colors",
    "--context",
    "--context-separator",
    "--dfa-size-limit",
    "--encoding",
    "--engine",
    "--field-context-separator",
    "--field-match-separator",
    "--file",
    "--generate",
    "--glob",
    "--hostname-bin",
    "--hyperlink-format",
    "--iglob",
    "--ignore-file",
    "--max-columns",
    "--max-count",
    "--max-depth",
    "--max-filesize",
    "--path-separator",
    "--pre",
    "--pre-glob",
    "--regex-size-limit",
    "--regexp",
    "--replace",
    "--sort",
    "--sortr",
    "--threads",
    "--type",
    "--type-add",
    "--type-clear",
    "--type-not",
}

#: Flags that SUPPLY THE PATTERN. If any of these appears in the option region, rg does NOT
#: treat the first bare positional as the pattern -- every bare positional is a PATH. rg's
#: grammar is ORDER-INDEPENDENT here: `rg sub -eneedle` == `rg -eneedle sub` (verified live).
PATTERN_SOURCE_SHORT = set("ef")
PATTERN_SOURCE_LONG = {"--regexp", "--file"}


def rg_roots(argv: list[str]) -> list[str]:
    """Return the PATH positionals rg would search. Empty list == implicit cwd root."""
    positionals: list[str] = []
    pattern_from_flag = False
    end_of_opts = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if end_of_opts:
            positionals.append(arg)
            i += 1
            continue
        if arg == "--":
            end_of_opts = True
            i += 1
            continue
        if arg.startswith("--"):
            name, sep, _value = arg.partition("=")
            if name in PATTERN_SOURCE_LONG:
                pattern_from_flag = True
            if not sep and name in LONG_VALUE:
                i += 1  # value lives in the next argv token
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            cluster = arg[1:]
            for offset, char in enumerate(cluster):
                if char in SHORT_VALUE:
                    if char in PATTERN_SOURCE_SHORT:
                        pattern_from_flag = True
                    if offset == len(cluster) - 1:
                        i += 1  # value lives in the next argv token
                    break  # the remainder of the token is this flag's value
            i += 1
            continue
        positionals.append(arg)
        i += 1
    if pattern_from_flag:
        return positionals
    return positionals[1:]  # the first bare positional is the PATTERN


# --------------------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------------------

BOOL_SHORTS = list("inFvswxaqlcHINopzSPULb")
VALUES = ["5", "needle", "pats.txt", "*.py", "py", "utf-8", "-weird"]
PATTERNS = ["needle", "unwrap", "u", "g*.py"]
PATHS = [".", "otherdir", "sub", "--"]
CLUSTER_PREFIXES = ["", "i", "n", "iv", "vF", "zi", "s", "in", "wxa"]


def build_corpus(seed: int, iterations: int) -> list[list[str]]:
    """Systematic sweep (every prefix x value-flag x attach/separate x tail) + seeded fuzz."""
    cases: list[list[str]] = []
    for prefix, flag in itertools.product(CLUSTER_PREFIXES, sorted(SHORT_VALUE)):
        for value in ("needle", "5", "*.py", "py", "utf-8"):
            cases.append([f"-{prefix}{flag}{value}", "otherdir"])
            cases.append([f"-{prefix}{flag}", value, "otherdir"])
            cases.append([f"-{prefix}{flag}{value}"])
            cases.append([f"-{prefix}{flag}", value])
            cases.append([f"-{prefix}{flag}", value, "needle", "otherdir"])
            cases.append([f"-{prefix}{flag}", value, "needle", "--", "otherdir"])
            # ORDERING dimension (round 4): the PATH ahead of the pattern-source flag.
            cases.append(["otherdir", f"-{prefix}{flag}{value}"])
            cases.append(["otherdir", f"-{prefix}{flag}", value])

    rnd = random.Random(seed)
    alphabet = (
        [f"-{c}" for c in sorted(SHORT_VALUE) + BOOL_SHORTS]
        + [f"-{a}{b}" for a in "invFszwx" for b in sorted(SHORT_VALUE) + BOOL_SHORTS]
        + [f"-{a}{v}" for a in sorted(SHORT_VALUE) for v in VALUES]
        + [f"-{p}{a}{v}" for p in "inz" for a in sorted(SHORT_VALUE) for v in ("needle", "5", "py")]
        + sorted(LONG_VALUE)
        + [f"{k}={v}" for k in sorted(LONG_VALUE) for v in ("needle", "py", "5")]
        + ["--no-ignore", "--unrestricted", "--files", "--json", "--", "--hidden"]
        + PATTERNS
        + PATHS
        + VALUES
    )
    for _ in range(iterations):
        cases.append([rnd.choice(alphabet) for _ in range(rnd.randint(1, 6))])
    return cases


# --------------------------------------------------------------------------------------


def load_bootstrap(src: Path):
    sys.path.insert(0, str(src))
    spec = importlib.util.spec_from_file_location(
        "_fuzz_bootstrap", src / "tensor_grep" / "cli" / "bootstrap.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import bootstrap.py from {src}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shape_of(argv: list[str]) -> tuple[str, ...]:
    return tuple(
        "TOK" if not a.startswith("-") else ("LONG" if a.startswith("--") else a) for a in argv
    )


def main() -> int:
    default_src = Path(__file__).resolve().parent.parent / "src"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=default_src, help="path to the src/ tree")
    parser.add_argument("--seed", type=int, default=20260725, help="fuzz seed (reproducible)")
    parser.add_argument("--iterations", type=int, default=60000, help="random cases to generate")
    parser.add_argument("--max-report", type=int, default=25, help="distinct shapes to print")
    args = parser.parse_args()

    bootstrap = load_bootstrap(args.src)
    cases = build_corpus(args.seed, args.iterations)

    disagreements: list[tuple[list[str], list[str], list[str]]] = []
    for argv in cases:
        expected = rg_roots(argv)
        actual = bootstrap._search_path_args_raw(argv)
        if expected != actual:
            disagreements.append((argv, expected, actual))

    print(f"src        : {args.src}")
    print(f"seed       : {args.seed}")
    print(f"cases      : {len(cases)}")
    print(f"disagreements: {len(disagreements)}")

    if not disagreements:
        print("\nPASS -- _search_path_args_raw agrees with the rg grammar model on every case.")
        return 0

    by_shape: dict[tuple[str, ...], tuple[list[str], list[str], list[str]]] = {}
    for argv, expected, actual in disagreements:
        by_shape.setdefault(shape_of(argv), (argv, expected, actual))
    print(f"distinct shapes: {len(by_shape)}\n")
    # shortest reproducers first -- they are the ones worth reading
    ordered = sorted(by_shape.values(), key=lambda row: (len(row[0]), len("".join(row[0]))))
    for argv, expected, actual in ordered[: args.max_report]:
        print(f"  argv      : {argv}")
        print(f"    rg roots: {expected}")
        print(f"    tg roots: {actual}")
    if len(ordered) > args.max_report:
        print(f"  ... {len(ordered) - args.max_report} more distinct shapes")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
