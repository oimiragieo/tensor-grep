---
name: tensor-grep-local-ci-parity-harness
description: Use when you need to run a CI lane locally that this repo's shared-box rules forbid (cargo test, tests/e2e/test_routing_parity.py, a full pytest matrix), when building or debugging scripts/ci-local/, when a local container run and the GitHub run disagree, or when deciding between nektos/act and a hand-written harness. Covers the twelve measured container-vs-runner divergences, the CPU-cap discipline that makes local lanes acceptable on a shared machine, and the anti-drift gate that keeps a second CI definition honest. DO NOT USE for diagnosing a genuinely red CI job (tensor-grep-debugging-playbook), for deciding what counts as proof a change works (tensor-grep-validation-and-qa), or for release/publish mechanics (tensor-grep-release-and-positioning).
---

# tensor-grep: local CI-parity harness

`AGENTS.md`/`CLAUDE.md` forbid local `cargo build/test/check/clippy` and
`tests/e2e/test_routing_parity.py` because this dev box is a **SHARED SERVER**. That ban left F5,
F8 and Task 2C — and therefore MCP-SURFACE — unbuildable locally and pushed every Rust
verification onto GitHub Actions, which costs money and minutes.

`scripts/ci-local/` runs those lanes in a **CPU-capped container** instead. This skill is how to
use it, why each piece is shaped the way it is, and the ways a local harness lies.

## Hard rules

| Do | Never |
|---|---|
| Keep the `--cpus` cap; it is what preserves the ban's PURPOSE | Remove the cap "just this once" on a shared box |
| Treat the GitHub run as the merge arbiter | Merge on a green local lane |
| Verify the IMAGE before trusting a run (`docker inspect --format '{{.Config.User}}'`) | Assume `docker build` succeeded because the run started |
| Say which lanes did NOT execute, every time | Report "CI passes locally" |
| Keep the harness's mirrored strings pinned by a test | Let a second CI definition drift silently |

## 1. Run it

```bash
scripts/ci-local/run.sh            # both lanes
scripts/ci-local/run.sh rust       # cargo test only
scripts/ci-local/run.sh python     # pytest only
TG_CI_CPUS=2 scripts/ci-local/run.sh   # lower the cap while someone else needs the box
```

Default cap is **4 of 16 cores**. The cap is a REAL but PARTIAL mitigation: a cgroup CPU quota
bounds CPU time only — **not** disk I/O, page-cache pressure, memory bandwidth, or Docker
Desktop's VM overhead. The worst case is a COLD run (empty cargo volumes): ~400 crates compiling
will still thrash host I/O at any `--cpus`. Prefer warm runs.

## 2. `act` vs a hand-written harness — read this before "improving" it

`nektos/act` runs `.github/workflows/*.yml` **verbatim**, which structurally beats reimplementing
CI. That is a real advantage and it is why this section exists. But `act` **relocates** the
fidelity problem rather than removing it, and the primary sources say so:

- act's default images are **"intentionally incomplete"** — "many things can work improperly or
  not at all", and Docker containers are not GitHub's fully-virtualized VMs (no `systemd`)
  (`nektosact.com/usage/runners.html`).
- The faithful images (`catthehacker/ubuntu:full-*`, a filesystem dump of the real runner) are
  **~20GB compressed / ~60GB extracted**.
- Those images are **barely maintained** — `nektos/act` issue #2055 is an open request to move off
  them.

| Reach for | When |
|---|---|
| **`act`** | You are debugging the WORKFLOW FILE itself — job graph, `needs:`, `if:` conditions, path filters. Replaying the real YAML is the whole point. |
| **`scripts/ci-local/`** | You are debugging the CODE the lanes run, want a warm cargo cache, and need a bounded CPU footprint on a shared box. |
| **Neither — push to CI** | Anything OS-specific. Neither tool runs windows-latest or macos-latest. |

Whichever you use, the divergence catalogue in §3 still applies — every entry was measured in a
container, and most are properties of containers, not of this particular harness.

## 3. The twelve measured divergences (a container-vs-runner checklist)

Each was hit while getting this harness green. Phrased as the general trap, with the general tell.

