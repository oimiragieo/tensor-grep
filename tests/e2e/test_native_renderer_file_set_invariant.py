"""Regression guard: a RENDERER flag must never change WHICH FILES are searched (task #270).

THE INVARIANT
-------------
    files(A) == files(A + R)   for any argv A and any renderer R in {--json, --ndjson}

`--json`/`--ndjson` select an OUTPUT FORMAT. They must not select a FILE SET.

WHY THIS GUARD EXISTS -- and why it is a guard, not a bug fix
------------------------------------------------------------
The native-delegation gate keys on OUTPUT-FORMAT flags: `bootstrap.py` admits a search to the
native engine when argv contains any of `--cpu`/`--force-cpu`/`--json`/`--ndjson`/`--gpu-device-ids`,
and `main.py`'s config-form mirror is `config.force_cpu or config.json_mode or ndjson or
bool(config.gpu_device_ids)`. Two of those five are renderers. So *whenever the two engines
behave differently, that difference is reachable only by asking for a different output format* --
and it presents as "JSON returns fewer files", which is what tasks #264, #267, #269 and #272 all
were, wearing three faces.

Those instances are fixed and the engines have converged: measured on the PUBLISHED
`tensor-grep==1.98.8` wheel, the invariant holds in every cell of a git-repo x non-git x
{plain, --json, --ndjson} matrix, including nested `.gitignore`. **This suite is therefore a
ratchet, not a repair.** It pins the property so the next engine drift fails loudly here instead
of silently changing an agent's answers.

The residual tg-vs-`rg` difference in a NON-git directory (tg honours a root `.gitignore`, rg
deliberately does not because of its `require_git(true)`) is the intentional #127 behaviour and is
NOT asserted here -- this suite compares tg against ITSELF across renderers, which is the exact
control arm that found the real bug in #264 after an rg-vs-tg comparison conflated engine and
format. **Vary one thing at a time: the renderer.**

CONTROL ARM -- why a green run here is evidence rather than an absence of evidence
---------------------------------------------------------------------------------
An equality assertion that currently holds passes trivially, so on its own it proves nothing (the
repo's own rule: *a check that passes in both arms is not verification*). Two things discriminate:

1. `test_the_comparison_itself_discriminates` drives the same set-comparison over SYNTHETIC
   inputs, proving it reports a divergence when one exists rather than always returning equal.
2. The historical arm is real and documented: on the pre-#742/#745/#746 code these very shapes
   DID diverge. Reproduced during triage against a build predating those fixes -- a non-git
   fixture returned 2 files plain and 1 file under `--json`.

A note on that triage, kept because it cost real time: the divergence was first "confirmed" using
a binary on PATH reporting version 1.98.3 that turned out to be a DEV BUILD -- it accepted a
`--verbose` flag the published wheel rejects outright, and reported a different `routing_reason`
vocabulary. **A locally-installed binary is not the shipped artifact.** This suite resolves the
binary through `resolve_native_tg_binary()` (the repo's own build output) precisely so it can
never be measuring some other tool.

Named `test_native_*` deliberately: `ci.yml`'s `native-build-smoke` job runs
`tests/e2e/test_native_*.py` with `TG_REQUIRE_RG_PARITY=1`, so this suite is picked up
automatically, and `tests/unit/test_native_e2e_ci_coverage_contract.py` (task #275) asserts that
coverage cannot silently lapse.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    spec = importlib.util.find_spec("helpers.rg_parity")
    assert spec is not None, "tests/helpers/rg_parity.py must be importable"
    return importlib.import_module("helpers.rg_parity")


def _require_native_tg():
    """Resolve the native `tg`, or SKIP -- LOUDLY when the caller demanded coverage.

    Mirrors `test_native_plain_text_parity.py::_require_binaries`. A silent skip is how a gate
    stops gating: CI sets `TG_REQUIRE_RG_PARITY=1` on the job that actually builds the binary, so
    a runner without one cannot masquerade as passing coverage.
    """
    helpers = _helpers()
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        message = "renderer-invariant guard needs the native tg binary (cargo build --release in rust_core/)"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)
    return tg_binary


def _build_fixture(root: Path, *, as_git_repo: bool) -> None:
    """Root + nested ignore files, matched and unmatched payloads.

    `.git` is created as a bare marker directory -- that is sufficient for the `ignore` crate's
    repo detection, which is what flips its native gitignore machinery on.
    """
    if as_git_repo:
        (root / ".git").mkdir()
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (root / "keep.txt").write_text("sentinel\n", encoding="utf-8")
    (root / "root.log").write_text("sentinel\n", encoding="utf-8")
    nested = root / "pkg" / "sub"
    nested.mkdir(parents=True)
    (nested / ".gitignore").write_text("*.dat\n", encoding="utf-8")
    (nested / "nested.dat").write_text("sentinel\n", encoding="utf-8")
    (nested / "ok.txt").write_text("sentinel\n", encoding="utf-8")


def _norm(path_str: str) -> str:
    """Compare on a canonical relative form -- separators and a leading ./ are presentation."""
    return path_str.replace("\\", "/").removeprefix("./").strip()


def _files_plain(tg: Path, root: Path) -> set[str]:
    proc = subprocess.run(
        [str(tg), "search", "-l", "sentinel", "."],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    return {_norm(line) for line in proc.stdout.splitlines() if line.strip()}


def _files_json(tg: Path, root: Path) -> set[str]:
    proc = subprocess.run(
        [str(tg), "search", "--json", "sentinel", "."],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    payload = json.loads(proc.stdout)
    return {_norm(p) for p in (payload.get("matched_file_paths") or [])}


def _files_ndjson(tg: Path, root: Path) -> set[str]:
    proc = subprocess.run(
        [str(tg), "search", "--ndjson", "sentinel", "."],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = obj.get("file") or obj.get("path")
        if path:
            found.add(_norm(path))
    return found


@pytest.mark.parametrize("as_git_repo", [True, False], ids=["git-repo", "non-git"])
def test_renderer_flag_does_not_change_the_file_set(tmp_path: Path, as_git_repo: bool) -> None:
    """THE INVARIANT. Both topologies, because the `ignore` crate behaves differently in each.

    Non-git is not redundant: `require_git(true)` leaves the crate's native gitignore machinery
    dormant there, so a different code path decides the file set. A fix verified only in one
    topology is a fix verified in the topology where its mechanism happens to apply -- that exact
    trap cost a round on PR #750.
    """
    tg = _require_native_tg()
    root = tmp_path / ("gitrepo" if as_git_repo else "nongit")
    root.mkdir()
    _build_fixture(root, as_git_repo=as_git_repo)

    plain = _files_plain(tg, root)
    assert plain, "precondition: the plain search must find something, or the guard is vacuous"

    for renderer, files in (
        ("--json", _files_json(tg, root)),
        ("--ndjson", _files_ndjson(tg, root)),
    ):
        assert files == plain, (
            f"RENDERER CHANGED THE FILE SET ({'git repo' if as_git_repo else 'non-git'}). "
            f"plain={sorted(plain)} vs {renderer}={sorted(files)}. "
            "A renderer flag selects an OUTPUT FORMAT, never a FILE SET. The native-delegation "
            "gate admits searches to the native engine on --json/--ndjson, so any engine "
            "behaviour difference surfaces here first. See task #270."
        )


def test_nested_ignore_files_are_honoured_identically_by_every_renderer(tmp_path: Path) -> None:
    """Guards the SUBSTANCE, so the invariant above cannot be satisfied by returning nothing.

    An equality assertion is satisfiable by three empty sets. This pins that ignore semantics are
    actually applied: the root `.gitignore` excludes `root.log` and the nested one excludes
    `nested.dat`, in a git repo where the crate's own machinery is live.
    """
    tg = _require_native_tg()
    root = tmp_path / "gitrepo"
    root.mkdir()
    _build_fixture(root, as_git_repo=True)

    plain = _files_plain(tg, root)

    assert "keep.txt" in plain
    assert "pkg/sub/ok.txt" in plain, "nested non-ignored file must be found"
    assert "root.log" not in plain, "root .gitignore must exclude *.log"
    assert "pkg/sub/nested.dat" not in plain, "nested .gitignore must exclude *.dat"


@pytest.mark.parametrize(
    ("plain", "rendered", "should_match"),
    [
        ({"a.txt", "b.txt"}, {"a.txt", "b.txt"}, True),
        ({"a.txt", "b.txt"}, {"a.txt"}, False),
        ({"a.txt"}, {"a.txt", "b.txt"}, False),
        (set(), set(), True),
    ],
    ids=["identical", "renderer-drops-a-file", "renderer-adds-a-file", "both-empty"],
)
def test_the_comparison_itself_discriminates(
    plain: set[str], rendered: set[str], should_match: bool
) -> None:
    """Proves the set comparison REPORTS a divergence, using synthetic inputs.

    Without this, the invariant test passing tells you nothing while the engines agree -- and they
    agree today. `renderer-drops-a-file` is the historical shape (#264/#267/#269: JSON searched
    FEWER files); `renderer-adds-a-file` is its mirror. `both-empty` is included deliberately to
    document that the equality alone would accept a vacuous result, which is why
    `test_renderer_flag_does_not_change_the_file_set` asserts `plain` is non-empty first.
    """
    assert (plain == rendered) is should_match
