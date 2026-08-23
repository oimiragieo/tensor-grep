# Local CI-parity harness: runs the lanes this shared dev box forbids.
#
# WHY THIS EXISTS
# ---------------
# AGENTS.md/CLAUDE.md ban `cargo build/test/check/clippy` and `tests/e2e/test_routing_parity.py`
# on this machine, because it is a SHARED SERVER and those lanes saturate it. That ban left F5
# (Task 8 steps 3-5), F8 (Tasks 12-13) and Task 2C -- and therefore MCP-SURFACE -- unbuildable
# locally, and pushed every Rust verification onto GitHub runners, which costs money and minutes.
#
# A container does not remove CPU cost by itself. What makes this safe is the RUN-TIME CPU CAP in
# `run.sh` (`--cpus`), which bounds the work to a fraction of the box instead of letting cargo's
# default `-j <ncpu>` take all 16 cores. The ban's PURPOSE (do not saturate the shared box) is
# honoured by the cap; the ban's LETTER (never run cargo here) is superseded by it, deliberately
# and with the operator's explicit instruction.
#
# PARITY WITH ci.yml -- every value below is copied from a named source, not guessed:
#   * Rust toolchain      : rust_core/rust-toolchain.toml pins channel 1.96.0 + rustfmt/clippy.
#                           cargo auto-applies it, so we install exactly that channel.
#   * Cargo command       : `cargo test --verbose --no-default-features` (ci.yml test-rust-core).
#   * TG_REQUIRE_SYMLINK_TESTS=1 : ci.yml test-rust-core job env. Without it the symlink tests
#                           SKIP, and a skipped test is not a passing test.
#   * PyO3 linker venv    : ci.yml creates a venv and puts it on PATH before `cargo test`, because
#                           the PyO3 build needs a python to link against. Mirrored below.
#   * Python install      : `uv pip install -e ".[dev,ast]"` (ci.yml test-python).
#   * Pytest command      : `pytest tests -v --tb=short -m "not eval"` (ci.yml test-python).
#   * uv version          : pinned 0.11.25, matching ci.yml. An unpinned tool install is how this
#                           repo reddened main once already (a same-day ruff release).
#
# This mirrors the ubuntu-latest lane only. It does NOT reproduce windows-latest or macos-latest,
# and it therefore CANNOT clear a Windows-only or macOS-only failure -- several real defects this
# repo has hit were Windows-specific. Treat a green run here as "the Linux lane would pass",
# never as "CI is green".
FROM ubuntu:24.04

# CARGO_TARGET_DIR is set HERE, not passed with `docker run -e`, because Git Bash (MSYS) rewrites a
# leading-slash value on the command line into a Windows path: `-e CARGO_TARGET_DIR=/cargo-target`
# arrived inside the container as `C:/Program Files/Git/cargo-target`, which then produced
# `error: failed to join paths from $LD_LIBRARY_PATH ... path segment contains separator ':'`
# and exit 101 before a single test ran. An ENV baked into the image never crosses the MSYS
# boundary, so it cannot be mangled. (Same class as the `/tmp` vs `C:\...\Temp` split in CLAUDE.md.)
ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_TERM_COLOR=never \
    PYTHONUNBUFFERED=1 \
    TG_REQUIRE_SYMLINK_TESTS=1

# ripgrep is a runtime dependency of the search backend; git is needed by tests that shell out to
# it; build-essential + pkg-config are the PyO3/cdylib link toolchain.
#
# `python3.12-dev` is REQUIRED and is easy to miss: it ships `libpython3.12.so`, which the PyO3
# link step needs. Without it the whole crate compiles and then dies at the very last step with
# `rust-lld: error: unable to find library -lpython3.12` (exit 101). CI does not need this line
# because `actions/setup-python` installs a python that already carries the shared library --
# a real difference between this harness and the GitHub runner, and exactly the kind of gap that
# makes a local harness disagree with CI.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        pkg-config \
        python3.12 \
        python3.12-dev \
        python3.12-venv \
        python3-pip \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Pinned toolchain. `--profile minimal` matches ci.yml; rust-toolchain.toml adds the components.
# Installed to a SHARED prefix, not /root/.cargo: the image drops to a non-root user below, and a
# toolchain living under /root is unreachable at 0700.
ARG RUST_CHANNEL=1.96.0
ENV RUSTUP_HOME=/usr/local/rustup
RUN CARGO_HOME=/usr/local/cargo curl --proto '=https' --tlsv1.2 --retry 10 --retry-connrefused \
        --location --silent --show-error --fail https://sh.rustup.rs -o /tmp/rustup-init.sh \
    && CARGO_HOME=/usr/local/cargo sh /tmp/rustup-init.sh \
        --default-toolchain "${RUST_CHANNEL}" --profile minimal -y --no-modify-path \
    && CARGO_HOME=/usr/local/cargo /usr/local/cargo/bin/rustup component add rustfmt clippy \
    && chmod -R a+rX /usr/local/rustup /usr/local/cargo \
    && rm -f /tmp/rustup-init.sh
