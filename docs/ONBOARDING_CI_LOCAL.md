# Local CI harness

`scripts/ci-local/` runs selected Linux Rust and Python checks in a CPU-capped
Docker container. It is a development pre-check, not a replacement for the hosted
workflow or proof that another operating system passes.

## Run it

From the repository root:

```bash
scripts/ci-local/run.sh
scripts/ci-local/run.sh rust
scripts/ci-local/run.sh python
TG_CI_CPUS=2 scripts/ci-local/run.sh
scripts/ci-local/run.sh shell
```

The default CPU cap is 4. Select a cap appropriate to the host and other workloads.
CPU limits do not bound memory, disk I/O, or Docker VM overhead. The `shell` lane
is for debugging and reports no test verdict. Named Cargo volumes support cache
reuse across runs.

## Coverage boundary

A local pass covers only commands that actually ran. Read the harness's
`THIS RUN DID NOT EXECUTE` banner and compare the command list with
`.github/workflows/ci.yml`. Windows, macOS, other Python versions, release jobs,
and separate lint, audit, readiness, and benchmark gates require their own checks.

`tests/unit/test_ci_local_harness_parity.py` pins mirrored workflow values such as
Cargo and pytest invocations, dependency setup, and symlink-test configuration.
These checks detect selected configuration drift; matching strings do not prove
that the container reproduces a hosted runner's environment.

## Container pitfalls

- Run permission tests as the intended non-root user. Root can bypass the refusal
  that a test is intended to exercise.
- Check volume ownership after an image or user change. Preserve useful cached
  data before recreating a volume.
- Confirm executable mappings are allowed where the environment and extensions
  are installed; `noexec` can prevent collection from reaching a test.
- Compare environment variables with the hosted job. An extra override can change
  fail-closed routing and make a local result misleading.
- Install tools for the lane that needs them. A cached native binary or extra
  `sg` installation can cause tests to run that a different environment skips.
- On Git Bash/MSYS, path conversion can rewrite arguments passed to native
  executables. Apply `MSYS_NO_PATHCONV=1` only where needed for container mounts.
- Confirm an image rebuild succeeded before interpreting an old `:latest` image
  as the new configuration.

To debug workflow scheduling and job conditions, a workflow runner such as `act`
may be useful, but its container image and platform coverage also differ from
GitHub-hosted virtual machines. Record the actual runner and tool versions with
every result; only the exact hosted run proves the hosted gate.
