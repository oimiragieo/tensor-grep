#!/usr/bin/env bash
# Run the CI-parity container against the LIVE working tree, under a CPU cap.
#
# Usage:
#   scripts/ci-local/run.sh                 # both lanes, default cap
#   scripts/ci-local/run.sh rust            # rust lane only
#   scripts/ci-local/run.sh python          # python lane only
#   scripts/ci-local/run.sh shell           # interactive shell in the CI image
#   TG_CI_CPUS=4 scripts/ci-local/run.sh    # override the cap
#
# THE CPU CAP IS THE POINT. This box is a SHARED SERVER; the standing ban on local `cargo` exists
# because cargo defaults to `-j <ncpu>` and takes every core. `--cpus` bounds the container's total
# CPU regardless of how many jobs cargo spawns, so the ban's purpose is preserved. Default is 4.
#
# The cap is a REAL but PARTIAL mitigation, and the limit is worth knowing: a cgroup CPU quota
# bounds CPU time only. It does NOT bound disk I/O, page-cache pressure, memory bandwidth, or
# Docker Desktop's own VM overhead. The worst case is a COLD run (empty cargo volumes): ~400 crates
# compiling and linking will still thrash host I/O and evict page cache for other users even at a
# low `--cpus`. Prefer warm runs, and raise the cap only when nobody else is on the machine.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="tensor-grep-ci-local:latest"
LANE="${1:-all}"
CPUS="${TG_CI_CPUS:-4}"
MEMORY="${TG_CI_MEMORY:-8g}"

echo "repo   : ${REPO_ROOT}"
echo "lane   : ${LANE}"
echo "cpus   : ${CPUS} (host has $(nproc 2>/dev/null || echo '?'))"
echo "memory : ${MEMORY}"

echo "=== build image ==="
docker build -t "${IMAGE}" "${REPO_ROOT}/scripts/ci-local"

echo "=== run ==="
# Named volumes for cargo's target dir and registry: without them every run recompiles ~400 crates
# from scratch (measured: the difference between minutes and ~15s on a warm cache). They live in
# docker, NOT in the repo, so the host tree stays byte-clean -- a container writing into
# rust_core/target/ would collide with anything the host is doing and dirty `git status`.
# MSYS_NO_PATHCONV=1: Git Bash rewrites leading-slash arguments into Windows paths when handing them
# to a native .exe. That mangled `-e CARGO_TARGET_DIR=/cargo-target` into
# `C:/Program Files/Git/cargo-target` and killed the lane with exit 101 before any test ran.
# CARGO_TARGET_DIR now lives in the image (see Dockerfile); this guard protects the -v destinations
# too. Harmless on non-MSYS shells, where the variable is simply unused.
# --tmpfs /work/.venv : SHADOWS the operator's host venv inside the container.
# rust_core/tests/test_routing.rs resolves its sidecar interpreter with a helper that returns
# `<repo>/.venv/Scripts/python.exe` whenever that file EXISTS -- and through the bind mount the
# host's Windows venv does exist, so a Linux container tries to exec a .exe and the routing tests
# fail. The test passes that path explicitly via `.env(...)`, so an ambient TG_SIDECAR_PYTHON
# cannot override it; the path itself must be correct. A tmpfs hides the host venv, lets the
# entrypoint build a real Linux venv at the expected location, and guarantees ZERO writes reach
# the operator's repo. It is RAM-backed and discarded per run, so the package reinstall (~2 min)
# happens every time -- the cost of not touching the host tree.
#
# `exec` is MANDATORY in those tmpfs options. Docker mounts tmpfs NOEXEC by default, so any native
# extension installed into this venv cannot be mapped and pytest dies at collection with
# `ImportError: .../hypothesis/_native...so: failed to map segment from shared object`
# (measured; python lane exit 3 = INTERNALERROR, i.e. the suite never ran, NOT a test failure).
# The rust lane does not hit this because it only execs the interpreter for wrapper scripts and
# never imports a native extension from the venv -- so a green rust lane does NOT prove the mount
# options are right for python. Size is 4g: a full [dev,ast] install plus native wheels.
MSYS_NO_PATHCONV=1 docker run --rm \
    --cpus="${CPUS}" \
    --memory="${MEMORY}" \
    -v "${REPO_ROOT}:/work" \
    --tmpfs "/work/.venv:uid=1001,gid=1001,size=4g,exec" \
    -v tg-ci-cargo-target:/work/rust_core/target \
    -v tg-ci-cargo-registry:/cargo-home/registry \
    -e CARGO_BUILD_JOBS="${CPUS}" \
    "${IMAGE}" "${LANE}"