ENV PATH="/usr/local/cargo/bin:${PATH}"

RUN python3.12 -m pip install --break-system-packages uv==0.11.25

# RUN AS NON-ROOT. This is not hygiene, it is CORRECTNESS for this suite.
#
# `backend_cpu::tests_h1::unreadable_walk_entry_is_an_error_not_a_silent_empty_match` chmod-000s a
# directory and requires the walk to surface an Err. Root has CAP_DAC_OVERRIDE, so it reads the
# directory anyway, the walk succeeds, and the test fails -- measured: `188 passed; 1 failed` in
# this container while the same commit is green on CI. The hostile fixture DOES NOT BITE as root.
#
# GitHub runners execute as a non-root user, so running as root here makes the harness disagree
# with CI on exactly the tests that assert a permission denial. That is the worst possible
# disagreement: a permission-hostile test is one of the few things this suite proves about
# fail-closed behaviour.
#
# (Mirror image of the documented `/root is 0700` trap: there a probe read DENIED for everything
# and proved nothing; here a probe reads ALLOWED for everything and proves nothing.)
ARG CI_UID=1001
# Every path that will later receive a NAMED VOLUME must exist here and be owned by `ci`.
# Docker seeds an empty named volume from the image's directory at that mount point -- including
# its ownership. A mount point that does NOT exist in the image gets a fresh root-owned directory,
# and the non-root process then dies with
# `failed to create directory /cargo-home/registry/cache/...`. That is why `registry` is created
# explicitly and not left implicit: /cargo-home existing is not enough.
# NOTE ON CARGO_TARGET_DIR -- do not "optimise" this back to an out-of-tree path.
# An earlier version set CARGO_TARGET_DIR=/cargo-target. It built fine and broke exactly one test:
# `test_tg_defs_help_injects_repo_src_into_pythonpath_for_passthrough`. Cause is
# `resolve_repo_source_root_relative_to_exe` in rust_core/src/python_sidecar.rs, which walks UP
# FROM THE BINARY looking for `<base>/src/tensor_grep`. With the binary at /cargo-target/debug/tg
# the ancestors are only /cargo-target and /, so it can never reach /work/src and PYTHONPATH is
# left empty (measured directly: `PYTHONPATH=[]`). CI keeps the binary at
# <repo>/rust_core/target/debug/tg, where the walk succeeds. The target dir must therefore stay
# repo-relative; the build cache is preserved by mounting a named VOLUME over
# /work/rust_core/target instead, which keeps artifacts off the host disk without moving the path.
RUN useradd --create-home --uid "${CI_UID}" ci \
    && mkdir -p /work/rust_core/target /cargo-home/registry \
    && chown -R ci:ci /work /cargo-home
ENV CARGO_HOME=/cargo-home
USER ci

# The bind-mounted repo is owned by the HOST user, not by uid 1001, so git refuses it:
#   fatal: detected dubious ownership in repository at '/work'
# That is not cosmetic. `scripts/cost_split_floor_routes.py` resolves ROOT at IMPORT time via
# `git rev-parse --show-toplevel` with check=True, so the refusal raises CalledProcessError while
# pytest is COLLECTING and aborts the whole run: `collected 1137 items / 1 error` -- zero tests
# execute. CI never sees this because the runner owns its checkout.
# This is git's own prescribed remedy, scoped to the single container-only path.
RUN git config --global --add safe.directory /work

WORKDIR /work

# Source is BIND-MOUNTED at run time, not COPYed: a copy silently tests a stale snapshot, which is
# the same class of defect as dogfooding a stale wheel. run.sh mounts the live tree read-write so
# cargo/pytest artifacts land in a container-only target dir (see run.sh's --env CARGO_TARGET_DIR).

# `COPY --chmod`, NOT `COPY` + `RUN chmod`: this stage runs as the non-root `ci` user, which cannot
# chmod a root-owned file. `RUN chmod` here fails the BUILD with
# `chmod: changing permissions of '/usr/local/bin/entrypoint.sh': Operation not permitted` -- and a
# failed build leaves the PREVIOUS image still tagged `:latest`, so the next `docker run` silently
# executes stale bytes. That cost a wrong conclusion: a lane was re-run against the old root image
# and the non-root fix was recorded as "didn't work" when it had never been built.
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["all"]
