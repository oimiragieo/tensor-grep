# Onboarding: local CI-parity harness (`scripts/ci-local/`)

## What this is, in one paragraph

This repo's shared-box rules (AGENTS.md / CLAUDE.md) forbid local `cargo build/test/check/clippy` and `tests/e2e/test_routing_parity.py` because this machine is a SHARED SERVER and those lanes saturate it. That ban left several work items unbuildable locally and pushed every Rust verification onto GitHub Actions, which costs money and minutes. `scripts/ci-local/` runs the ubuntu-shaped rust and python lanes inside a CPU-capped Docker container instead: the `--cpus` cap in `run.sh` bounds the work so the ban's purpose (do not saturate the shared box) is preserved while you still get a fast local pre-filter before you spend Actions minutes.

## Run it

From the repo root:

```bash
scripts/ci-local/run.sh                 # both lanes, default cap
scripts/ci-local/run.sh rust            # rust lane only
scripts/ci-local/run.sh python          # python lane only
TG_CI_CPUS=4 scripts/ci-local/run.sh    # override the cap
```

Default CPU cap is **4** (`TG_CI_CPUS` defaults to 4 in `run.sh`). Lower it when someone else needs the box (for example `TG_CI_CPUS=2`). The cap bounds container CPU time only; it does not bound disk I/O, page-cache pressure, memory bandwidth, or Docker Desktop VM overhead. Prefer warm runs (named cargo volumes already populated).

There is a fourth lane, `scripts/ci-local/run.sh shell`, which drops you into an interactive shell
inside the same image (`entrypoint.sh:139` — `shell) exec /bin/bash`). Use it when a lane fails and
you want to poke at the container the way CI would see it: check `id`, check volume ownership, try
the failing command by hand. It runs no tests and reports no verdict, so it can never tell you a
lane passed — it is a debugging surface only.

## What a green run does NOT mean

The GitHub Actions run remains the merge arbiter.

A green local verdict covers only the lanes that actually ran. From `entrypoint.sh`:

```
  THIS RUN DID NOT EXECUTE (a green above says NOTHING about any of these):
    - windows-latest / macos-latest legs of test-python and test-rust-core
    - python 3.11 (this image is 3.12 only; CI matrixes 3.11 + 3.12)
    - the nightly Rust channel leg of test-rust-core
    - Formatting & Linting  (ruff check / ruff format --preview / cargo fmt / clippy)
    - docs-governance, repo-hygiene, release-readiness, release-intent
    - agent-readiness / windows-agent-readiness
    - native-build-smoke, cuda-feature-check, search-golden-parity, benchmark-regression
    - test-gpu-nvidia, the `-m eval` gate, and the whole release/publish chain
  KNOWN LOCAL-ONLY FAILURE (not a product defect):
    Whole-repo, DEADLINE-BOUNDED tests can fail here purely on bind-mount I/O.
    /work is a Docker Desktop bind mount, which is materially slower than the
    native checkout a GitHub runner uses. Measured on this box:
      tests/unit/test_agent_capsule_hardcases.py
        ::test_agent_capsule_live_repo_prefers_exe_bridge_implementation_over_marker_helper
      -> exit_code=2, partial=True, partial_reason=deadline, elapsed 60.7s vs a 60s budget.
    The RANKING was correct in that run (primary_target = rust_core/src/python_sidecar.rs,
    exactly what the test asserts) -- only the clock lost. Treat a `partial_reason=deadline`
    failure here as an I/O artifact and confirm it on CI before believing it; do NOT widen
    the product deadline to make this harness pass.

  The GitHub Actions run remains the merge arbiter. This harness is a fast local
  pre-filter, not a substitute for it.
```

## The twelve divergences, and why each one matters

Each row was measured while getting this harness green. Every one produced a WRONG VERDICT (green when CI would be red, or red when CI is green), not a clear "instrument broken" message.