| # | Trap | Tell | Fix |
|---|---|---|---|
| 1 | MSYS rewrites a leading-slash value passed to a native `.exe` | env value arrives as `C:/Program Files/Git/...`; `$LD_LIBRARY_PATH` join error | bake the ENV into the image; `MSYS_NO_PATHCONV=1` for mounts |
| 2 | Distro splits a shared library into a `-dev` package | links fine on CI, `unable to find library -lpython3.12` locally | install `python3.12-dev`; CI gets it free from `actions/setup-python` |
| 3 | **Running as root defeats permission-hostile fixtures** | a `chmod 000` test PASSES on CI and FAILS/behaves oddly in the container — or worse, passes for the wrong reason | run non-root (`USER ci`, uid 1001); prove it with a `chmod 000` probe that must report DENIED |
| 4 | **A failed `docker build` leaves the previous image tagged** | your fix "didn't work" — because it was never built | check `BUILD_EXIT`; `docker inspect --format '{{.Config.User}}'` before trusting the run |
| 5 | Named volume created by an earlier ROOT run stays root-owned | `Permission denied` mid-install | `docker volume rm` and recreate |
| 6 | A volume mount point ABSENT from the image is created root-owned | `failed to create directory .../registry/cache` | `mkdir -p` + `chown` that exact path in the Dockerfile |
| 7 | **`tmpfs` is `noexec` by default** | `failed to map segment from shared object` on any native wheel | add `exec` to the tmpfs options |
| 8 | **git refuses a bind mount as dubious ownership** | pytest aborts at COLLECTION (`collected N items / 1 error`) — zero tests run, exit code looks like a test failure | `git config --global --add safe.directory /work` |
| 9 | Host venv leaks through the bind mount | a Linux container resolves `.venv/Scripts/python.exe` | shadow it with a container-only tmpfs at that exact path |
| 10 | **An out-of-tree `CARGO_TARGET_DIR` breaks path-relative product logic** | `PYTHONPATH=[]`; `resolve_repo_source_root_relative_to_exe` walks up FROM THE BINARY | keep the target dir repo-relative; mount a volume OVER it |
| 11 | **An ambient env var turns a fail-closed test green** | a test that must exit 2 exits 0 | export nothing the CI job does not export |
| 12 | A shared cargo-target volume makes a native binary visible to the PYTHON lane | tests CI SKIPS (`_skip_if_native_binary_missing`) suddenly RUN, then fail on a missing tool | expect it; scope any extra install to the lane that needs it |

**The meta-lesson:** every one of these produced a WRONG VERDICT, not an error message —
green-when-CI-would-be-red, or red-when-CI-is-green. A local harness is an instrument, and
[[tensor-grep-validation-and-qa]]'s rule applies to it: *what would this show if the thing it
verifies were broken?*

## 4. Known local-only failures (do NOT "fix" the product for these)

- **Deadline-bounded whole-repo tests.** `/work` is a bind mount and is materially slower than a
  runner's native disk. Measured: `test_agent_capsule_live_repo_prefers_exe_bridge_implementation_over_marker_helper`
  → `exit_code=2, partial_reason=deadline, elapsed 60.7s` against a 60s budget, while the RANKING
  was correct (`primary_target = rust_core/src/python_sidecar.rs`, exactly what the test asserts).
  Confirm on CI before believing it. **Never widen a product deadline to make this harness pass** —
  that was tried on a sibling lane on 2026-07-27, bought 4× the wasted wall-clock, and was
  reverted the same day.

## 5. The anti-drift gate (why a second CI definition is allowed to exist)

`tests/unit/test_ci_local_harness_parity.py` pins every value the harness MIRRORS from `ci.yml` —
the cargo invocation, the pytest invocation, `uv==0.11.25`, `TG_REQUIRE_SYMLINK_TESTS`, the
editable extras — asserting each appears in BOTH files. Change `ci.yml` and a test that NAMES this
harness goes red.

It carries a positive control (both sources present and non-trivial) and a negative control (the
matcher must be able to fail), and was perturbation-proved: changing the cargo flags fails exactly
`[cargo test invocation]`; reverting returns 9 passed.

**Adding a mirrored value is a two-file edit** — the harness and `MIRRORED_VALUES` — in the same
commit. Passing does NOT mean the harness matches CI; it means the mirrored STRINGS still agree.

## Related

- `tensor-grep-validation-and-qa` — what counts as proof; this harness is one more instrument to distrust
- `tensor-grep-hermetic-hostile-tests` — why fixture #3 must BITE, and the CI-vs-desktop engine table
- `tensor-grep-debugging-playbook` — for a genuinely red CI job, not a local/CI disagreement
- `tensor-grep-build-and-env` — the Windows/WSL/venv path domains this harness sits on top of
