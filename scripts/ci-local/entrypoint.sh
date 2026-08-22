#!/usr/bin/env bash
# Lane runner for the local CI-parity container. One lane per argument so a caller can run only
# what it needs; `all` runs both and reports each independently.
#
# EXIT SEMANTICS (deliberate): each lane's exit code is captured and reported per lane, and the
# script exits non-zero if ANY lane failed. It does NOT pipe test output through `tail`/`head` --
# doing that reads the exit code of the PAGER, not the test, which is how a commit landed on RED
# in this repo on 2026-07-29.
set -uo pipefail

LANE="${1:-all}"
RUST_RC=0
PY_RC=0
RAN_RUST=0
RAN_PY=0

banner() { printf '\n=== %s ===\n' "$1"; }

setup_python_env() {
    # ci.yml creates a venv and prepends it to PATH BEFORE cargo test, because the PyO3 build links
    # against a python. Same order here.
    #
    # The venv MUST live at /work/.venv, because rust_core's test helper hardcodes that path (see
    # the block below). It is safe only because run.sh mounts a container-only tmpfs there, so the
    # operator's real Windows venv is hidden rather than modified and nothing is written to the
    # host repo. A venv written directly into a bind-mounted /work would be litter in `git status`,
    # and one created by an earlier ROOT container is root-owned -- the non-root user then dies
    # mid-install with `failed to create directory .../jsonschema-4.26.0.dist-info: Permission
    # denied` (both measured).
    export PATH="/work/.venv/bin:${PATH}"
    export VIRTUAL_ENV=/work/.venv

    # The HOST's Windows `.venv` is visible at /work/.venv through the bind mount, so inside a
    # LINUX container the sidecar resolves to a WINDOWS path:
    #   Python sidecar not found. Tried `/work/.venv/Scripts/python.exe`.
    # (measured: test_routing_explicit_gpu_device_ids_use_gpu_sidecar + _override_warm_index).
    #
    # An ambient TG_SIDECAR_PYTHON export CANNOT fix this. rust_core/tests/test_routing.rs sets the
    # variable EXPLICITLY per-command from its own helper:
    #     fn repo_python() { let windows = repo_root()/.venv/Scripts/python.exe;
    #                        if windows.exists() { return windows } ... }
    # so the test's value always wins over the environment. The path itself has to be right.
    #
    # run.sh therefore mounts a container-only tmpfs over /work/.venv, which HIDES the host's
    # Windows venv from the container without touching the operator's real one. We then build a
    # Linux venv at exactly the path the helper looks for.
    #
    # SCOPE OF THAT GUARANTEE, stated precisely: the operator's `.venv` is never written and never
    # even opened -- only occluded inside the container's mount namespace. It is NOT true that the
    # run writes nothing to the host: /work is bind-mounted read-write and pytest runs with
    # cwd=/work, so `__pycache__/`, `.pytest_cache/` and any non-tmp_path fixture output DO land in
    # the operator's tree. They are gitignored (hence a clean `git status`), but "zero writes" would
    # be false.
    if [ ! -x /work/.venv/bin/python ]; then
        echo "creating container-only Linux venv at /work/.venv (tmpfs; host venv is untouched)"
        if ! python3.12 -m venv /work/.venv; then
            # Checked explicitly: an unchecked failure here surfaces later as a confusing
            # "install failed" or "no module named" downstream, blaming the wrong step.
            echo "VENV CREATION FAILED at /work/.venv -- no lane can run." >&2
            return 1
        fi
    fi

    # DELIBERATELY NOT exporting TG_SIDECAR_PYTHON. Creating the venv above is sufficient --
    # `repo_python()` finds /work/.venv/bin/python once the tmpfs hides the host's Windows layout.
    #
    # An ambient export actively BREAKS a test: `test_missing_python_reports_actionable_error`
    # copies tg to an isolated dir, sets PATH="" and requires exit 2 with "Python sidecar not
    # found". It clears PATH but NOT TG_SIDECAR_PYTHON, so a global export hands it an interpreter
    # and it exits 0 (measured: `left: Some(0), right: Some(2)`). CI sets no such variable, so
    # exporting one here is a divergence that manufactures a false pass on a fail-closed test --
    # the worst direction for this suite to be wrong in.
}