| # | Trap | Tell | Fix | Wrong verdict a junior would see |
|---|---|---|---|---|
| 1 | MSYS rewrites a leading-slash value passed to a native `.exe` | env value arrives as `C:/Program Files/Git/...`; `$LD_LIBRARY_PATH` join error | bake the ENV into the image; `MSYS_NO_PATHCONV=1` for mounts | Red locally (exit 101 before any test) while CI is green -- the lane never ran. |
| 2 | Distro splits a shared library into a `-dev` package | links fine on CI, `unable to find library -lpython3.12` locally | install `python3.12-dev`; CI gets it free from `actions/setup-python` | Red locally at the last link step while the same commit is green on CI. |
| 3 | **Running as root defeats permission-hostile fixtures** | a `chmod 000` test PASSES on CI and FAILS/behaves oddly in the container -- or worse, passes for the wrong reason | run non-root (`USER ci`, uid 1001); prove it with a `chmod 000` probe that must report DENIED | Red when CI green (root reads `chmod 000` dirs via CAP_DAC_OVERRIDE), or a false green if the fixture never bites. |
| 4 | **A failed `docker build` leaves the previous image tagged** | your fix "didn't work" -- because it was never built | check `BUILD_EXIT`; `docker inspect --format '{{.Config.User}}'` before trusting the run | You re-run against stale `:latest` bytes and conclude the fix failed when it never built. |
| 5 | Named volume created by an earlier ROOT run stays root-owned | `Permission denied` mid-install | `docker volume rm` and recreate | Red mid-`uv pip install` while CI installs cleanly as a non-root runner user. |
| 6 | A volume mount point ABSENT from the image is created root-owned | `failed to create directory .../registry/cache` | `mkdir -p` + `chown` that exact path in the Dockerfile | Red on cargo registry writes while CI never hits that ownership trap. |
| 7 | **`tmpfs` is `noexec` by default** | `failed to map segment from shared object` on any native wheel | add `exec` to the tmpfs options | Python lane dies at collection (suite never ran) while looking like a product ImportError; rust green does not prove the mount is right for python. |
| 8 | **git refuses a bind mount as dubious ownership** | pytest aborts at COLLECTION (`collected N items / 1 error`) -- zero tests run, exit code looks like a test failure | `git config --global --add safe.directory /work` | Red that looks like a failing test when zero tests executed; CI never sees this. |
| 9 | Host venv leaks through the bind mount | a Linux container resolves `.venv/Scripts/python.exe` | shadow it with a container-only tmpfs at that exact path | Red routing tests trying to exec a Windows `.exe` path inside Linux; CI has no host Windows venv. |
| 10 | **An out-of-tree `CARGO_TARGET_DIR` breaks path-relative product logic** | `PYTHONPATH=[]`; `resolve_repo_source_root_relative_to_exe` walks up FROM THE BINARY | keep the target dir repo-relative; mount a volume OVER it | One PYTHONPATH-injection test fails locally while CI (repo-relative `target/`) stays green. |
| 11 | **An ambient env var turns a fail-closed test green** | a test that must exit 2 exits 0 | export nothing the CI job does not export | Green on a fail-closed test that CI would fail -- the worst direction for a harness to be wrong. |
| 12 | A shared cargo-target volume makes a native binary visible to the PYTHON lane | tests CI SKIPS (`_skip_if_native_binary_missing`) suddenly RUN, then fail on a missing tool | expect it; scope any extra install to the lane that needs it | Red on tests CI skips (or false extra coverage); a failure here may not match any CI failure. |

## The anti-drift gate

`tests/unit/test_ci_local_harness_parity.py` exists because `scripts/ci-local/` is a SECOND definition of what CI runs. A second definition drifts; a drifted local harness is worse than none, because it reports GREEN for a command CI no longer runs.

What it pins: every value the harness MIRRORS from `.github/workflows/ci.yml` must appear verbatim in both places -- cargo invocation (`cargo test --verbose --no-default-features`), pytest invocation (`pytest tests -v --tb=short -m "not eval"`), pinned `uv==0.11.25`, `TG_REQUIRE_SYMLINK_TESTS`, and editable extras (`-e ".[dev,ast]"`). Change `ci.yml` and a test that NAMES this harness goes red.

Passing does NOT mean the harness matches CI -- only that the mirrored STRINGS still agree. The harness is deliberately not a full reproduction (ubuntu-only, no static-analysis lane, python-lane ast-grep-cli as a documented superset). The "DID NOT EXECUTE" banner is prose and is not pinned by this test.

Controls (without them the suite proves nothing):

- Positive control: `ci.yml` and the harness files must be present and non-trivially sized. Without this, every "needle in file" assertion would pass vacuously on an empty or missing file.
- Negative control: the substring matcher must be able to FAIL (`"cargo test --verbose --no-default-features" not in "cargo test"`). Without this, the parametrised agreement tests cannot discriminate a real miss.

Adding a mirrored value is a two-file edit: the harness file and `MIRRORED_VALUES`, in the same change.

## When to use act instead

`nektos/act` runs `.github/workflows/*.yml` verbatim, which structurally beats reimplementing CI. That is a real advantage. But `act` relocates the fidelity problem rather than removing it, and the primary sources say so:

- act's default images are "intentionally incomplete" -- "many things can work improperly or not at all", and Docker containers are not GitHub's fully-virtualized VMs (no `systemd`) (`nektosact.com/usage/runners.html`).
- The faithful images (`catthehacker/ubuntu:full-*`, a filesystem dump of the real runner) are ~20GB compressed / ~60GB extracted.
- Those images are barely maintained -- `nektos/act` issue #2055 is an open request to move off them.

| Reach for | When |
|---|---|
| **`act`** | You are debugging the WORKFLOW FILE itself -- job graph, `needs:`, `if:` conditions, path filters. Replaying the real YAML is the whole point. |
| **`scripts/ci-local/`** | You are debugging the CODE the lanes run, want a warm cargo cache, and need a bounded CPU footprint on a shared box. |
| **Neither -- push to CI** | Anything OS-specific. Neither tool runs windows-latest or macos-latest. |

Whichever you use, the twelve divergences above still apply -- every entry was measured in a container, and most are properties of containers, not of this particular harness.

## If it breaks: first five things to check

1. Verify the image actually rebuilt: `docker inspect --format '{{.Config.User}}'` on `tensor-grep-ci-local:latest` -- expect non-root (`ci`). A failed build leaves the previous `:latest` tagged.
2. Confirm you are non-root inside the container (`USER ci`, uid 1001). Root defeats permission-hostile fixtures.
3. Check volume ownership: if an earlier root run created named volumes, `Permission denied` mid-install means `docker volume rm` and recreate (`tg-ci-cargo-target`, `tg-ci-cargo-registry`).
4. Check tmpfs `exec`: `/work/.venv` must be mounted with `exec` in the options. Default `noexec` yields `failed to map segment from shared object` and the python suite never runs.
5. Check `git config --global --add safe.directory /work` (baked into the image). Without it, pytest aborts at collection on dubious ownership of the bind-mounted `/work`.