# THE RUST LANE DELIBERATELY DOES NOT INSTALL THE PACKAGE -- ci.yml's test-rust-core uses a BARE
# venv, and so does this.
#
# An earlier version installed it, because
# `test_option_first_root_search_forwards_no_line_number_to_rg` died with
# `No module named tensor_grep`. That was a MISDIAGNOSIS. The real cause was an out-of-tree
# CARGO_TARGET_DIR breaking tg's own PYTHONPATH injection (see the Dockerfile note). With the
# target dir repo-relative the same test passes against a bare venv -- measured directly:
# `test result: ok. 1 passed; 0 failed ... 46 filtered out`.
#
# Keeping the install would have made this lane a SUPERSET of CI's rust job and masked any future
# regression in the PYTHONPATH-injection mechanism -- precisely the behaviour
# `test_tg_defs_help_injects_repo_src_into_pythonpath_for_passthrough` exists to guard. Do not add
# it back without re-measuring against a bare venv first.

run_rust() {
    RAN_RUST=1
    banner "RUST LANE (cargo test --verbose --no-default-features)"
    echo "toolchain: $(rustc --version)"
    echo "TG_REQUIRE_SYMLINK_TESTS=${TG_REQUIRE_SYMLINK_TESTS:-<unset>}"
    # `cd` in a subshell so a failure cannot leave the caller in rust_core.
    ( cd /work/rust_core && cargo test --verbose --no-default-features )
    RUST_RC=$?
    echo "rust lane exit: ${RUST_RC}"
}

run_python() {
    RAN_PY=1
    banner "PYTHON LANE (pytest tests -m 'not eval')"
    # `ast-grep-cli` is installed for THIS LANE ONLY, and the scoping is deliberate.
    #
    # WHY IT IS NEEDED HERE: tests/e2e/test_routing_parity.py skips only on a missing NATIVE BINARY
    # (`_skip_if_native_binary_missing`). CI's test-python never builds one, so its ruleset-scan
    # tests SKIP there. This harness shares the rust lane's cargo target volume, so the binary IS
    # present and those tests RUN -- and then fail without the ast-grep CLI, which ships in no
    # extra (`ast` and `dev` carry tree-sitter only). Measured: 1 failed -> 68 passed with it.
    #
    # WHY NOT IN THE IMAGE: an `ast-grep` binary on PATH CHANGES AST-backend availability, which
    # this repo has already been bitten by once (a CI-only AST failure whose real cause was the
    # CLI being present, not the Python package). The rust lane is green WITHOUT it and stays that
    # way; only the lane that needs it gets it.
    #
    # This makes the python lane a deliberate SUPERSET of CI: it runs tests CI skips. That is the
    # safe direction (more coverage, not less) but it is still a divergence -- a failure here may
    # not correspond to any CI failure. The GitHub run remains the arbiter.
    ( cd /work && uv pip install --python /work/.venv/bin/python -e ".[dev,ast]" ast-grep-cli )
    local install_rc=$?
    if [ "${install_rc}" -ne 0 ]; then
        echo "DEPENDENCY INSTALL FAILED (exit ${install_rc}) -- the suite never ran."
        echo "This is NOT a passing suite and NOT a test failure; it is a COULD-NOT-MEASURE."
        PY_RC="${install_rc}"
        return
    fi
    ( cd /work && python -m pytest tests -v --tb=short -m "not eval" )
    PY_RC=$?
    echo "python lane exit: ${PY_RC}"
}

setup_python_env || { echo "SETUP FAILED -- refusing to report any lane result." >&2; exit 2; }

case "${LANE}" in
    rust)   run_rust ;;
    python) run_python ;;
    all)    run_rust; run_python ;;
    shell)  exec /bin/bash ;;
    *)
        echo "unknown lane '${LANE}' (expected: rust | python | all | shell)" >&2
        exit 2
        ;;
esac

banner "SUMMARY"
[ "${RAN_RUST}" -eq 1 ] && echo "  rust  : exit ${RUST_RC}"
[ "${RAN_PY}" -eq 1 ]   && echo "  python: exit ${PY_RC}"

if [ "${RUST_RC}" -ne 0 ] || [ "${PY_RC}" -ne 0 ]; then
    echo "  VERDICT: FAILED"
    exit 1
fi
echo "  VERDICT: PASSED -- for the lanes below ONLY."
cat <<'NOT_COVERED'

  THIS RUN DID NOT EXECUTE (a green above says NOTHING about any of these):
    - windows-latest / macos-latest legs of test-python and test-rust-core
    - python 3.11 (this image is 3.12 only; CI matrixes 3.11 + 3.12)
    - the nightly Rust channel leg of test-rust-core
    - Formatting & Linting  (ruff check / ruff format --preview / cargo fmt / clippy)
    - docs-governance, repo-hygiene, release-readiness, release-intent
    - agent-readiness / windows-agent-readiness
    - native-build-smoke, cuda-feature-check, search-golden-parity, benchmark-regression
    - test-gpu-nvidia, the `-m eval` gate, and the whole release/publish chain
  The GitHub Actions run remains the merge arbiter. This harness is a fast local
  pre-filter, not a substitute for it.
NOT_COVERED
